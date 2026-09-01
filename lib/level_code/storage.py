# -*- coding: utf-8 -*-
"""Persist the Level -> code map in the model, so it is entered only once.

Stored as a JSON blob on ProjectInformation via the shared Extensible Storage
manager. Keyed by Level.UniqueId (stable, survives renames), so a level the
user renames keeps its code and a newly-added level simply isn't in the map yet
(and falls back to a name guess).
"""

from lb_shared.extensible_storage import ExtensibleStorageManager

# Fixed schema GUID — generated once, never change it.
_store = ExtensibleStorageManager(
    schema_guid='B2F3C1A4-7D6E-4A9B-9C21-5E8F0A3D2B10',
    schema_name='LBSheetLevelCodes',
)


def load_map(doc):
    """Return {level_uniqueid: code}; empty dict when nothing saved yet."""
    data = _store.load(doc)
    if isinstance(data, dict):
        return data.get('levels') or {}
    return {}


def save_map(doc, level_map):
    """Persist {level_uniqueid: code}. Must run inside an open Transaction."""
    _store.save(doc, {'levels': level_map})
