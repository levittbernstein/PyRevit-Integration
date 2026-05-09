# -*- coding: utf-8 -*-
"""
Reusable Revit Extensible Storage manager for LB pyRevit plugins.

Each plugin creates its own ExtensibleStorageManager instance with a unique
schema GUID.  Different instances are completely independent — they each own
a separate DataStorage element in the model, with separate worksharing
ownership, so two plugins can be used simultaneously by different users with
no interference.

Quick-start
-----------
    from lb_shared.extensible_storage import ExtensibleStorageManager

    _store = ExtensibleStorageManager(
        schema_guid  = 'YOUR-UNIQUE-GUID-HERE',   # generate once, never change
        schema_name  = 'LBYourPluginSettings',
        element_name = 'LBYourPluginStorage',
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
    Manages a single Revit DataStorage element that stores an arbitrary
    JSON blob in one String field.

    Parameters
    ----------
    schema_guid : str
        A fixed UUID string.  Generate it once with ``str(uuid.uuid4())``
        and never change it — Revit uses this to identify the schema across
        sessions and users.
    schema_name : str
        Human-readable schema name shown in Revit's diagnostic tools.
    element_name : str
        Name given to the DataStorage element in the model.
    json_field : str, optional
        Name of the single String field inside the schema.
        Defaults to ``'Data'``.
    """

    def __init__(self, schema_guid, schema_name, element_name, json_field='Data'):
        self._guid         = schema_guid
        self._schema_name  = schema_name
        self._element_name = element_name
        self._field        = json_field
        self._schema_cache = None  # populated on first _get_schema() call

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

    # ── DataStorage element ────────────────────────────────────────────────────

    def find_element(self, doc):
        """Return the DataStorage element for this schema, or None."""
        from Autodesk.Revit.DB import FilteredElementCollector                  # noqa: PLC0415
        from Autodesk.Revit.DB.ExtensibleStorage import (                       # noqa: PLC0415
            Schema, ExtensibleStorageFilter,
        )
        import System                                                            # noqa: PLC0415

        # Use ExtensibleStorageFilter — the purpose-built Revit API filter for
        # locating DataStorage elements by schema GUID.  This avoids importing
        # the DataStorage class itself, which IronPython cannot resolve as a
        # namespace attribute inside a Python package context.
        guid   = System.Guid(self._guid)
        schema = Schema.Lookup(guid)
        if schema is None:
            return None
        col = list(FilteredElementCollector(doc).WherePasses(
            ExtensibleStorageFilter(guid)
        ))
        return col[0] if col else None

    # ── Read ──────────────────────────────────────────────────────────────────

    def load(self, doc):
        """
        Return the stored data as a dict, or None if nothing has been saved yet.
        No Transaction is required.
        """
        try:
            schema = self._get_schema()
            ds     = self.find_element(doc)
            if ds is None:
                return None
            import System as _Sys  # noqa: PLC0415
            raw = ds.GetEntity(schema).Get[_Sys.String](self._field)
            return json.loads(raw) if raw else None
        except Exception as exc:
            print('[{}] load error: {}'.format(self._schema_name, exc))
            return None

    # ── Write ─────────────────────────────────────────────────────────────────

    def save(self, doc, data):
        """
        Persist *data* (any JSON-serialisable value, typically a dict).
        Must be called inside an open Revit Transaction.
        Creates the DataStorage element on the first call.
        """
        from Autodesk.Revit.DB.ExtensibleStorage import Entity  # noqa: PLC0415
        import System as _Sys                                    # noqa: PLC0415

        schema = self._get_schema()
        ds     = self.find_element(doc)
        if ds is None:
            # Scan all loaded assemblies for Autodesk.Revit.DB.DataStorage —
            # the type's home assembly varies across Revit versions so we
            # cannot hardcode an assembly name.
            _ds_type = None
            for _asm in _Sys.AppDomain.CurrentDomain.GetAssemblies():
                try:
                    _t = _asm.GetType('Autodesk.Revit.DB.DataStorage')
                    if _t is not None:
                        _ds_type = _t
                        break
                except Exception:
                    pass
            if _ds_type is None:
                raise RuntimeError(
                    '[{}] DataStorage type not found in any loaded assembly'
                    .format(self._schema_name))
            ds = _ds_type.GetMethod('Create').Invoke(
                None, _Sys.Array[_Sys.Object]([doc]))
            ds.Name = self._element_name

        entity = Entity(schema)
        entity.Set[_Sys.String](self._field, json.dumps(data, ensure_ascii=False))
        ds.SetEntity(entity)

    # ── Worksharing ownership ─────────────────────────────────────────────────

    def check_and_acquire_ownership(self, doc):
        """
        For workshared models: verify the DataStorage element is not owned by
        another user, then check it out from the central server via
        ``WorksharingUtils.CheckoutElements``.

        This is the same mechanism Revit uses to prevent two users editing the
        same wall — once checked out, other users see
        ``CheckoutStatus.OwnedByOtherUser`` and cannot commit changes to this
        element.  Ownership is released when the current user next syncs to
        central.

        Returns
        -------
        (can_proceed : bool, owner_name : str)

        ``(True, '')``
            Not a workshared model, the element doesn't exist yet, or the
            element was successfully checked out for the current user.

        ``(False, 'username')``
            The element is currently owned by *username*.  Show a message and
            abort — the user should wait until the owner has synced to central.
        """
        if not doc.IsWorkshared:
            return True, ''

        ds = self.find_element(doc)
        if ds is None:
            # Element is created during the first save Transaction.
            # A non-existent element has no owner.
            return True, ''

        try:
            from Autodesk.Revit.DB import (                      # noqa: PLC0415
                WorksharingUtils, CheckoutStatus, ElementId,
            )
            from System.Collections.Generic import List          # noqa: PLC0415

            # Fast local check (based on last-sync state).
            status = WorksharingUtils.GetCheckoutStatus(doc, ds.Id)
            if status == CheckoutStatus.OwnedByOtherUser:
                return False, self._owner_name(doc, ds.Id)

            # If we don't already own it, ask the central server to reserve it.
            if status != CheckoutStatus.OwnedByCurrentUser:
                ids = List[ElementId]()
                ids.Add(ds.Id)
                checked_out    = WorksharingUtils.CheckoutElements(doc, ids)
                checked_values = set(_eid_int(e) for e in checked_out)
                if _eid_int(ds.Id) not in checked_values:
                    # Central server confirmed it is owned by someone else.
                    return False, self._owner_name(doc, ds.Id)

        except Exception as exc:
            # Network error, non-workshared cloud variant, or unsupported API.
            # Proceed — the save Transaction will surface any real conflict
            # at commit time, which is still a safe failure mode.
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
