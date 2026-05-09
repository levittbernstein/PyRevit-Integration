# -*- coding: utf-8 -*-
"""
LB Issue Register Exporter — pyRevit push-button script.

Runs under IronPython (pyRevit default engine).
Excel building and PDF export run in a separate CPython subprocess
so openpyxl and win32com never load inside Revit's process.
"""

import sys
import os
import json
import traceback
import tempfile
import subprocess

from pyrevit import forms, revit
from Autodesk.Revit.DB import Transaction

# ── Add tool lib folder to path ───────────────────────────────────────────────
_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB  = os.path.join(_EXT_ROOT, 'lib', 'issue_register')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from revit_reader import get_sheets_data, get_project_info, collect_issue_dates
from storage      import load_settings, save_settings, check_and_acquire_ownership
from dialog       import ExportDialog

# ── Find CPython ──────────────────────────────────────────────────────────────
def _find_cpython():
    appdata = os.environ.get('APPDATA', '')
    search_roots = ['pyRevit-Master', 'pyRevit']
    engine_name  = 'CPY3123'
    for root in search_roots:
        candidate = os.path.join(
            appdata, root, 'bin', 'cengines', engine_name, 'python.exe')
        if os.path.exists(candidate):
            return candidate
    import glob
    for root in search_roots:
        pattern = os.path.join(appdata, root, 'bin', 'cengines', 'CPY*', 'python.exe')
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None

_cpython = _find_cpython()
if not _cpython:
    forms.alert(
        'Cannot find pyRevit CPython.\n\n'
        'Expected at:\n'
        '  %APPDATA%\\pyRevit-Master\\bin\\cengines\\CPY3123\\python.exe\n'
        '  %APPDATA%\\pyRevit\\bin\\cengines\\CPY3123\\python.exe\n\n'
        'Check your pyRevit installation.',
        title='CPython not found', warn_icon=True)
    sys.exit(0)

# ── Revit document ────────────────────────────────────────────────────────────
doc = __revit__.ActiveUIDocument.Document  # noqa: F821

if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project file first.',
                title='No project open', warn_icon=True)
    sys.exit(0)

# ── Collect data ──────────────────────────────────────────────────────────────
try:
    project_info = get_project_info(doc)
    sheets_data  = get_sheets_data(doc)
except Exception:
    forms.alert(
        'Failed to read sheet data from the model.\n\n' + traceback.format_exc(),
        title='Data error', warn_icon=True)
    sys.exit(0)

if not sheets_data:
    forms.alert('No printable sheets found in this model.',
                title='No sheets', warn_icon=True)
    sys.exit(0)

sheets_data = [s for s in sheets_data if s.get('revisions')]
if not sheets_data:
    forms.alert('No sheets have been issued yet.\n\nAdd at least one revision to a sheet first.',
                title='No issued sheets', warn_icon=True)
    sys.exit(0)

issue_keys = collect_issue_dates(sheets_data)

# ── Load saved settings ───────────────────────────────────────────────────────
settings = load_settings(doc)

# ── Workshared ownership check ────────────────────────────────────────────────
# In a workshared model, check out the settings DataStorage element from the
# central server before opening the dialog.  If another user has it checked
# out, Revit reports OwnedByOtherUser and we show a named warning.
# Ownership is released when the current user next syncs to central — the
# same contract as editing any other element in a workshared model.
can_proceed, blocking_user = check_and_acquire_ownership(doc)
if not can_proceed:
    forms.alert(
        'The Export Register dialog is currently in use by:\n\n'
        '    {}\n\n'
        'Their settings will be saved to the model when they sync to central.\n'
        'Please wait until then before opening this dialog.'.format(blocking_user),
        title='Settings element checked out', warn_icon=True)
    sys.exit(0)

# ── Settings dialog ───────────────────────────────────────────────────────────
all_packages = sorted(set(s['sheet_type'] for s in sheets_data))

revision_index = {}
for _s in sheets_data:
    for _r in _s['revisions']:
        _k = (_r['date'], _r.get('issued_by', ''))
        if _k not in revision_index:
            revision_index[_k] = set()
        revision_index[_k].add(_s['sheet_type'])

