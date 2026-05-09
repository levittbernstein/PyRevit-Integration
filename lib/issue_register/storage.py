# -*- coding: utf-8 -*-
"""
Per-project persistent settings using Revit Extensible Storage.

Extensible Storage lives inside the .rvt file and propagates to other users
through the normal worksharing sync cycle.  It is the only storage mechanism
used so that the plugin works identically on local network drives and on
BIM 360 / Autodesk Construction Cloud, where there is no local file path
for a sidecar approach.

Workshared ownership
--------------------
The settings DataStorage element acts as the natural lock.  Before showing
the dialog, the plugin calls WorksharingUtils.CheckoutElements() to reserve
it from the central server.  If another user already owns it, Revit returns
CheckoutStatus.OwnedByOtherUser and we show a message naming them.

Ownership is released when the current user syncs-to-central, which is the
standard Revit worksharing contract — the same mechanism that prevents two
people from editing the same wall simultaneously.
"""

import copy
import json


# ── Settings schema ───────────────────────────────────────────────────────────
# Fixed GUID — do NOT change after first deployment.
_SCHEMA_GUID   = '6F3A1B2C-4D5E-4F60-8A9B-1C2D3E4F5061'
_SCHEMA_NAME   = 'LBIssueRegisterSettings'
_FIELD_NAME    = 'SettingsJson'
_STORAGE_NAME  = 'LBIssueRegisterStorage'

_DEFAULT_SETTINGS = {
    'recipients': [
        {'name': 'Client'},
        {'name': 'Contractor'},
        {'name': 'Project Manager'},
        {'name': 'Structures'},
        {'name': 'MEP'},
        {'name': 'Extranet upload'},
    ],
    'issues': {},
}


# ── Extensible Storage helpers ────────────────────────────────────────────────

def _get_or_create_schema():
    from Autodesk.Revit.DB.ExtensibleStorage import (  # noqa: PLC0415
        Schema, SchemaBuilder, AccessLevel,
    )
    import System  # noqa: PLC0415

    guid = System.Guid(_SCHEMA_GUID)
    existing = Schema.Lookup(guid)
    if existing:
        return existing

    builder = SchemaBuilder(guid)
    builder.SetSchemaName(_SCHEMA_NAME)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.AddSimpleField(_FIELD_NAME, System.String)
    return builder.Finish()


def _find_storage_element(doc):
    """Return the settings DataStorage element, or None if it doesn't exist yet."""
    from Autodesk.Revit.DB import FilteredElementCollector, DataStorage  # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Schema               # noqa: PLC0415
    import System                                                         # noqa: PLC0415

    schema = Schema.Lookup(System.Guid(_SCHEMA_GUID))
    if schema is None:
        return None
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        if ds.GetEntity(schema).IsValid():
            return ds
    return None


def _element_id_value(eid):
    """Return the integer value of an ElementId (compatible with Revit 2024+)."""
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


# ── Worksharing ownership ─────────────────────────────────────────────────────

def check_and_acquire_ownership(doc):
    """
    For workshared models, check whether the settings DataStorage element is
    already owned by another user and, if not, explicitly check it out from
    the central server using WorksharingUtils.CheckoutElements.

    Returns (can_proceed: bool, owner_name: str).

    - (True,  '')        — not workshared, element doesn't exist yet, or
                           successfully checked out for the current user.
    - (False, 'username') — element is owned by 'username'; caller should
                            show a message and abort.

    Ownership is released when the current user next syncs to central, which
    is the standard Revit worksharing contract.
    """
    if not doc.IsWorkshared:
        return True, ''

    ds = _find_storage_element(doc)
    if ds is None:
        # First use — the element will be created during the save Transaction.
        # No one else can own an element that doesn't exist yet.
        return True, ''

    try:
        from Autodesk.Revit.DB import WorksharingUtils, CheckoutStatus  # noqa: PLC0415
        from System.Collections.Generic import List                      # noqa: PLC0415
        from Autodesk.Revit.DB import ElementId                         # noqa: PLC0415

        # Fast local check based on last-sync state.
        status = WorksharingUtils.GetCheckoutStatus(doc, ds.Id)
        if status == CheckoutStatus.OwnedByOtherUser:
            return False, _get_owner_name(doc, ds.Id)

        # If not already owned by us, ask the central server to reserve it.
        if status != CheckoutStatus.OwnedByCurrentUser:
            ids = List[ElementId]()
            ids.Add(ds.Id)
            checked_out = WorksharingUtils.CheckoutElements(doc, ids)
            checked_values = set(
                _element_id_value(eid) for eid in checked_out
            )
            if _element_id_value(ds.Id) not in checked_values:
                # Central server reported it is owned by someone else.
                return False, _get_owner_name(doc, ds.Id)

    except Exception as exc:
        # Network error, unsupported API, or non-workshared cloud model variant.
        # Proceed — the save Transaction will fail with a Revit-level error if
        # there is a genuine conflict, which is still safe.
        print('LB Issue Register — ownership check error: {}'.format(exc))

    return True, ''


def _get_owner_name(doc, element_id):
    """Return the Revit username of whoever currently owns an element."""
    try:
        from Autodesk.Revit.DB import WorksharingUtils  # noqa: PLC0415
        info = WorksharingUtils.GetWorksharingTooltipInfo(doc, element_id)
        return info.Owner or 'another user'
    except Exception:
        return 'another user'


# ── Settings read / write ─────────────────────────────────────────────────────

def _load_extensible(doc):
    try:
        schema = _get_or_create_schema()
        ds = _find_storage_element(doc)
        if ds is None:
            return None
        import System as _Sys  # noqa: PLC0415
        json_str = ds.GetEntity(schema).Get[_Sys.String](_FIELD_NAME)
        return json.loads(json_str) if json_str else None
    except Exception as exc:
        print('LB Issue Register — settings read error: {}'.format(exc))
        return None


def _save_extensible(doc, settings):
    """Write settings JSON.  Caller must be inside an open Transaction."""
    from Autodesk.Revit.DB import DataStorage                    # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Entity       # noqa: PLC0415
    import System as _Sys                                         # noqa: PLC0415

    schema = _get_or_create_schema()
    ds = _find_storage_element(doc)
    if ds is None:
        ds = DataStorage.Create(doc)
        ds.Name = _STORAGE_NAME

    entity = Entity(schema)
    entity.Set[_Sys.String](_FIELD_NAME, json.dumps(settings, ensure_ascii=False))
    ds.SetEntity(entity)


# ── Merge helper ──────────────────────────────────────────────────────────────

def _merge_defaults(saved, defaults):
    for key, val in defaults.items():
        if key not in saved:
            saved[key] = val
        elif key == 'recipients' and isinstance(val, list):
            existing = {r.get('name', '') for r in saved[key]}
            for r in val:
                if r.get('name', '') not in existing:
                    saved[key].append(r)
        elif isinstance(val, dict):
            for sub_key, sub_val in val.items():
                if sub_key not in saved[key]:
                    saved[key][sub_key] = sub_val
    return saved


# ── Public API ────────────────────────────────────────────────────────────────

def load_settings(doc):
    """Load settings from Extensible Storage, falling back to defaults."""
    defaults = copy.deepcopy(_DEFAULT_SETTINGS)
    saved = _load_extensible(doc)
    if saved is not None:
        return _merge_defaults(saved, defaults)
    return defaults


def save_settings(doc, settings):
    """
    Persist settings to Extensible Storage.
    Must be called inside an open Revit Transaction.
    The element remains checked out (owned by the current user) until the
    next sync-to-central, which is the intended worksharing behaviour.
    """
    _save_extensible(doc, settings)
