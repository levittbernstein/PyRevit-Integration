# -*- coding: utf-8 -*-
"""
Issue Register plugin settings — load, save, and worksharing ownership.

All persistence is handled by lb_shared.ExtensibleStorageManager.
Plugin-specific logic here is limited to default values and merging.
"""

import copy
import os
import sys

# ── Make lb_shared importable ─────────────────────────────────────────────────
_LIB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LIB_ROOT not in sys.path:
    sys.path.insert(0, _LIB_ROOT)

from lb_shared.extensible_storage import ExtensibleStorageManager  # noqa: E402

# ── Resolve DataStorage type ──────────────────────────────────────────────────
# 'from Autodesk.Revit.DB import DataStorage' fails in IronPython in this
# environment.  Instead we get Transaction (which does import) and walk to
# DataStorage via its assembly — both types live in RevitAPI.dll.
_DataStorage = None
try:
    import clr as _clr                                          # noqa: E402
    from Autodesk.Revit.DB import Transaction as _Txn          # noqa: E402
    _DataStorage = _clr.GetClrType(_Txn).Assembly.GetType(
        'Autodesk.Revit.DB.DataStorage')
except Exception as _e:
    print('[LBIssueRegister] DataStorage lookup failed: {}'.format(_e))

# ── Storage instance — one per plugin, each with a unique GUID ───────────────
_store = ExtensibleStorageManager(
    schema_guid        = '6F3A1B2C-4D5E-4F60-8A9B-1C2D3E4F5061',  # fixed — do not change
    schema_name        = 'LBIssueRegisterSettings',
    element_name       = 'LBIssueRegisterStorage',
    json_field         = 'SettingsJson',
    data_storage_class = _DataStorage,   # None → falls back to ProjectInformation
)

# ── Default settings ──────────────────────────────────────────────────────────
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
    saved = _store.load(doc)
    if saved is not None:
        return _merge_defaults(saved, defaults)
    return defaults


def save_settings(doc, settings):
    """
    Persist settings. Must be called inside an open Revit Transaction.
    The DataStorage element remains checked out until the next sync-to-central.
    """
    _store.save(doc, settings)


def check_and_acquire_ownership(doc):
    """
    Check whether the settings element is owned by another user and, if not,
    check it out from the central server.
    Returns (can_proceed: bool, owner_name: str).
    """
    return _store.check_and_acquire_ownership(doc)
