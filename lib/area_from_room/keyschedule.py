# -*- coding: utf-8 -*-
"""Best-effort replicate room key schedules as area key schedules.

Per room key schedule used by the converted rooms:
  1. create (or reuse) an Area key schedule "<name> (Area)";
  2. add the same value columns (only those parameters bound to Areas);
  3. recreate each key row, setting its Key Name and copying its field values;
  4. point each area at the matching new key.

Key elements are read/captured through a schedule-scoped FilteredElementCollector
(FilteredElementCollector(doc, scheduleId)) — the reliable way to enumerate the
rows of a key schedule. Everything is guarded; shortfalls are reported.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewSchedule, ElementId, BuiltInCategory,
    SectionType,
)

from area_from_room.params import copy_parameters
from area_from_room.param_bridge import copy_bridged, ensure_area_param

_AREAS = ElementId(BuiltInCategory.OST_Areas)
_ROOMS = ElementId(BuiltInCategory.OST_Rooms)


def _eid(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _area_name(name):
    """Rename a Rooms-side label to its Areas equivalent: 'Room'->'Area',
    'Rooms'->'Areas'; falls back to appending ' (Area)' if nothing matched."""
    if not name:
        return name
    out = (name.replace('Rooms', 'Areas').replace('Room', 'Area')
               .replace('rooms', 'areas').replace('room', 'area'))
    return out if out != name else name + ' (Area)'


def _room_key_schedules(doc):
    out = []
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            d = vs.Definition
            if d.IsKeySchedule and _eid(d.CategoryId) == _eid(_ROOMS):
                out.append(vs)
        except Exception:
            continue
    return out


def _key_elements(doc, schedule):
    """Key elements (rows) of a key schedule, via a view-scoped collector."""
    try:
        return list(FilteredElementCollector(doc, schedule.Id)
                    .WhereElementIsNotElementType().ToElements())
    except Exception:
        return []


def _source_field_names(vs):
    names = []
    try:
        d = vs.Definition
        for i in range(d.GetFieldCount()):
            try:
                names.append(d.GetField(i).GetName())
            except Exception:
                pass
    except Exception:
        pass
    return names


def _add_fields(area_sched, field_names, bridge_cache):
    # Columns to add: the source field names that exist for areas, plus the
    # bridged "<name>_Areas" columns for any Rooms-only source field.
    wanted = set(field_names)
    for rname, aname in bridge_cache.items():
        if rname in field_names:
            wanted.add(aname)

    added = 0
    try:
        d = area_sched.Definition
        existing = set()
        for i in range(d.GetFieldCount()):
            try:
                existing.add(d.GetField(i).GetName())
            except Exception:
                pass
        for sf in d.GetSchedulableFields():
            try:
                nm = sf.GetName(area_sched.Document)
            except Exception:
                continue
            if nm in wanted and nm not in existing:
                try:
                    d.AddField(sf)
                    added += 1
                except Exception:
                    pass
    except Exception:
        pass
    return added


def _bridge_builtin_fields(doc, vs, area_sched, bridge_cache, report):
    """For each source key column that is a BUILT-IN room parameter with no area
    equivalent (e.g. Occupancy), create a "<name>_Areas" parameter so it can be
    an area key-schedule column. Non-built-in project params are left for the
    user to add to Areas manually."""
    # Source field name -> parameter id.
    src_fields = {}
    try:
        d = vs.Definition
        for i in range(d.GetFieldCount()):
            try:
                f = d.GetField(i)
                src_fields[f.GetName()] = f.ParameterId
            except Exception:
                pass
    except Exception:
        return

    # Field names the area schedule can already offer.
    area_names = set()
    try:
        for sf in area_sched.Definition.GetSchedulableFields():
            try:
                area_names.add(sf.GetName(doc))
            except Exception:
                pass
    except Exception:
        pass

    src_keys = _key_elements(doc, vs)
    sample = src_keys[0] if src_keys else None
    made = False
    for name, pid in src_fields.items():
        if name in area_names or name in bridge_cache:
            continue
        pidv = _eid(pid)
        if pidv is None or pidv >= 0:
            continue          # only built-in params have a negative id
        if sample is None:
            continue
        rp = sample.LookupParameter(name)
        if rp is None:
            continue
        try:
            data_type = rp.Definition.GetDataType()
        except Exception:
            data_type = None
        aname = ensure_area_param(doc, name, data_type, report)
        if aname:
            bridge_cache[name] = aname
            made = True
    if made:
        doc.Regenerate()


def _find_area_schedule(doc, name):
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if vs.Name == name and vs.Definition.IsKeySchedule and \
                    _eid(vs.Definition.CategoryId) == _eid(_AREAS):
                return vs
        except Exception:
            continue
    return None


def _insert_key(doc, area_sched):
    """Insert one key row and return the new key element (or None)."""
    before = set(_eid(e.Id) for e in _key_elements(doc, area_sched))
    try:
        sd = area_sched.GetTableData().GetSectionData(SectionType.Body)
        inserted = False
        for idx in (sd.LastRowNumber + 1, sd.FirstRowNumber, 0):
            try:
                sd.InsertRow(idx)
                inserted = True
                break
            except Exception:
                continue
        if not inserted:
            return None
        doc.Regenerate()
    except Exception:
        return None
    new_ids = set(_eid(e.Id) for e in _key_elements(doc, area_sched)) - before
    if len(new_ids) != 1:
        return None
    return doc.GetElement(ElementId(list(new_ids)[0]))


def replicate_key_schedules(doc, rooms, room_to_area, cache, report,
                            bridge_cache=None):
    bridge_cache = bridge_cache or {}
    for vs in _room_key_schedules(doc):
        try:
            key_param = vs.KeyScheduleParameterName
        except Exception:
            continue

        used = {}   # room -> source key id (int)
        for room in rooms:
            p = room.LookupParameter(key_param)
            if p is None:
                continue
            try:
                kid = p.AsElementId()
            except Exception:
                kid = None
            if kid and kid != ElementId.InvalidElementId:
                used[room] = _eid(kid)
        if not used:
            continue

        cache_key = _eid(vs.Id)
        entry = cache.get(cache_key)
        if entry is None:
            entry = _build(doc, vs, used, report, bridge_cache)
            cache[cache_key] = entry
        if entry is None:
            continue
        area_param, mapping = entry

        assigned = 0
        for room, src_kid in used.items():
            area = room_to_area.get(room)
            if area is None:
                continue
            akid = mapping.get(src_kid)
            if akid is None:
                continue
            ap = area.LookupParameter(area_param)
            if ap is None or ap.IsReadOnly:
                continue
            try:
                ap.Set(ElementId(akid))
                assigned += 1
            except Exception:
                pass
        report.count('area keys assigned', assigned)


def _build(doc, vs, used, report, bridge_cache):
    target_name = _area_name(vs.Name)
    area_sched = _find_area_schedule(doc, target_name)
    reused = area_sched is not None

    if not reused:
        try:
            area_sched = ViewSchedule.CreateKeySchedule(doc, _AREAS)
            area_sched.Name = target_name
        except Exception as ex:
            report.warn('Key schedule "{}" not created: {}. Do it manually.'
                        .format(vs.Name, ex))
            return None
        try:
            area_sched.KeyScheduleParameterName = \
                _area_name(vs.KeyScheduleParameterName)
        except Exception:
            pass

    try:
        area_param = area_sched.KeyScheduleParameterName
    except Exception:
        area_param = None
    if not area_param:
        return None

    # Built-in columns (e.g. Occupancy) can't be added to Areas — create
    # "<name>_Areas" for those so they can still be schedule columns.
    _bridge_builtin_fields(doc, vs, area_sched, bridge_cache, report)

    field_names = set(_source_field_names(vs))
    added = _add_fields(area_sched, field_names, bridge_cache)
    doc.Regenerate()

    # Existing area keys, indexed by their Key Name, so a re-run reuses them.
    area_by_name = {}
    for k in _key_elements(doc, area_sched):
        try:
            area_by_name[k.Name] = k
        except Exception:
            pass

    # Mirror the WHOLE room key schedule (every key, whether or not it is used
    # on this plan), so later runs on other plans reuse the same keys.
    mapping = {}
    made = 0
    for src_key in _key_elements(doc, vs):
        skid = _eid(src_key.Id)
        try:
            src_name = src_key.Name
        except Exception:
            src_name = None

        area_key = area_by_name.get(src_name) if src_name else None
        if area_key is None:
            area_key = _insert_key(doc, area_sched)
            if area_key is None:
                continue
            try:
                if src_name:
                    area_key.Name = src_name
                    area_by_name[src_name] = area_key
            except Exception:
                pass
            made += 1
        # Copy the key's field values every run (idempotent), so a re-run fills
        # them in once Rooms-only columns have been extended to Areas. Then the
        # bridged built-ins (e.g. Occupancy -> Occupancy_Areas).
        copy_parameters(src_key, area_key)
        copy_bridged(src_key, area_key, bridge_cache)
        mapping[skid] = _eid(area_key.Id)

    if not reused:
        report.count('key schedules', 1)
    report.count('keys created', made)
    if added == 0 and _source_field_names(vs):
        report.warn('Key schedule "{}": no matching area fields — its columns '
                    'are Rooms-only parameters, so area keys carry no values. '
                    'Bind those parameters to Areas as well to fix.'
                    .format(vs.Name))
    return (area_param, mapping)
