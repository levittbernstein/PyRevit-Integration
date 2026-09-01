# -*- coding: utf-8 -*-
"""Place one Area per room and copy its data across.

Idempotent: an area whose Number already exists in the target plan is left
alone, so re-running never duplicates areas. Two areas in the same place with
different Numbers are both kept — only a matching Number is treated as "already
done".
"""

from Autodesk.Revit.DB import UV, BuiltInCategory, FilteredElementCollector

from area_from_room.params import copy_parameters


def _num(spatial):
    try:
        return spatial.Number
    except Exception:
        return None


def _location_uv(room):
    try:
        p = room.Location.Point
        return UV(p.X, p.Y)
    except Exception:
        return None


def existing_area_numbers(doc, area_plan):
    """Numbers of areas already shown in the target area plan."""
    nums = set()
    for a in (FilteredElementCollector(doc, area_plan.Id)
              .OfCategory(BuiltInCategory.OST_Areas)
              .WhereElementIsNotElementType()):
        n = _num(a)
        if n is not None:
            nums.add(n)
    return nums


def create_areas(doc, rooms, area_plan, key_param_names, report,
                 existing_numbers=None):
    """Create an Area per room (boundaries must already exist + be regenerated),
    then copy parameters. Returns {room: area} for areas newly created."""
    existing = set(existing_numbers or [])
    room_to_area = {}
    already = 0

    for room in rooms:
        if _num(room) in existing:
            already += 1
            continue
        uv = _location_uv(room)
        if uv is None:
            report.warn('Room {} has no location point — no area placed.'
                        .format(_num(room)))
            continue
        try:
            area = doc.Create.NewArea(area_plan, uv)
            room_to_area[room] = area
        except Exception as ex:
            report.warn('Could not place area for room {}: {}'
                        .format(_num(room), ex))

    doc.Regenerate()   # let the new areas compute against the boundaries

    copied_total = 0
    unenclosed = 0
    for room, area in room_to_area.items():
        copied, _skipped = copy_parameters(room, area,
                                            skip_names=key_param_names)
        copied_total += copied
        try:
            if area.Area <= 0:
                unenclosed += 1
        except Exception:
            unenclosed += 1

    report.count('areas', len(room_to_area))
    if already:
        report.count('areas already present', already)
    report.count('params copied', copied_total)
    if unenclosed:
        report.warn('{} area(s) came out not-enclosed — check for boundary '
                    'gaps on those rooms.'.format(unenclosed))

    return room_to_area
