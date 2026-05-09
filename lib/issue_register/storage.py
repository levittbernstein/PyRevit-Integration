# -*- coding: utf-8 -*-
"""
Per-project persistent settings using Revit Extensible Storage.

Extensible Storage lives inside the .rvt file and propagates to other users
through the normal worksharing sync cycle.  It is the only storage mechanism
used so that the plugin works identically on local network drives and on
BIM 360 / Autodesk Construction Cloud, where there is no local file path
for a sidecar approach.

Workshared locking
------------------
A second DataStorage element (separate schema) acts as a soft lock so that
only one user has the export dialog open at a time.  The lock is written
when the dialog opens and cleared when it closes (cancel or export).
Because Revit worksharing is sync-based, the lock is only as current as the
last sync-to-central.  A staleness timeout of _LOCK_TIMEOUT_HOURS prevents
a crashed session from permanently blocking other users.
"""

import copy
import json
import time

# ── Settings schema ───────────────────────────────────────────────────────────
# Fixed GUIDs — do NOT change after first deployment.
_SCHEMA_GUID    = '6F3A1B2C-4D5E-4F60-8A9B-1C2D3E4F5061'
_SCHEMA_NAME    = 'LBIssueRegisterSettings'
_FIELD_NAME     = 'SettingsJson'
_STORAGE_NAME   = 'LBIssueRegisterStorage'

# ── Lock schema ───────────────────────────────────────────────────────────────
_LOCK_GUID      = '6F3A1B2C-4D5E-4F60-8A9B-1C2D3E4F5062'
_LOCK_SCHEMA    = 'LBIssueRegisterLock'
_LOCK_FIELD_WHO = 'LockedBy'
_LOCK_FIELD_AT  = 'LockedAt'
_LOCK_EL_NAME   = 'LBIssueRegisterLock'
_LOCK_TIMEOUT_HOURS = 4

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


# ── Shared Extensible Storage helpers ─────────────────────────────────────────

def _make_schema(guid_str, schema_name, fields):
    """Get or create an ES schema.  fields is a list of (name, type_str) pairs."""
    from Autodesk.Revit.DB.ExtensibleStorage import (  # noqa: PLC0415
        Schema, SchemaBuilder, AccessLevel,
    )
    import System  # noqa: PLC0415

    guid = System.Guid(guid_str)
    existing = Schema.Lookup(guid)
    if existing:
        return existing

    builder = SchemaBuilder(guid)
    builder.SetSchemaName(schema_name)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    for field_name, _ in fields:
        builder.AddSimpleField(field_name, System.String)
    return builder.Finish()


def _find_ds(doc, guid_str):
    """Return the DataStorage element that carries a given schema GUID, or None."""
    from Autodesk.Revit.DB import FilteredElementCollector, DataStorage  # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Schema               # noqa: PLC0415
    import System                                                         # noqa: PLC0415

    schema = Schema.Lookup(System.Guid(guid_str))
    if schema is None:
        return None
    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        if ds.GetEntity(schema).IsValid():
            return ds
    return None


# ── Settings storage ──────────────────────────────────────────────────────────

def _settings_schema():
    return _make_schema(_SCHEMA_GUID, _SCHEMA_NAME, [(_FIELD_NAME, 'String')])


def _load_extensible(doc):
    try:
        schema = _settings_schema()
        ds = _find_ds(doc, _SCHEMA_GUID)
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

    schema = _settings_schema()
    ds = _find_ds(doc, _SCHEMA_GUID)
    if ds is None:
        ds = DataStorage.Create(doc)
        ds.Name = _STORAGE_NAME

    entity = Entity(schema)
    entity.Set[_Sys.String](_FIELD_NAME, json.dumps(settings, ensure_ascii=False))
    ds.SetEntity(entity)


# ── Lock storage ──────────────────────────────────────────────────────────────

def _lock_schema():
    return _make_schema(_LOCK_GUID, _LOCK_SCHEMA,
                        [(_LOCK_FIELD_WHO, 'String'), (_LOCK_FIELD_AT, 'String')])


def get_current_user(doc):
    """Return the Revit application username for the current session."""
    try:
        return doc.Application.Username or 'Unknown user'
    except Exception:
        return 'Unknown user'


def check_lock(doc):
    """
    Read the current lock state from Extensible Storage.

    Returns (is_locked, locked_by_username).  A lock older than
    _LOCK_TIMEOUT_HOURS is treated as stale and ignored.
    Only meaningful on workshared models; always returns (False, '') otherwise.
    """
    try:
        if not doc.IsWorkshared:
            return False, ''
        schema = _lock_schema()
        ds = _find_ds(doc, _LOCK_GUID)
        if ds is None:
            return False, ''
        import System as _Sys  # noqa: PLC0415
        entity    = ds.GetEntity(schema)
        locked_by = entity.Get[_Sys.String](_LOCK_FIELD_WHO) or ''
        locked_at = entity.Get[_Sys.String](_LOCK_FIELD_AT)  or ''
        if not locked_by:
            return False, ''
        # Staleness check — locked_at is stored as a Unix timestamp string
        if locked_at:
            try:
                elapsed_hours = (time.time() - float(locked_at)) / 3600.0
                if elapsed_hours > _LOCK_TIMEOUT_HOURS:
                    return False, ''
            except (ValueError, TypeError):
                pass
        return True, locked_by
    except Exception as exc:
        print('LB Issue Register — lock check error: {}'.format(exc))
        return False, ''


def acquire_lock(doc):
    """
    Write the current user's name and a timestamp into the lock element.
    Caller must be inside an open Transaction.
    Does nothing on non-workshared models.
    """
    try:
        if not doc.IsWorkshared:
            return
        from Autodesk.Revit.DB import DataStorage              # noqa: PLC0415
        from Autodesk.Revit.DB.ExtensibleStorage import Entity # noqa: PLC0415
        import System as _Sys                                   # noqa: PLC0415

        schema = _lock_schema()
        ds = _find_ds(doc, _LOCK_GUID)
        if ds is None:
            ds = DataStorage.Create(doc)
            ds.Name = _LOCK_EL_NAME

        entity = Entity(schema)
        entity.Set[_Sys.String](_LOCK_FIELD_WHO, get_current_user(doc))
        entity.Set[_Sys.String](_LOCK_FIELD_AT,  str(time.time()))
        ds.SetEntity(entity)
    except Exception as exc:
        print('LB Issue Register — lock acquire error: {}'.format(exc))


def release_lock(doc):
    """
    Clear the lock.  Caller must be inside an open Transaction.
    Does nothing on non-workshared models or if no lock element exists.
    """
    try:
        if not doc.IsWorkshared:
            return
        schema = _lock_schema()
        ds = _find_ds(doc, _LOCK_GUID)
        if ds is None:
            return
        from Autodesk.Revit.DB.ExtensibleStorage import Entity  # noqa: PLC0415
        import System as _Sys                                     # noqa: PLC0415
        entity = Entity(schema)
        entity.Set[_Sys.String](_LOCK_FIELD_WHO, '')
        entity.Set[_Sys.String](_LOCK_FIELD_AT,  '')
        ds.SetEntity(entity)
    except Exception as exc:
        print('LB Issue Register — lock release error: {}'.format(exc))


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
    """
    _save_extensible(doc, settings)
