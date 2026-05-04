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

# ── Add lib folder to path ────────────────────────────────────────────────────
_EXT_LIB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from revit_reader import get_sheets_data, get_project_info, collect_issue_dates
from storage      import load_settings, save_settings
from dialog       import ExportDialog

# ── Find CPython ──────────────────────────────────────────────────────────────
def _find_cpython():
    appdata = os.environ.get('APPDATA', '')
    candidate = os.path.join(
        appdata, 'pyRevit-Master', 'bin', 'cengines', 'CPY3123', 'python.exe')
    if os.path.exists(candidate):
        return candidate
    return None

_cpython = _find_cpython()
if not _cpython:
    forms.alert(
        'Cannot find pyRevit CPython at:\n'
        '%APPDATA%\\pyRevit-Master\\bin\\cengines\\CPY3123\\python.exe\n\n'
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

# Only include sheets that have been issued at least once
sheets_data = [s for s in sheets_data if s.get('revisions')]
if not sheets_data:
    forms.alert('No sheets have been issued yet.\n\nAdd at least one revision to a sheet first.',
                title='No issued sheets', warn_icon=True)
    sys.exit(0)

issue_keys = collect_issue_dates(sheets_data)

# ── Load saved settings ───────────────────────────────────────────────────────
settings = load_settings(doc)

# ── Settings dialog ───────────────────────────────────────────────────────────
dlg = ExportDialog(issue_keys, settings)
confirmed, updated_settings = dlg.show()

if not confirmed:
    sys.exit(0)

# ── Save settings back to model ───────────────────────────────────────────────
from Autodesk.Revit.DB import Transaction  # noqa: PLC0415, E402

with Transaction(doc, 'LB Issue Register — save settings') as t:
    t.Start()
    try:
        save_settings(doc, updated_settings)
        t.Commit()
    except Exception:
        t.RollBack()

# ── Choose output folder ──────────────────────────────────────────────────────
output_folder = forms.pick_folder(title='Select output folder for register files')
if not output_folder:
    sys.exit(0)

proj_num  = project_info.get('project_number', 'PROJECT')
xlsx_path = os.path.join(output_folder, '{}-LB-Issue-Register.xlsx'.format(proj_num))
pdf_path  = os.path.join(output_folder, '{}-LB-Issue-Register.pdf'.format(proj_num))

# ── Build Excel + PDF via CPython subprocess ──────────────────────────────────
# Serialise all data to a temp JSON file
payload = {
    'sheets_data':   sheets_data,
    'issue_keys':    [list(k) for k in issue_keys],  # tuples → lists for JSON
    'settings':      updated_settings,
    'project_info':  project_info,
    'xlsx_path':     xlsx_path,
    'pdf_path':      pdf_path,
    'lib_path':      _EXT_LIB,
}

tmp_json = tempfile.NamedTemporaryFile(
    suffix='.json', delete=False, mode='w', encoding='utf-8')
try:
    json.dump(payload, tmp_json, ensure_ascii=False)
    tmp_json.close()

    worker_py = os.path.join(_EXT_LIB, 'worker.py')
    result = subprocess.Popen(
        [_cpython, worker_py, tmp_json.name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_b, stderr_b = result.communicate(timeout=180)
    stdout = stdout_b.decode('utf-8', errors='replace').strip()
    stderr = stderr_b.decode('utf-8', errors='replace').strip()
finally:
    try:
        os.unlink(tmp_json.name)
    except Exception:
        pass

if result.returncode != 0 or stdout.startswith('ERROR'):
    detail = stdout or stderr
    forms.alert(
        'Export failed:\n\n' + detail,
        title='Export error', warn_icon=True)
    sys.exit(0)

# ── Done ──────────────────────────────────────────────────────────────────────
forms.alert(
    'Export complete!\n\nExcel: {}\nPDF:   {}'.format(xlsx_path, pdf_path),
    title='LB Issue Register',
)