dlg = ExportDialog(issue_keys, settings, all_packages=all_packages,
                   project_info=project_info, revision_index=revision_index)
confirmed, updated_settings = dlg.show()

# ── Save settings — always, whether the user exported or just cancelled ───────
# Saving on cancel lets users update recipients or distribution codes without
# running a full export.  The DataStorage element remains checked out (owned
# by the current user) until the next sync-to-central.
with Transaction(doc, 'LB Issue Register — save settings') as _t:
    _t.Start()
    try:
        save_settings(doc, updated_settings)
        _t.Commit()
    except Exception:
        _t.RollBack()
        forms.alert(
            'Settings could not be saved to the model.\n\n' + traceback.format_exc(),
            title='Save error', warn_icon=True)

# ── Export (only when user confirmed) ────────────────────────────────────────
# Do NOT use sys.exit(0) on the cancel path — pyRevit's cleanup on SystemExit
# rolls back recent transactions, which would undo the settings save above.
# Instead, wrap the export block in a conditional and let the script end
# naturally on cancel.
if confirmed:
    # ── Filter excluded drawing packages ─────────────────────────────────────
    excluded = set(updated_settings.get('excluded_packages', []))
    if excluded:
        sheets_data = [s for s in sheets_data if s['sheet_type'] not in excluded]

    if not sheets_data:
        forms.alert('All drawing packages are excluded. Nothing to export.',
                    title='No packages', warn_icon=True)
    else:
        # ── Choose output folder ──────────────────────────────────────────────
        output_folder = forms.pick_folder(title='Select output folder for register files')
        if output_folder:
            proj_num = project_info.get('project_number', 'PROJECT')

            _reg_date = updated_settings.get('register_issue_date', '').strip()
            _date_prefix = ''
            if _reg_date:
                import datetime as _dt
                for _fmt in ('%d/%m/%Y', '%d/%m/%y', '%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d'):
                    try:
                        _date_prefix = _dt.datetime.strptime(_reg_date, _fmt).strftime('%y%m%d') + '_'
                        break
                    except ValueError:
                        pass

            _reg_rev = updated_settings.get('register_revision', '').strip()
            _rev_suffix = ('_' + ''.join(c for c in _reg_rev if c.isalnum())) if _reg_rev else ''

            _stem     = '{}{}-LB-Issue-Register{}'.format(_date_prefix, proj_num, _rev_suffix)
            xlsx_path = os.path.join(output_folder, _stem + '.xlsx')
            pdf_path  = os.path.join(output_folder, _stem + '.pdf')

            # ── Build Excel + PDF via CPython subprocess ──────────────────────
            payload = {
                'sheets_data':   sheets_data,
                'issue_keys':    [list(k) for k in issue_keys],
                'settings':      updated_settings,
                'project_info':  project_info,
                'xlsx_path':     xlsx_path,
                'pdf_path':      pdf_path,
                'lib_path':      _EXT_LIB,
            }

            import io as _io
            tmp_fd, tmp_json_name = tempfile.mkstemp(suffix='.json')
            os.close(tmp_fd)
            try:
                with _io.open(tmp_json_name, 'w', encoding='utf-8') as _fh:
                    json.dump(payload, _fh, ensure_ascii=False)

                worker_py = os.path.join(_EXT_LIB, 'worker.py')
                result = subprocess.Popen(
                    [_cpython, worker_py, tmp_json_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout_b, stderr_b = result.communicate()
                stdout = stdout_b.decode('utf-8', errors='replace').strip()
                stderr = stderr_b.decode('utf-8', errors='replace').strip()
            finally:
                try:
                    os.unlink(tmp_json_name)
                except Exception:
                    pass

            if result.returncode != 0 or stdout.startswith('ERROR'):
                detail = stdout or stderr
                forms.alert(
                    'Export failed:\n\n' + detail,
                    title='Export error', warn_icon=True)
            else:
                forms.alert(
                    'Export complete!\n\nExcel: {}\nPDF:   {}'.format(xlsx_path, pdf_path),
                    title='LB Issue Register',
                )
