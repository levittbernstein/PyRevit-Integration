# -*- coding: utf-8 -*-
"""
LB Issue Register Exporter
==========================
pyRevit push-button script.

Extracts all sheets + revision histories from the active Revit model,
shows a settings dialog (pre-populated from extensible storage), then
exports a formatted Excel register and matching PDF.

Requirements
------------
• pyRevit with CPython engine  (check: pyRevit Settings → About)
• openpyxl   — pip install openpyxl
• pywin32    — pip install pywin32
"""

# ── Engine check ─────────────────────────────────────────────────────────────
import sys
if sys.version_info.major < 3:
    from pyrevit import forms
    forms.alert(
        'This tool requires the CPython engine.\n\n'
        'How to fix:\n'
        '1. Open pyRevit Settings (pyRevit tab → Settings button)\n'
        '2. Under "CPython Engine" enable it and select Python 3.x\n'
        '3. Restart Revit and try again.\n\n'
        'Current engine: IronPython {}'.format(sys.version),
        title='Wrong Python Engine',
        warn_icon=True,
    )
    sys.exit(0)

# ── Dependency check ─────────────────────────────────────────────────────────
def _ensure_packages():
    missing = []
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        missing.append('openpyxl')
    try:
        import win32com  # noqa: F401
    except ImportError:
        missing.append('pywin32')

    if missing:
        msg = (
            'The following Python packages are not installed:\n    {}\n\n'
            'Install them by running this command in a terminal:\n'
            '    "{}" -m pip install {}\n\n'
            'Then restart Revit.'
        ).format(
            ', '.join(missing),
            sys.executable,
            ' '.join(missing),
        )
        from pyrevit import forms as _f
        _f.alert(msg, title='Missing packages', warn_icon=True)
        sys.exit(0)

_ensure_packages()

# ── Standard imports (all available at this point) ───────────────────────────
import os
import traceback

from pyrevit import forms, revit, script as pvscript

# Add the extension lib folder to path so we can import our modules
_EXT_LIB = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from revit_reader import get_sheets_data, get_project_info, collect_issue_dates
from storage      import load_settings, save_settings
from dialog       import ExportDialog
from excel_builder import build_register
from pdf_exporter  import export_pdf

# ── Revit document ────────────────────────────────────────────────────────────
doc = __revit__.ActiveUIDocument.Document  # noqa: F821

if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project file first.',
                title='No project open', warn_icon=True)
    sys.exit(0)

# ── Collect data ─────────────────────────────────────────────────────────────
output = pvscript.get_output()
output.print_md('## LB Issue Register — collecting sheet data…')

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

issue_keys = collect_issue_dates(sheets_data)  # [(date_str, issued_by), …]

output.print_md('Found **{}** sheets and **{}** unique issue dates.'.format(
    len(sheets_data), len(issue_keys)))

# ── Load saved settings ───────────────────────────────────────────────────────
settings = load_settings(doc)

# ── Settings dialog ───────────────────────────────────────────────────────────
dlg = ExportDialog(issue_keys, settings)
confirmed, updated_settings = dlg.show()

if not confirmed:
    output.print_md('Export cancelled.')
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
        output.print_md('⚠ Could not save settings to model (continuing anyway).')

# ── Choose output folder ──────────────────────────────────────────────────────
output_folder = forms.pick_folder(title='Select output folder for register files')
if not output_folder:
    output.print_md('No folder selected — export cancelled.')
    sys.exit(0)

proj_num   = project_info.get('project_number', 'PROJECT')
xlsx_name  = '{}-LB-Issue-Register.xlsx'.format(proj_num)
pdf_name   = '{}-LB-Issue-Register.pdf'.format(proj_num)
xlsx_path  = os.path.join(output_folder, xlsx_name)
pdf_path   = os.path.join(output_folder, pdf_name)

# ── Build Excel ───────────────────────────────────────────────────────────────
output.print_md('Building Excel register…')
try:
    build_register(
        sheets_data    = sheets_data,
        issue_keys     = issue_keys,
        settings       = updated_settings,
        output_path    = xlsx_path,
        project_info   = project_info,
    )
    output.print_md('✓ Excel saved: `{}`'.format(xlsx_path))
except Exception:
    forms.alert(
        'Excel export failed:\n\n' + traceback.format_exc(),
        title='Excel error', warn_icon=True)
    sys.exit(0)

# ── Export PDF ────────────────────────────────────────────────────────────────
output.print_md('Exporting PDF via Excel…')
try:
    export_pdf(xlsx_path, pdf_path)
    output.print_md('✓ PDF saved: `{}`'.format(pdf_path))
except Exception:
    forms.alert(
        'PDF export failed:\n\n' + traceback.format_exc(),
        title='PDF error', warn_icon=True)
    # Excel was already saved — don't abort entirely
    output.print_md('⚠ PDF export failed but Excel was saved successfully.')

# ── Done ──────────────────────────────────────────────────────────────────────
output.print_md(
    '\n---\n**Export complete.**\n'
    '- Excel: `{}`\n'
    '- PDF: `{}`'.format(xlsx_path, pdf_path)
)
forms.alert(
    'Export complete!\n\n'
    'Excel: {}\n'
    'PDF:   {}'.format(xlsx_path, pdf_path),
    title='LB Issue Register',
)
