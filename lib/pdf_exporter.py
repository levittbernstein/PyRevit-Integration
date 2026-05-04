# -*- coding: utf-8 -*-
"""Export an Excel file to PDF.

Tries LibreOffice (headless) first — it preserves rich text, bold, colours.
Falls back to Excel COM automation if LibreOffice is not installed.
"""

import os
import shutil
import subprocess


def _find_libreoffice():
    candidates = [
        r'C:\Program Files\LibreOffice\program\soffice.exe',
        r'C:\Program Files (x86)\LibreOffice\program\soffice.exe',
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _export_via_libreoffice(soffice_exe, excel_path, pdf_path):
    xlsx_abs = os.path.abspath(excel_path)
    out_dir  = os.path.dirname(os.path.abspath(pdf_path))

    proc = subprocess.Popen(
        [soffice_exe, '--headless', '--convert-to', 'pdf', '--outdir', out_dir, xlsx_abs],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout_b, stderr_b = proc.communicate()
    if proc.returncode != 0:
        msg = (stderr_b or stdout_b).decode('utf-8', errors='replace')
        raise RuntimeError('LibreOffice PDF export failed:\n' + msg)

    # LibreOffice writes <basename>.pdf into outdir
    generated = os.path.join(out_dir,
                             os.path.splitext(os.path.basename(xlsx_abs))[0] + '.pdf')
    target = os.path.abspath(pdf_path)
    if os.path.normcase(generated) != os.path.normcase(target) and os.path.exists(generated):
        shutil.move(generated, target)


def _export_via_excel_com(excel_path, pdf_path):
    try:
        import win32com.client as win32
    except ImportError:
        raise RuntimeError(
            'pywin32 is not installed. Run:\n'
            '    pip install pywin32\n'
            'in pyRevit\'s CPython environment, then retry.'
        )

    excel = None
    wb    = None
    try:
        excel = win32.Dispatch('Excel.Application')
        excel.Visible          = False
        excel.DisplayAlerts    = False
        excel.AskToUpdateLinks = False

        wb = excel.Workbooks.Open(
            os.path.abspath(excel_path),
            UpdateLinks=False,
            ReadOnly=True,
        )
        ws = wb.Worksheets(1)
        ws.ExportAsFixedFormat(
            Type=0,
            Filename=os.path.abspath(pdf_path),
            Quality=0,
            IncludeDocProperties=True,
            IgnorePrintAreas=False,
            OpenAfterPublish=False,
        )
    except Exception as exc:
        raise RuntimeError(
            'Excel COM PDF export failed: {}\n\n'
            'Make sure Microsoft Excel is installed and not currently '
            'blocking automation (e.g. an open dialog box).'.format(exc)
        )
    finally:
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:
                pass
        if excel is not None:
            try:
                excel.Quit()
            except Exception:
                pass


def export_pdf(excel_path, pdf_path):
    """Export *excel_path* to *pdf_path*, trying LibreOffice then Excel COM."""
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    soffice = _find_libreoffice()
    if soffice:
        _export_via_libreoffice(soffice, excel_path, pdf_path)
    else:
        _export_via_excel_com(excel_path, pdf_path)
