# -*- coding: utf-8 -*-
"""Tag areas whose corresponding room was tagged in the floor plan.

The area tag family/type is chosen by the user (the room tag family rarely has
an area-tag equivalent). An area is tagged only if its room carried a room tag;
each tag is placed at the centre of its area with no leader (placing at the room
tag head position tended to stack every tag on one point with a leader).
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, UV, FamilySymbol,
)


def _room_id_of(tag):
    for attr in ('Room', 'TaggedLocalRoomId'):
        try:
            val = getattr(tag, attr)
            if val is None:
                continue
            return val.Id if hasattr(val, 'Id') else val
        except Exception:
            continue
    return None


def _eid(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _tagged_room_ids(doc, src_view):
    """Set of room-id ints for rooms that carry a tag in the source view."""
    ids = set()
    for tag in (FilteredElementCollector(doc, src_view.Id)
                .OfCategory(BuiltInCategory.OST_RoomTags)
                .WhereElementIsNotElementType()):
        rid = _room_id_of(tag)
        if rid is not None:
            ids.add(_eid(rid))
    return ids


def _area_uv(area):
    try:
        p = area.Location.Point
        return UV(p.X, p.Y)
    except Exception:
        return None


def _num(area):
    try:
        return area.Number
    except Exception:
        return '?'


def tag_areas(doc, src_view, area_plan, room_to_area, tag_type_id, report):
    """Tag every area whose room was tagged, centred in the area, no leader."""
    tagged = _tagged_room_ids(doc, src_view)
    if not tagged:
        report.note('No room tags in the source view — no areas tagged.')
        return 0

    symbol = doc.GetElement(tag_type_id)
    if isinstance(symbol, FamilySymbol) and not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()

    placed = 0
    for room, area in room_to_area.items():
        if _eid(room.Id) not in tagged:
            continue
        uv = _area_uv(area)
        if uv is None:
            continue
        try:
            tag = doc.Create.NewAreaTag(area_plan, area, uv)
            try:
                tag.HasLeader = False
            except Exception:
                pass
            try:
                tag.ChangeTypeId(tag_type_id)
            except Exception:
                pass
            placed += 1
        except Exception as ex:
            report.warn('Could not tag area {}: {}'.format(_num(area), ex))

    report.count('areas tagged', placed)
    return placed
