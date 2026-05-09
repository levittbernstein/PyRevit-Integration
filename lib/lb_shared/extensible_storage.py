# -*- coding: utf-8 -*-
"""
Reusable Revit Extensible Storage manager for LB pyRevit plugins.

Settings are stored as a JSON blob on the model's ProjectInformation element,
which always exists and requires no import of the DataStorage class (whose
import is broken in IronPython's package context across multiple Revit versions).

Each plugin uses its own schema GUID so the entities are completely independent.

Quick-start
-----------
    from lb_shared.extensible_storage import ExtensibleStorageManager

    _store = ExtensibleStorageManager(
        schema_guid  = 'YOUR-UNIQUE-GUID-HERE',   # generate once, never change
        schema_name  = 'LBYourPluginSettings',
    )

    # Read — no Transaction needed
    data = _store.load(doc)                        # dict or None

    # Write — must be inside an open Revit Transaction
    _store.save(doc, my_dict)

    # Workshared dialog lock — call before showing any modal dialog
    can_open, owner = _store.check_and_acquire_ownership(doc)
    if not can_open:
        # show message: owner has the dialog open, wait for them to sync
        ...

Generating a GUID
-----------------
Run in any Python shell:
    import uuid; print(str(uuid.uuid4()).upper())
"""

import json


# ── Utility ───────────────────────────────────────────────────────────────────

def _eid_int(element_id):
    """Integer value of an ElementId — compatible with Revit 2024+ (Value)
    and earlier (IntegerValue)."""
    return getattr(element_id, 'Value', None) or getattr(element_id, 'IntegerValue', None)


# ── Manager class ─────────────────────────────────────────────────────────────

