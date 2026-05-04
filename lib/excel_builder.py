# -*- coding: utf-8 -*-
"""
Build the issue register Excel workbook by filling data into the LB template.

The template (lib/template.xltx) already contains all formatting for:
  - Rows 1-11  (header, key, disclaimer, distribution block, column headers)
  - Rows 12+   (pre-formatted data rows)
  - Date columns L+ (pre-formatted empty cells)

We load the template, clear/overwrite content, and save.
"""

import os
from datetime import datetime

from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.styles.colors import Color
from openpyxl.utils import get_column_letter

# Header dark fill (theme 0 / dark1 = black, tint -0.15 → near-black matching LB template)
_HEADER_FILL = PatternFill(patternType='solid',
                           fgColor=Color(theme=0, tint=-0.14999847407452621))

_TEMPLATE = os.path.join(os.path.dirname(__file__), 'template.xltx')

# ── Layout constants (must match template) ───────────────────────────────────
HEADER_ROW     = 11
DATA_ROW_START = 12
FIRST_DATE_COL = 12   # column L

_HEADER_LABELS = [
    'Drawing Package', 'Project', 'Originator',
    'Functional Breakdown', 'Spatial Breakdown', 'Form', 'Discipline',
    'Number', 'Document Title', 'Size', 'Scale',
]

_DATA_ROW_HEIGHT = 21.6
_MAX_RECIPIENTS  = 7


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(date_str):
    for fmt in ('%d/%m/%y', '%d/%m/%Y', '%d.%m.%y', '%d.%m.%Y'):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    return None


def _fmt_header(date_str):
    dt = _parse_date(date_str)
    return dt.strftime('%d/%m/%y') if dt else date_str


def _fmt_title(date_str):
    dt = _parse_date(date_str)
    return dt.strftime('%d.%m.%Y') if dt else date_str


# ── Style cloning helpers ─────────────────────────────────────────────────────

def _copy_cell_style(src, dst):
    """Copy font, fill, border, alignment from src cell to dst cell."""
    if src.has_style:
        dst.font      = src.font.copy()
        dst.fill      = src.fill.copy()
        dst.border    = src.border.copy()
        dst.alignment = src.alignment.copy()


def _unmerge_region(ws, min_row, max_row, min_col, max_col):
    """Remove all merges overlapping a region and flush stale MergedCell cache entries.

    openpyxl leaves MergedCell (read-only) objects in ws._cells after removing
    a merge range. Deleting them forces fresh writable Cell objects on next access.
    """
    to_remove = [
        m for m in list(ws.merged_cells.ranges)
        if m.min_row <= max_row and m.max_row >= min_row
        and m.min_col <= max_col and m.max_col >= min_col
    ]
    for m in to_remove:
        ws.merged_cells.remove(m)
        for r in range(m.min_row, m.max_row + 1):
            for c in range(m.min_col, m.max_col + 1):
                ws._cells.pop((r, c), None)


# ── Main entry point ──────────────────────────────────────────────────────────

def build_register(sheets_data, issue_keys, settings, output_path, project_info):
    wb = load_workbook(_TEMPLATE)
    ws = wb.active

    n_dates  = len(issue_keys)
    last_col = FIRST_DATE_COL + n_dates - 1

    # How many date columns are already in the template?
    template_date_cols = ws.max_column - (FIRST_DATE_COL - 1)

    # ── Expand date columns if we need more than the template provides ─────
    if n_dates > template_date_cols:
        _expand_date_columns(ws, template_date_cols, n_dates)

    # ── Widen columns B-H ────────────────────────────────────────────────
    for _col, _w in [('B', 9), ('C', 9), ('D', 9), ('E', 9), ('F', 9), ('G', 9), ('H', 22)]:
        ws.column_dimensions[_col].width = _w

    # ── Row 1: project name ───────────────────────────────────────────────
    ws.cell(row=1, column=1).value = project_info.get('project_name', '')

    # ── Row 3: label → "Issue date", value → today's date ────────────────
    ws.cell(row=3, column=11).value = 'Issue date'
    ws.cell(row=3, column=FIRST_DATE_COL).value = datetime.now().strftime('%d.%m.%Y')

    # ── Rows 4-10: distribution block ─────────────────────────────────────
    _write_distribution_block(ws, issue_keys, settings)

    # ── Row 11: date column headers ───────────────────────────────────────
    _write_date_headers(ws, issue_keys)

    # ── Rows 12+: drawing data ────────────────────────────────────────────
    last_data_row = _write_data_rows(ws, sheets_data, issue_keys, last_col)

    # ── Fix merge for row 1 and 2 to cover all columns ────────────────────
    _remerge_row(ws, 1, last_col)
    _remerge_row(ws, 2, last_col)
    _remerge_row(ws, 3, last_col, start_col=FIRST_DATE_COL)

    # ── Freeze panes ──────────────────────────────────────────────────────
    ws.freeze_panes = ws.cell(row=DATA_ROW_START, column=FIRST_DATE_COL)

    # ── Print area: stop at the last written data row ─────────────────────
    ws.print_area = 'A1:{}{}'.format(get_column_letter(last_col), last_data_row)

    wb.template = False
    wb.save(output_path)
    return output_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _latest_code(sheets_data, date_str, issued_by):
    codes = [
        rev['code']
        for sheet in sheets_data
        for rev in sheet['revisions']
        if rev['date'] == date_str and rev.get('issued_by', '') == issued_by
    ]
    if not codes:
        return ''
    c = [x for x in codes if x.upper().startswith('C')]
    p = [x for x in codes if x.upper().startswith('P')]
    return sorted(c or p or codes)[-1]


