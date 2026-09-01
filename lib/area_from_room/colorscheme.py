# -*- coding: utf-8 -*-
"""Colour the area plan to match the floor plan's room colour scheme.

Revit's API can't create a colour scheme from nothing and can't change which
parameter a scheme colours by (ColorFillScheme.ParameterDefinition is
read-only). So this needs ONE area colour scheme, coloured by the matching
parameter (e.g. "Name"), to exist under the chosen area scheme — created once in
the UI. Given that, the tool:

  * finds it (matched by the scheme's parameter — id first, then a normalised
    name, so the Rooms/Areas "Name" built-in lines up);
  * writes the room scheme's value->colour entries into it (valid now the
    parameter matches);
  * applies it to the area plan.

Idempotent: safe to run on every conversion and re-run — it just re-writes the
same colours. As more plans are converted, any extra room values simply get
written too.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, ColorFillScheme, ColorFillSchemeEntry,
    ElementId, BuiltInCategory, StorageType,
)

_ROOMS = ElementId(BuiltInCategory.OST_Rooms)
_AREAS = ElementId(BuiltInCategory.OST_Areas)


def _eid(x):
    return getattr(x, 'Value', None) or getattr(x, 'IntegerValue', None)


def _hex(color):
    try:
        return '#{:02X}{:02X}{:02X}'.format(
            int(color.Red), int(color.Green), int(color.Blue))
    except Exception:
        return '?'


def _param_name(doc, scheme):
    try:
        pid = scheme.ParameterDefinition
    except Exception:
        return None
    el = doc.GetElement(pid)
    if el is not None:
        try:
            return el.Name
        except Exception:
            return None
    try:
        from Autodesk.Revit.DB import LabelUtils, BuiltInParameter
        import System
        bip = System.Enum.ToObject(BuiltInParameter, _eid(pid))
        return LabelUtils.GetLabelFor(bip)
    except Exception:
        return None


def _norm(name):
    """'Room Name'/'Area Name'/'Name' all normalise to 'name'."""
    if not name:
        return ''
    return name.lower().replace('room', '').replace('area', '').strip()


def _entries_summary(scheme):
    out = []
    try:
        for e in scheme.GetEntries():
            out.append('{} = {}'.format(e.GetStringValue(), _hex(e.Color)))
    except Exception:
        pass
    return out


def _clone_entry(src_entry, storage):
    e = ColorFillSchemeEntry(storage)
    try:
        e.Color = src_entry.Color
    except Exception:
        pass
    for attr in ('FillPatternId', 'IsVisible', 'IsInRange'):
        try:
            setattr(e, attr, getattr(src_entry, attr))
        except Exception:
            pass
    try:
        if storage == StorageType.String:
            e.SetStringValue(src_entry.GetStringValue())
        elif storage == StorageType.Integer:
            e.SetIntegerValue(src_entry.GetIntegerValue())
        elif storage == StorageType.Double:
            e.SetDoubleValue(src_entry.GetDoubleValue())
        elif storage == StorageType.ElementId:
            e.SetElementIdValue(src_entry.GetElementIdValue())
    except Exception:
        pass
    return e


def _to_ilist(py_list):
    from System.Collections.Generic import List
    lst = List[ColorFillSchemeEntry]()
    for e in py_list:
        lst.Add(e)
    return lst


def _write_entries(chosen, room_entries):
    """Write the room colours into the area scheme. Batch first; fall back to
    per-entry Add/Update so one bad value can't lose them all."""
    storage = chosen.StorageType
    try:
        chosen.SetEntries(_to_ilist([_clone_entry(e, storage)
                                     for e in room_entries]))
        return len(room_entries)
    except Exception:
        pass
    existing = set()
    try:
        existing = set(e.GetStringValue() for e in chosen.GetEntries())
    except Exception:
        pass
    applied = 0
    for e in room_entries:
        try:
            ne = _clone_entry(e, storage)
            if e.GetStringValue() in existing:
                chosen.UpdateEntry(ne)
            else:
                chosen.AddEntry(ne)
            applied += 1
        except Exception:
            pass
    return applied


def _report_missing(report, src_scheme, src_param, area_scheme):
    scheme_name = area_scheme.Name if area_scheme else 'the area scheme'
    report.colour_action([
        'Open the new area plan.',
        'On the **Annotate** tab, click **Colour Fill → Colour Fill '
        'Legend**, then click once in the view to place a legend.',
        'In the dialog, set **Space Type** to "{}" and pick **<New>** for the '
        'colour scheme.'.format(scheme_name),
        'In **Edit Colour Scheme**, set **Colour** to **Name**, keep **By '
        'value**, name it "Area Name", and click OK.',
        'Delete the legend if you don\'t want it, then run this tool again.',
    ])


def replicate_color_scheme(doc, src, dst, area_scheme, report):
    try:
        src_id = src.GetColorFillSchemeId(_ROOMS)
    except Exception:
        src_id = ElementId.InvalidElementId
    if src_id is None or src_id == ElementId.InvalidElementId:
        report.note('Floor plan has no room colour scheme — nothing to copy.')
        return

    src_scheme = doc.GetElement(src_id)
    src_param = _param_name(doc, src_scheme)
    try:
        target_pid = _eid(src_scheme.ParameterDefinition)
    except Exception:
        target_pid = None

    # Area colour schemes under the chosen area scheme.
    candidates = []
    for c in FilteredElementCollector(doc).OfClass(ColorFillScheme):
        try:
            if _eid(c.CategoryId) != _eid(_AREAS):
                continue
            if area_scheme is not None and \
                    _eid(c.AreaSchemeId) != _eid(area_scheme.Id):
                continue
            candidates.append(c)
        except Exception:
            continue

    chosen = None
    for c in candidates:
        try:
            if target_pid is not None and \
                    _eid(c.ParameterDefinition) == target_pid:
                chosen = c
                break
        except Exception:
            continue
    if chosen is None:
        for c in candidates:
            if _norm(_param_name(doc, c)) == _norm(src_param):
                chosen = c
                break

    if chosen is None:
        _report_missing(report, src_scheme, src_param, area_scheme)
        return

    room_entries = list(src_scheme.GetEntries())
    applied = _write_entries(chosen, room_entries)
    try:
        dst.SetColorFillSchemeId(_AREAS, chosen.Id)
        report.note('Colours applied to match the floor plan.')
    except Exception as ex:
        report.warn('Colour scheme "{}" prepared but not applied: {}'
                    .format(chosen.Title, ex))
