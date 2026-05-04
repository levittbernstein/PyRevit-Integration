# -*- coding: utf-8 -*-
"""Export an Excel file to PDF via Excel COM automation (win32com)."""

import os


def export_pdf(excel_path, pdf_path):
    """Open *excel_path* in Excel, print sheet 1 to *pdf_path*, close."""
    # Remove existing PDF so Excel doesn't get a "overwrite?" prompt
    if os.path.exists(pdf_path):
        os.remove(pdf_path)

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