def _remerge_row(ws, row, last_col, start_col=1):
    """Remove any existing merge on the row and re-merge from start_col to last_col."""
    _unmerge_region(ws, row, row, start_col, last_col)
    if last_col > start_col:
        ws.merge_cells(start_row=row, start_column=start_col,
                       end_row=row, end_column=last_col)


def _expand_date_columns(ws, template_cols, needed_cols):
    """Copy the style of the last template date column to fill in extra columns."""
    ref_col = FIRST_DATE_COL + template_cols - 1
    ref_hdr = ws.cell(row=HEADER_ROW, column=ref_col)

    for extra in range(template_cols, needed_cols):
        new_col = FIRST_DATE_COL + extra
        # Header row 11
        _copy_cell_style(ref_hdr, ws.cell(row=HEADER_ROW, column=new_col))
        ws.column_dimensions[get_column_letter(new_col)].width = \
            ws.column_dimensions[get_column_letter(ref_col)].width or 4.57
        # Distribution rows 4-10
        for r in range(4, 11):
            _copy_cell_style(ws.cell(row=r, column=ref_col),
                             ws.cell(row=r, column=new_col))
        # Data rows 12+
        for r in range(DATA_ROW_START, ws.max_row + 1):
            _copy_cell_style(ws.cell(row=r, column=ref_col),
                             ws.cell(row=r, column=new_col))


def _write_distribution_block(ws, issue_keys, settings):
    recipients   = settings.get('recipients', [])
    saved_issues = settings.get('issues', {})

    # Capture font/border/alignment refs BEFORE unmerge flushes the cell cache.
    ref_label = ws.cell(row=4, column=10)          # J4 'Client'
    ref_code  = ws.cell(row=4, column=FIRST_DATE_COL)  # L4 date column

    # Unmerge everything in rows 4-10 cols I+ and flush stale MergedCell cache
    _unmerge_region(ws, 4, 10, 9, ws.max_column)

    # Paint ALL cells in rows 4-10 cols I+ with the header dark fill first,
    # then overwrite content. Using _HEADER_FILL directly (not a theme-copy)
    # because openpyxl can't reliably round-trip theme-colored fills via copy().
    for r in range(4, 11):
        for c in range(9, ws.max_column + 1):
            cell = ws.cell(row=r, column=c)
            cell.fill  = _HEADER_FILL
            cell.value = None

    for i, recipient in enumerate(recipients[:_MAX_RECIPIENTS]):
        row    = 4 + i
        r_name = recipient.get('name', '')

        # Write label into I (anchor for merge I:K)
        label_cell = ws.cell(row=row, column=9)
        label_cell.value = r_name
        _copy_cell_style(ref_label, label_cell)
        label_cell.fill = _HEADER_FILL   # override fill explicitly
        label_cell.alignment = Alignment(horizontal='right', vertical='center')
        ws.merge_cells(start_row=row, start_column=9, end_row=row, end_column=11)

        # Write distribution codes
        for col_idx, (date_str, issued_by) in enumerate(issue_keys):
            key  = '{}||{}'.format(date_str, issued_by)
            code = saved_issues.get(key, {}).get(r_name, '')
            dc   = FIRST_DATE_COL + col_idx
            code_cell = ws.cell(row=row, column=dc)
            _copy_cell_style(ref_code, code_cell)
            code_cell.fill  = _HEADER_FILL   # override fill explicitly
            code_cell.value = code


def _write_date_headers(ws, issue_keys):
    """Write date into row 11 date columns (date only, no issued_by initials)."""
    for col_idx, (date_str, _issued_by) in enumerate(issue_keys):
        col  = FIRST_DATE_COL + col_idx
        cell = ws.cell(row=HEADER_ROW, column=col)
        cell.value = _fmt_header(date_str)


def _write_data_rows(ws, sheets_data, issue_keys, last_col):
    issue_idx = {key: i for i, key in enumerate(issue_keys)}

    # Get style reference from template row 12 (first data row)
    ref_cols = {c: ws.cell(row=DATA_ROW_START, column=c) for c in range(1, last_col + 1)}

    # Unmerge and clear all data rows
    _unmerge_region(ws, DATA_ROW_START, ws.max_row, 1, ws.max_column)
    for row in ws.iter_rows(min_row=DATA_ROW_START, max_row=ws.max_row):
        for cell in row:
            cell.value = None

    current_row = DATA_ROW_START
    prev_group  = None

    for sheet in sheets_data:
        group = sheet['sheet_type']

        if prev_group is not None and group != prev_group:
            ws.row_dimensions[current_row].height = _DATA_ROW_HEIGHT
            current_row += 1

        prev_group = group
        ws.row_dimensions[current_row].height = _DATA_ROW_HEIGHT
        r = current_row

        # Restore styles from template row 12 reference
        for c in range(1, last_col + 1):
            _copy_cell_style(ref_cols.get(c, ref_cols.get(min(c, 11))),
                             ws.cell(row=r, column=c))

        values = [
            (1,  sheet['sheet_type'],           'center'),
            (2,  sheet['project'],              'center'),
            (3,  sheet['originator'],           'center'),
            (4,  sheet['functional_breakdown'], 'center'),
            (5,  sheet['spatial_breakdown'],    'center'),
            (6,  sheet['form'],                 'center'),
            (7,  sheet['discipline'],           'center'),
            (8,  sheet['number'],               'center'),
            (9,  sheet['title'],                'left'),
            (10, sheet['size'],                 'center'),
            (11, sheet['scale'],                'center'),
        ]

        for col, val, _ in values:
            ws.cell(row=r, column=col).value = val

        for rev in sheet['revisions']:
            key = (rev['date'], rev.get('issued_by', ''))
            if key in issue_idx:
                col = FIRST_DATE_COL + issue_idx[key]
                ws.cell(row=r, column=col).value = rev['code']

        current_row += 1

    return current_row - 1  # last row with data
