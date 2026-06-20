# -*- coding: utf-8 -*-
"""
Shoebox per-room state in the Revit file (Extensible Storage) + worksharing
ownership.

Mirrors lb_shared.extensible_storage, but the entity is attached to the **Room
element itself** rather than a single DataStorage element. That means:
  * the saved state travels with the .rvt (portable, no shared-drive needed);
  * ownership is per-room — Revit's worksharing checkout of the Room serialises
    edits, so two users can't make clashing changes to the same room, while
    different rooms can be edited concurrently.

Schema GUID is fixed forever — Revit identifies the schema by it across
sessions and users. Never change it.
"""
import json

_SCHEMA_GUID = "B7E2B8A0-3C4D-4E5F-9A1B-2C3D4E5F6071"
_SCHEMA_NAME = "LBShoeboxRoomState"
_FIELD = "Data"
_schema_cache = None


def _get_schema():
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache
    from Autodesk.Revit.DB.ExtensibleStorage import Schema, SchemaBuilder, AccessLevel
    import System
    guid = System.Guid(_SCHEMA_GUID)
    existing = Schema.Lookup(guid)
    if existing:
        _schema_cache = existing
        return existing
    b = SchemaBuilder(guid)
    b.SetSchemaName(_SCHEMA_NAME)
    b.SetReadAccessLevel(AccessLevel.Public)
    b.SetWriteAccessLevel(AccessLevel.Public)
    b.AddSimpleField(_FIELD, System.String)
    _schema_cache = b.Finish()
    return _schema_cache


def read_room_state(room):
    """Saved state dict stored on the room, or None. No transaction needed."""
    try:
        import System as _Sys
        schema = _get_schema()
        entity = room.GetEntity(schema)
        if entity is None or not entity.IsValid():
            return None
        raw = entity.Get[_Sys.String](_FIELD)
        return json.loads(raw) if raw else None
    except Exception as exc:
        print("[LBShoebox] read_room_state error: {0}".format(exc))
        return None


def write_room_state(room, state):
    """Write state dict onto the room. MUST be called inside an open Transaction."""
    from Autodesk.Revit.DB.ExtensibleStorage import Entity
    import System as _Sys
    schema = _get_schema()
    entity = Entity(schema)
    entity.Set[_Sys.String](_FIELD, json.dumps(state, ensure_ascii=False))
    room.SetEntity(entity)


def _eid_int(eid):
    return getattr(eid, "Value", None) or getattr(eid, "IntegerValue", None)


def acquire_room(doc, room):
    """Check the Room out from central (workshared models only).
    Returns (can_proceed: bool, owner_name: str)."""
    if not doc.IsWorkshared:
        return True, ""
    try:
        from Autodesk.Revit.DB import WorksharingUtils, CheckoutStatus, ElementId
        from System.Collections.Generic import List
        status = WorksharingUtils.GetCheckoutStatus(doc, room.Id)
        if status == CheckoutStatus.OwnedByOtherUser:
            return False, _owner(doc, room.Id)
        if status != CheckoutStatus.OwnedByCurrentUser:
            ids = List[ElementId]()
            ids.Add(room.Id)
            out = WorksharingUtils.CheckoutElements(doc, ids)
            vals = set(_eid_int(e) for e in out)
            if _eid_int(room.Id) not in vals:
                return False, _owner(doc, room.Id)
    except Exception as exc:
        print("[LBShoebox] acquire_room error: {0}".format(exc))
    return True, ""


def _owner(doc, element_id):
    try:
        from Autodesk.Revit.DB import WorksharingUtils
        return WorksharingUtils.GetWorksharingTooltipInfo(doc, element_id).Owner or "another user"
    except Exception:
        return "another user"
