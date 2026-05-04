# -*- coding: utf-8 -*-
"""
Per-project persistent settings.

Primary storage: JSON sidecar file next to the .rvt
  Path: <same folder as .rvt>/<rvt_name>.lb-settings.json
  Works across users on a shared network drive.

Fallback: Revit Extensible Storage
  Saved inside the .rvt file; requires the model to be saved to persist.
"""

import os
import io
import json
import copy

# Fixed GUID — do NOT change after first deployment
_SCHEMA_GUID   = '6F3A1B2C-4D5E-4F60-8A9B-1C2D3E4F5061'
_SCHEMA_NAME   = 'LBIssueRegisterSettings'
_FIELD_NAME    = 'SettingsJson'
_STORAGE_NAME  = 'LBIssueRegisterStorage'

_DEFAULT_SETTINGS = {
    'title_block': {
        'subject':      '',
        'drawn_by':     '',
        'checked_by':   '',
        'approved_by':  '',
    },
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


# ── Sidecar JSON helpers ──────────────────────────────────────────────────────

def _sidecar_path(doc):
    """Return path to the JSON sidecar file, or None if the model is unsaved."""
    rvt_path = doc.PathName
    if not rvt_path:
        return None
    return os.path.splitext(rvt_path)[0] + '.lb-settings.json'


def _load_sidecar(doc):
    path = _sidecar_path(doc)
    if not path or not os.path.exists(path):
        return None
    try:
        with io.open(path, 'r', encoding='utf-8') as fh:
            return json.loads(fh.read())
    except Exception as exc:
        print('LB Issue Register — could not read sidecar: {}'.format(exc))
        return None


def _save_sidecar(doc, settings):
    path = _sidecar_path(doc)
    if not path:
        return
    try:
        with io.open(path, 'w', encoding='utf-8') as fh:
            fh.write(json.dumps(settings, ensure_ascii=False, indent=2))
    except Exception as exc:
        print('LB Issue Register — could not write sidecar: {}'.format(exc))


# ── Extensible Storage helpers ────────────────────────────────────────────────

def _get_or_create_schema():
    from Autodesk.Revit.DB.ExtensibleStorage import (  # noqa: PLC0415
        Schema, SchemaBuilder, AccessLevel
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
    fb = builder.AddSimpleField(_FIELD_NAME, System.String)
    fb.SetDocumentation('JSON blob for LB Issue Register settings')
    return builder.Finish()


def _find_storage_element(doc):
    from Autodesk.Revit.DB import FilteredElementCollector, DataStorage  # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Schema               # noqa: PLC0415
    import System                                                         # noqa: PLC0415

    guid   = System.Guid(_SCHEMA_GUID)
    schema = Schema.Lookup(guid)
    if schema is None:
        return None

    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        entity = ds.GetEntity(schema)
        if entity.IsValid():
            return ds
    return None


def _load_extensible(doc):
    try:
        schema = _get_or_create_schema()
        ds     = _find_storage_element(doc)
        if ds is None:
            return None
        entity   = ds.GetEntity(schema)
        import System as _Sys  # noqa: PLC0415
        json_str = entity.Get[_Sys.String](_FIELD_NAME)
        if not json_str:
            return None
        return json.loads(json_str)
    except Exception as exc:
        print('LB Issue Register — extensible storage read error: {}'.format(exc))
        return None


def _save_extensible(doc, settings):
    from Autodesk.Revit.DB import DataStorage                    # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Entity       # noqa: PLC0415
    import System as _Sys                                         # noqa: PLC0415

    try:
        schema = _get_or_create_schema()
        ds     = _find_storage_element(doc)
        if ds is None:
            ds      = DataStorage.Create(doc)
            ds.Name = _STORAGE_NAME
        entity = Entity(schema)
        entity.Set[_Sys.String](_FIELD_NAME, json.dumps(settings, ensure_ascii=False))
        ds.SetEntity(entity)
    except Exception as exc:
        import traceback
        print('LB Issue Register — extensible storage write error: {}'.format(exc))
        traceback.print_exc()
        raise


# ── Merge helper ──────────────────────────────────────────────────────────────

def _merge_defaults(saved, defaults):
    for key, val in defaults.items():
        if key not in saved:
            saved[key] = val
        elif key == 'recipients' and isinstance(val, list):
            # Add any default recipients not already present (keeps custom ones too)
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
    """
    Load settings. Tries sidecar JSON first (cross-user), then extensible storage.
    Returns defaults if neither has data.
    """
    defaults = copy.deepcopy(_DEFAULT_SETTINGS)

    # Primary: sidecar JSON (works for all users with file access)
    saved = _load_sidecar(doc)
    if saved is not None:
        return _merge_defaults(saved, defaults)

    # Fallback: extensible storage (single-user, requires model save to persist)
    saved = _load_extensible(doc)
    if saved is not None:
        return _merge_defaults(saved, defaults)

    return defaults


def save_settings(doc, settings):
    """
    Persist settings. Writes sidecar JSON (primary) and extensible storage (backup).
    Must be called inside an open Revit Transaction for the extensible storage write.
    """
    # Primary: sidecar JSON — always attempt regardless of transaction state
    _save_sidecar(doc, settings)

    # Backup: extensible storage — requires an open Transaction (caller's responsibility)
    _save_extensible(doc, settings)
