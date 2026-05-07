# -*- coding: utf-8 -*-
"""
CPython worker — called as a subprocess by script.py.

Reads a JSON payload file, builds the Excel register, exports PDF.
Prints "OK" on success or "ERROR: <message>" on failure.
"""

import sys
import os
import json
import traceback


def main():
    if len(sys.argv) < 2:
        print('ERROR: no payload file argument')
        sys.exit(1)

    payload_path = sys.argv[1]
    with open(payload_path, 'r', encoding='utf-8') as fh:
        payload = json.load(fh)

    lib_path = payload.get('lib_path', os.path.dirname(os.path.abspath(__file__)))
    if lib_path not in sys.path:
        sys.path.insert(0, lib_path)

    from excel_builder import build_register
    from pdf_exporter  import export_pdf

    sheets_data   = payload['sheets_data']
    issue_keys    = [tuple(k) for k in payload['issue_keys']]
    settings      = payload['settings']
    project_info  = payload['project_info']
    xlsx_path     = payload['xlsx_path']
    pdf_path      = payload['pdf_path']

    try:
        build_register(
            sheets_data  = sheets_data,
            issue_keys   = issue_keys,
            settings     = settings,
            output_path  = xlsx_path,
            project_info = project_info,
        )
    except Exception:
        print('ERROR: Excel build failed:\n' + traceback.format_exc())
        sys.exit(1)

    try:
        export_pdf(xlsx_path, pdf_path)
    except Exception:
        print('ERROR: PDF export failed:\n' + traceback.format_exc())
        sys.exit(1)

    print('OK')


if __name__ == '__main__':
    main()
