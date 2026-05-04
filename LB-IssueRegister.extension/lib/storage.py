# -*- coding: utf-8 -*-
"""
Per-project persistent settings via Revit Extensible Storage.

Settings are stored as a single JSON string on a DataStorage element
identified by the schema GUID below.  Each .rvt file therefore carries
its own settings independently.
"""

import json

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
        {'name': 'Client',             'row': 4},
        {'name': 'Contractor',         'row': 5},
        {'name': 'Project Manager',    'row': 6},
        {'name': 'Structures',         'row': 7},
        {'name': 'MEP',                'row': 8},
        {'name': 'Landscape',          'row': 9},
        {'name': 'CDM PD',             'row': 10},
        {'name': 'Extranet upload',    'row': 11},
    ],
    # issues: dict mapping date_key → {recipient_name: format_code}
    # date_key = "<date_str>||<issued_by>"
    'issues': {},
}


def _get_or_create_schema():
    """Return the ExtensibleStorage Schema, creating it if necessary."""
    from Autodesk.Revit.DB.ExtensibleStorage import (  # noqa: PLC0415
        Schema, SchemaBuilder, FieldBuilder, AccessLevel
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
    """Return the DataStorage element for our schema, or None."""
    from Autodesk.Revit.DB import FilteredElementCollector, DataStorage  # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Schema  # noqa: PLC0415
    import System  # noqa: PLC0415

    guid = System.Guid(_SCHEMA_GUID)
    schema = Schema.Lookup(guid)
    if schema is None:
        return None

    for ds in FilteredElementCollector(doc).OfClass(DataStorage):
        entity = ds.GetEntity(schema)
        if entity.IsValid():
            return ds
    return None


def load_settings(doc):
    """
    Load settings from extensible storage.
    Returns the settings dict (defaults if not yet saved).
    """
    import copy
    defaults = copy.deepcopy(_DEFAULT_SETTINGS)

    try:
        schema = _get_or_create_schema()
        ds = _find_storage_element(doc)
        if ds is None:
            return defaults

        entity = ds.GetEntity(schema)
        json_str = entity.Get[str](_FIELD_NAME)
        if not json_str:
            return defaults

        saved = json.loads(json_str)

        # Merge: keep defaults for any keys missing in saved data
        for top_key, top_val in defaults.items():
            if top_key not in saved:
                saved[top_key] = top_val
            elif isinstance(top_val, dict):
                for sub_key, sub_val in top_val.items():
                    if sub_key not in saved[top_key]:
                        saved[top_key][sub_key] = sub_val

        return saved

    except Exception as exc:
        import traceback
        print('LB Issue Register — could not load settings: {}'.format(exc))
        traceback.print_exc()
        return defaults


def save_settings(doc, settings):
    """
    Persist settings dict to extensible storage.
    Must be called inside an open Revit Transaction.
    """
    from Autodesk.Revit.DB import DataStorage  # noqa: PLC0415
    from Autodesk.Revit.DB.ExtensibleStorage import Entity  # noqa: PLC0415

    try:
        schema = _get_or_create_schema()
        ds = _find_storage_element(doc)

        if ds is None:
            ds = DataStorage.Create(doc)
            ds.Name = _STORAGE_NAME

        entity = Entity(schema)
        entity.Set[str](_FIELD_NAME, json.dumps(settings, ensure_ascii=False))
        ds.SetEntity(entity)

    except Exception as exc:
        import traceback
        print('LB Issue Register — could not save settings: {}'.format(exc))
        traceback.print_exc()
        raise
