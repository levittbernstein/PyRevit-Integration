# -*- coding: utf-8 -*-
"""Extract sheet and revision data from the active Revit document."""

import re
from collections import OrderedDict


def _normalise_date(val):
    """Return a DD/MM/YYYY string from whatever RevisionDate gives us.

    Revit may return: a plain string, a Python datetime.date, or a .NET
    DateTime struct (which has Day/Month/Year but no strftime).
    An unset RevisionDate often comes back as DateTime.MinValue (year 0001)
    or a similar sentinel — those are treated as 'no date'.
    """
    if not val:
        return ''
    # Python datetime.date / datetime.datetime
    try:
        if val.year < 1900:
            return ''
        return val.strftime('%d/%m/%Y')
    except AttributeError:
        pass
    # .NET DateTime struct exposed via IronPython interop
    try:
        year = int(val.Year)
        if year < 1900:
            return ''
        return '{:02d}/{:02d}/{:04d}'.format(int(val.Day), int(val.Month), year)
    except AttributeError:
        pass
    # Plain string — strip whitespace and reject obvious sentinel values
    s = str(val).strip()
    if not s:
        return ''
    # Reject year-0001 sentinel strings like '01/01/0001' or '1/1/1'
    try:
        from datetime import datetime as _dt
        for _fmt in ('%d/%m/%Y', '%d/%m/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                if _dt.strptime(s, _fmt).year < 1900:
                    return ''
                break
            except ValueError:
                pass
    except Exception:
        pass
    return s


def _get_param(element, name, built_in=None):
    """Return a parameter value by name or BuiltInParameter, or ''."""
    try:
        if built_in is not None:
            p = element.get_Parameter(built_in)
            if p and p.HasValue:
                return p.AsString() or str(p.AsDouble()) if p.StorageType.ToString() == 'Double' else p.AsString() or ''
        p = element.LookupParameter(name)
        if p and p.HasValue:
            st = p.StorageType.ToString()
            if st == 'String':
                return p.AsString() or ''
            if st == 'Double':
                return str(p.AsValueString() or p.AsDouble())
            if st == 'Integer':
                return str(p.AsInteger())
            if st == 'ElementId':
                return str(p.AsElementId().IntegerValue)
    except Exception:
        pass
    return ''


def _mm_from_internal(feet_value):
    """Convert Revit internal units (feet) to millimetres."""
    return feet_value * 304.8


def _paper_size(width_mm, height_mm):
    """Derive ISO paper size code from sheet dimensions (mm)."""
    sizes = {
        'A0': (1189, 841),
        'A1': (841, 594),
        'A2': (594, 420),
        'A3': (420, 297),
        'A4': (297, 210),
    }
    w = round(max(width_mm, height_mm))
    h = round(min(width_mm, height_mm))
    for name, (sw, sh) in sizes.items():
        if abs(w - sw) <= 5 and abs(h - sh) <= 5:
            return name
    return '{:.0f}x{:.0f}'.format(w, h)


def _revision_code(doc, sheet, rev_element, per_sheet_index):
    """
    Return the revision code string (e.g. 'P01', 'C03') for a revision
    as it would appear on the given sheet.

    Strategy:
      1. Try ViewSheet.GetRevisionNumberOnSheet (Revit 2024+).
      2. Try the PROJECT_REVISION_REVISION_NUM built-in parameter on the
         Revision element — returns the full custom code when custom
         numbering is used.
      3. Fall back to deriving prefix from the Numbering sequence name
         (e.g. 'P-Preliminary' → prefix 'P') combined with per-sheet index.
    """
    from Autodesk.Revit.DB import BuiltInParameter  # noqa: PLC0415

    # Strategy 1: GetRevisionNumberOnSheet (2024+)
    try:
        code = sheet.GetRevisionNumberOnSheet(rev_element.Id)
        if code:
            return code
    except AttributeError:
        pass

    # Strategy 2: Built-in parameter on Revision element
    try:
        p = rev_element.get_Parameter(BuiltInParameter.PROJECT_REVISION_REVISION_NUM)
        if p and p.HasValue:
            val = p.AsString() or ''
            if val and not val.isdigit():
                # Already a full code like 'P01'
                return val
    except Exception:
        pass

    # Strategy 3: Construct from numbering sequence name + per-sheet position
    try:
        num_param = rev_element.LookupParameter('Numbering')
        seq_name = num_param.AsString() if (num_param and num_param.HasValue) else ''
    except Exception:
        seq_name = ''

    if not seq_name:
        # Try the element's Description as last resort
        seq_name = _get_param(rev_element, 'Description')

    # Extract prefix: 'P-Preliminary' → 'P', 'C-Contractual' → 'C'
    prefix = seq_name.split('-')[0] if seq_name else 'P'
    return '{}{:02d}'.format(prefix, per_sheet_index)


def get_project_info(doc):
    """Return a dict of project-level information."""
    info = doc.ProjectInformation
    return {
        'project_number': _get_param(info, 'Project Number') or info.Number,
        'project_name':   _get_param(info, 'Project Name') or info.Name,
        'client_name':    _get_param(info, 'Client Name') or '',
        'author':         _get_param(info, 'Author') or '',
        'org_name':       _get_param(info, 'Organization Name') or '',
    }


def get_all_revision_dates(doc):
    """
    Return an OrderedDict mapping revision ElementId → (date_str, code)
    for every Revision element in the project, ordered by sequence number.
    """
    from Autodesk.Revit.DB import FilteredElementCollector, Revision  # noqa: PLC0415

    revisions = (
        FilteredElementCollector(doc)
        .OfClass(Revision)
        .ToElements()
    )

    # Sort by SequenceNumber
    def _seq(r):
        try:
            return r.SequenceNumber
        except Exception:
            return 0

    ordered = sorted(revisions, key=_seq)
    result = OrderedDict()
    for rev in ordered:
        result[rev.Id] = {
            'date': _normalise_date(rev.RevisionDate),
            'description': rev.Description or '',
            'issued_by': rev.IssuedBy or '',
            'issued_to': rev.IssuedTo or '',
        }
    return result


def get_sheets_data(doc):
    """
    Return a list of sheet dicts, each containing:
      sheet_type, project, originator, functional_breakdown,
      spatial_breakdown, form, discipline, number, title,
      size, scale, revisions (list of {date, code})

    Sorted first by sheet_type (group), then by sheet number.
    """
    from Autodesk.Revit.DB import (  # noqa: PLC0415
        FilteredElementCollector, ViewSheet, BuiltInParameter, UnitUtils
    )

    project_info = get_project_info(doc)
    sheets = (
        FilteredElementCollector(doc)
        .OfClass(ViewSheet)
        .ToElements()
    )

    # ── Pre-build titleblock lookup (one collector for the whole model) ──────
    # Running a scoped FilteredElementCollector per sheet is very slow on large
    # models.  Collect everything once and index by OwnerViewId.
    _tb_name_by_sheet = {}   # ElementId → family name string
    _tb_sym_by_sheet  = {}   # ElementId → FamilySymbol (for dimension fallback)
    try:
        from Autodesk.Revit.DB import FamilyInstance, BuiltInCategory  # noqa: PLC0415
        for _tb in (FilteredElementCollector(doc)
                    .OfCategory(BuiltInCategory.OST_TitleBlocks)
                    .OfClass(FamilyInstance)
                    .ToElements()):
            try:
                _oid = _tb.OwnerViewId
                if _oid not in _tb_name_by_sheet:
                    _tb_name_by_sheet[_oid] = _tb.Symbol.Family.Name
                    _tb_sym_by_sheet[_oid]  = _tb.Symbol
            except Exception:
                pass
    except Exception:
        pass

    result = []
    for sheet in sheets:
        if not sheet.CanBePrinted:
            continue  # skip placeholder sheets

        # --- Drawing number components ---
        sheet_number = _get_param(sheet, 'Sheet Number') or sheet.SheetNumber
        sheet_name   = _get_param(sheet, 'Sheet Name')   or sheet.Name

        originator           = _get_param(sheet, 'Originator')
        zone_building        = _get_param(sheet, 'Zone/Building')
        level                = _get_param(sheet, 'Level')
        file_type            = _get_param(sheet, 'File Type')
        discipline           = _get_param(sheet, 'Discipline')
        sheet_type           = _get_param(sheet, 'Sheet Type')

        # Sheet size — O(1) lookup into pre-built dict
        size = ''
        family_name = _tb_name_by_sheet.get(sheet.Id, '')
        if family_name:
            m = re.search(r'A[0-4](?!\d)', family_name, re.IGNORECASE)
            if m:
                size = m.group(0).upper()
            else:
                # Try Width/Height from the symbol
                try:
                    sym = _tb_sym_by_sheet.get(sheet.Id)
                    if sym:
                        w_p = h_p = None
                        for w_name in ('Width', 'Sheet Width', 'W'):
                            _p = sym.LookupParameter(w_name)
                            if _p and _p.HasValue:
                                w_p = _p
                                break
                        for h_name in ('Height', 'Sheet Height', 'H'):
                            _p = sym.LookupParameter(h_name)
                            if _p and _p.HasValue:
                                h_p = _p
                                break
                        if w_p and h_p:
                            try:
                                from Autodesk.Revit.DB import UnitTypeId  # noqa: PLC0415
                                w_mm = UnitUtils.ConvertFromInternalUnits(w_p.AsDouble(), UnitTypeId.Millimeters)
                                h_mm = UnitUtils.ConvertFromInternalUnits(h_p.AsDouble(), UnitTypeId.Millimeters)
                            except (ImportError, AttributeError):
                                from Autodesk.Revit.DB import DisplayUnitType  # noqa: PLC0415
                                w_mm = UnitUtils.ConvertFromInternalUnits(w_p.AsDouble(), DisplayUnitType.DUT_MILLIMETERS)
                                h_mm = UnitUtils.ConvertFromInternalUnits(h_p.AsDouble(), DisplayUnitType.DUT_MILLIMETERS)
                            size = _paper_size(w_mm, h_mm)
                except Exception:
                    pass
                if not size:
                    pass  # size stays blank for non-standard titleblock families

        if not size:
            try:
                try:
                    from Autodesk.Revit.DB import UnitTypeId  # noqa: PLC0415
                    w_mm = UnitUtils.ConvertFromInternalUnits(sheet.SheetWidth,  UnitTypeId.Millimeters)
                    h_mm = UnitUtils.ConvertFromInternalUnits(sheet.SheetHeight, UnitTypeId.Millimeters)
                except (ImportError, AttributeError):
                    from Autodesk.Revit.DB import DisplayUnitType  # noqa: PLC0415
                    w_mm = UnitUtils.ConvertFromInternalUnits(sheet.SheetWidth,  DisplayUnitType.DUT_MILLIMETERS)
                    h_mm = UnitUtils.ConvertFromInternalUnits(sheet.SheetHeight, DisplayUnitType.DUT_MILLIMETERS)
                size = _paper_size(w_mm, h_mm)
            except Exception:
                pass

        # Scale
        scale_param = sheet.LookupParameter('Scale')
        scale = scale_param.AsValueString() if (scale_param and scale_param.HasValue) else ''
        if not scale:
            # Try to get from the first viewport on the sheet
            try:
                vp_ids = sheet.GetAllViewports()
                if vp_ids:
                    from Autodesk.Revit.DB import Viewport  # noqa: PLC0415
                    vp = doc.GetElement(list(vp_ids)[0])
                    view = doc.GetElement(vp.ViewId)
                    scale_val = view.LookupParameter('View Scale')
                    scale = scale_val.AsValueString() if (scale_val and scale_val.HasValue) else ''
            except Exception:
                pass

        # --- Revisions on this sheet ---
        rev_ids = list(sheet.GetAllRevisionIds())
        sheet_revisions = []
        _seq_counters = {}  # per-sequence counters for Strategy 3 fallback
        for rev_id in rev_ids:
            rev_elem = doc.GetElement(rev_id)
            if rev_elem is None:
                continue
            date_str = _normalise_date(rev_elem.RevisionDate)
            if not date_str:
                continue  # skip revisions with no date — they produce a spurious column
            # Identify the numbering sequence so Strategy 3 uses per-sequence counts
            # (prevents P and C revisions sharing a counter and producing P04 instead of C01)
            try:
                _np = rev_elem.LookupParameter('Numbering')
                _seq_key = _np.AsString() if (_np and _np.HasValue) else ''
            except Exception:
                _seq_key = ''
            if not _seq_key:
                _seq_key = rev_elem.Description or '_default'
            _seq_counters[_seq_key] = _seq_counters.get(_seq_key, 0) + 1
            code = _revision_code(doc, sheet, rev_elem, _seq_counters[_seq_key])
            try:
                _rid = rev_id.IntegerValue
            except AttributeError:
                _rid = int(rev_id.Value)
            sheet_revisions.append({
                'rev_id':      _rid,
                'date':        date_str,
                'code':        code,
                'description': rev_elem.Description or '',
                'issued_by':   rev_elem.IssuedBy or '',
            })

        result.append({
            'sheet_type':           sheet_type or 'UNCATEGORISED',
            'project':              project_info['project_number'],
            'originator':           originator,
            'functional_breakdown': zone_building,
            'spatial_breakdown':    level,
            'form':                 file_type,
            'discipline':           discipline,
            'number':               sheet_number,
            'title':                sheet_name,
            'size':                 size,
            'scale':                scale,
            'revisions':            sheet_revisions,
        })

    # Sort: group by sheet_type then by sheet number
    def _sort_key(s):
        num = s['number']
        # Extract trailing digits for numeric sort
        digits = re.sub(r'[^0-9]', '', num)
        return (s['sheet_type'], int(digits) if digits else 0, num)

    result.sort(key=_sort_key)
    return result


def collect_issue_dates(sheets_data):
    """
    Return a sorted list of unique issue date strings across all sheets,
    preserving the real date order.

    Returns list of date strings (as they appear in Revit).
    Where two revisions share the same date string, they are kept as
    separate entries distinguished by (date, issued_by) tuple.
    """
    seen = OrderedDict()
    for sheet in sheets_data:
        for rev in sheet['revisions']:
            if not rev['date']:
                continue  # already filtered in get_sheets_data, but guard here too
            key = (rev['date'], rev['issued_by'])
            if key not in seen:
                seen[key] = rev['date']

    # Sort by parsing the date string DD/MM/YY or DD/MM/YYYY
    def _date_sort(key):
        from datetime import datetime  # noqa: PLC0415
        date_str = str(key[0])
        for fmt in ('%d/%m/%y', '%d/%m/%Y', '%d.%m.%y', '%d.%m.%Y',
                    '%m/%d/%y', '%m/%d/%Y', '%Y-%m-%d'):
            try:
                return datetime.strptime(date_str, fmt)
            except (ValueError, TypeError):
                pass
        return datetime.min  # always datetime — IronPython can't compare datetime vs str

    sorted_keys = sorted(seen.keys(), key=_date_sort)
    return sorted_keys  # list of (date_str, issued_by) tuples
