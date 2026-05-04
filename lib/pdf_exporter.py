# -*- coding: utf-8 -*-
"""Export an Excel file to PDF.

Strategy:
  1. LibreOffice headless (preserves all rich text/colour — best quality).
  2. Excel COM: open template + output in the same session, copy A4 and A7
     (the KEY Revisions and disclaimer cells) back from the template to
     restore rich text that openpyxl stripped, then ExportAsFixedFormat.
"""

import os
import shutil
import subprocess


_TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'template.xltx')


# ── LibreOffice ───────────────────────────────────────────────────────────────

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

    generated = os.path.join(out_dir,
                             os.path.splitext(os.path.basename(xlsx_abs))[0] + '.pdf')
    target = os.path.abspath(pdf_path)
    if os.path.normcase(generated) != os.path.normcase(target) and os.path.exists(generated):
        shutil.move(generated, target)


# ── Excel COM ─────────────────────────────────────────────────────────────────

def _copy_rich_text(src, dst):
    """Copy value + character-level formatting from COM Range src to dst."""
    dst.Value = src.Value
    n = src.Characters.Count
    for i in range(1, n + 1):
        sf = src.Characters(i, 1).Font
        df = dst.Characters(i, 1).Font
        df.Bold      = sf.Bold
        df.Italic    = sf.Italic
        df.Underline = sf.Underline
        df.Color     = sf.Color
        df.Size      = sf.Size
        df.Name      = sf.Name


def _export_via_excel_com(excel_path, pdf_path):
    try:
        import win32com.client as win32
    except ImportError:
        raise RuntimeError(
            'pywin32 is not installed. Run:\n'
            '    pip install pywin32\n'
            'in pyRevit\'s CPython environment, then retry.'
        )

    excel   = None
    tmpl_wb = None
    out_wb  = None
    try:
        excel = win32.Dispatch('Excel.Application')
        excel.Visible          = False
        excel.DisplayAlerts    = False
        excel.AskToUpdateLinks = False

        out_wb = excel.Workbooks.Open(
            os.path.abspath(excel_path),
            UpdateLinks=False,
            ReadOnly=False,
        )
        out_ws = out_wb.Worksheets(1)

        # ── Restore rich text from template ──────────────────────────────────
        # openpyxl strips character-level formatting (bold/colour/underline)
        # when loading the template.  Re-copy those cells from the original
        # .xltx before exporting so the PDF has the correct formatting.
        if os.path.exists(_TEMPLATE):
            tmpl_wb = excel.Workbooks.Open(
                os.path.abspath(_TEMPLATE),
                UpdateLinks=False,
                ReadOnly=True,
            )
            tmpl_ws = tmpl_wb.Worksheets(1)
            # A4:H6  — KEY Revisions cell (merged, value in A4)
            # A7:H10 — disclaimer cell     (merged, value in A7)
            for row in (4, 7):
                _copy_rich_text(tmpl_ws.Cells(row, 1), out_ws.Cells(row, 1))
            tmpl_wb.Close(SaveChanges=False)
            tmpl_wb = None
            out_wb.Save()

        # ── Export PDF ────────────────────────────────────────────────────────
        out_ws.ExportAsFixedFormat(
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
        if tmpl_wb is not None:
            try: tmpl_wb.Close(SaveChanges=False)
            except Exception: pass
        if out_wb is not None:
            try: out_wb.Close(SaveChanges=False)
            except Exception: pass
        if excel is not None:
            try: excel.Quit()
            except Exception: pass


# ── Public API ────────────────────────────────────────────────────────────────

def export_pdf(excel_path, pdf_path):
    """Export *excel_path* to *pdf_path*, trying LibreOffice then Excel COM."""
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

    soffice = _find_libreoffice()
    if soffice:
        _export_via_libreoffice(soffice, excel_path, pdf_path)
    else:
        _export_via_excel_com(excel_path, pdf_path)