class ExtensibleStorageManager(object):
    """
    Stores an arbitrary JSON blob in Extensible Storage on the model's
    ProjectInformation element (always present, no creation required).

    Parameters
    ----------
    schema_guid : str
        A fixed UUID string.  Generate it once with ``str(uuid.uuid4())``
        and never change it — Revit uses this to identify the schema across
        sessions and users.
    schema_name : str
        Human-readable schema name shown in Revit's diagnostic tools.
    json_field : str, optional
        Name of the single String field inside the schema.
        Defaults to ``'Data'``.
    """

    def __init__(self, schema_guid, schema_name, element_name='LBStorage',
                 json_field='Data', data_storage_class=None, **kwargs):
        self._guid               = schema_guid
        self._schema_name        = schema_name
        self._element_name       = element_name
        self._field              = json_field
        self._data_storage_class = data_storage_class  # None → use ProjectInformation
        self._schema_cache       = None

    # ── Schema ────────────────────────────────────────────────────────────────

    def _get_schema(self):
        if self._schema_cache is not None:
            return self._schema_cache

        from Autodesk.Revit.DB.ExtensibleStorage import (  # noqa: PLC0415
            Schema, SchemaBuilder, AccessLevel,
        )
        import System  # noqa: PLC0415

        guid     = System.Guid(self._guid)
        existing = Schema.Lookup(guid)
        if existing:
            self._schema_cache = existing
            return existing

        builder = SchemaBuilder(guid)
        builder.SetSchemaName(self._schema_name)
        builder.SetReadAccessLevel(AccessLevel.Public)
        builder.SetWriteAccessLevel(AccessLevel.Public)
        builder.AddSimpleField(self._field, System.String)
        self._schema_cache = builder.Finish()
        return self._schema_cache

    # ── Storage element ───────────────────────────────────────────────────────

    def _get_element(self, doc):
        """
        Return the element that holds our Extensible Storage entity.

        Preference: a dedicated DataStorage element (found via
        ExtensibleStorageFilter).  This gives each plugin its own element
        with independent worksharing ownership.

        Fallback: doc.ProjectInformation — always present, but its
        worksharing ownership is shared across all plugins.
        """
        if self._data_storage_class is not None:
            # Try to find an existing dedicated DataStorage element.
            from Autodesk.Revit.DB import FilteredElementCollector          # noqa: PLC0415
            from Autodesk.Revit.DB.ExtensibleStorage import (               # noqa: PLC0415
                Schema, ExtensibleStorageFilter,
            )
            import System                                                    # noqa: PLC0415
            guid   = System.Guid(self._guid)
            schema = Schema.Lookup(guid)
            if schema is not None:
                col = list(FilteredElementCollector(doc).WherePasses(
                    ExtensibleStorageFilter(guid)))
                if col:
                    return col[0]
            # Not found yet — will be created in save().
            return None
        # DataStorage unavailable — fall back to ProjectInformation.
        return doc.ProjectInformation

    def find_element(self, doc):
        """Public alias kept for backwards compatibility."""
        return self._get_element(doc)

    # ── Read ──────────────────────────────────────────────────────────────────

    def load(self, doc):
        """
        Return the stored data as a dict, or None if nothing has been saved yet.
        No Transaction is required.
        """
        try:
            schema = self._get_schema()
            el     = self._get_element(doc)
            if el is None:
                return None  # no DataStorage element created yet
            import System as _Sys  # noqa: PLC0415
            entity = el.GetEntity(schema)
            if not entity.IsValid():
                return None
            raw = entity.Get[_Sys.String](self._field)
            return json.loads(raw) if raw else None
        except Exception as exc:
            print('[{}] load error: {}'.format(self._schema_name, exc))
            return None

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(self, doc, data):
        """
        Persist *data* (any JSON-serialisable value, typically a dict).
        Must be called inside an open Revit Transaction.
        Creates the DataStorage element on the first call (if available).
        """
        from Autodesk.Revit.DB.ExtensibleStorage import Entity  # noqa: PLC0415
        import System as _Sys                                    # noqa: PLC0415

        schema = self._get_schema()
        el     = self._get_element(doc)
        if el is None:
            # First save — create a dedicated DataStorage element.
            el      = self._data_storage_class.Create(doc)
            el.Name = self._element_name

        entity = Entity(schema)
        entity.Set[_Sys.String](self._field, json.dumps(data, ensure_ascii=False))
        el.SetEntity(entity)

    # ── Worksharing ownership ─────────────────────────────────────────────────

    def check_and_acquire_ownership(self, doc):
        """
        For workshared models: verify the ProjectInformation element is not
        owned by another user, then check it out from the central server.

        Returns (can_proceed: bool, owner_name: str).
        """
        if not doc.IsWorkshared:
            return True, ''

        el = self._get_element(doc)
        if el is None:
            # DataStorage element not yet created — no owner, safe to proceed.
            return True, ''

        try:
            from Autodesk.Revit.DB import (                      # noqa: PLC0415
                WorksharingUtils, CheckoutStatus, ElementId,
            )
            from System.Collections.Generic import List          # noqa: PLC0415

            status = WorksharingUtils.GetCheckoutStatus(doc, el.Id)
            if status == CheckoutStatus.OwnedByOtherUser:
                return False, self._owner_name(doc, el.Id)

            if status != CheckoutStatus.OwnedByCurrentUser:
                ids = List[ElementId]()
                ids.Add(el.Id)
                checked_out    = WorksharingUtils.CheckoutElements(doc, ids)
                checked_values = set(_eid_int(e) for e in checked_out)
                if _eid_int(el.Id) not in checked_values:
                    return False, self._owner_name(doc, el.Id)

        except Exception as exc:
            print('[{}] check_and_acquire_ownership error: {}'.format(
                self._schema_name, exc))

        return True, ''

    def _owner_name(self, doc, element_id):
        """Human-readable name of whoever currently owns *element_id*."""
        try:
            from Autodesk.Revit.DB import WorksharingUtils  # noqa: PLC0415
            name = WorksharingUtils.GetWorksharingTooltipInfo(
                doc, element_id).Owner
            return name or 'another user'
        except Exception:
            return 'another user'
