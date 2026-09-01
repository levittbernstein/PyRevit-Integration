# -*- coding: utf-8 -*-
"""Create '<name>_Areas' shared parameters bound to Areas.

Used ONLY for built-in room parameters that a key schedule needs (e.g.
Occupancy) — built-ins can't be added to the Areas category at all, even in the
UI, so a parallel '<name>_Areas' parameter is the only way to carry their
values onto areas.

Non-shared project parameters are NOT handled here — they can be ticked onto
Areas in Manage > Project Parameters, so the tool just lists them for the user
to fix (see bindings.unexpanded_room_params).

Definitions live in a dedicated group in a private temp shared-parameter file
(the office file is never touched); the app's original shared-parameter file
setting is restored afterwards.
"""

import os
import tempfile

from Autodesk.Revit.DB import (
    Category, BuiltInCategory, CategorySet, StorageType,
    ExternalDefinitionCreationOptions,
)

# A valid shared-parameter file needs this header — a 0-byte file is rejected.
_SP_HEADER = (
    '# This is a Revit shared parameter file.\n'
    '# Do not edit manually.\n'
    '*META\tVERSION\tMINVERSION\n'
    'META\t2\t1\n'
    '*GROUP\tID\tNAME\n'
    '*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\tVISIBLE\t'
    'DESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\n'
)


def _areas_cat(doc):
    return Category.GetCategory(doc, BuiltInCategory.OST_Areas)


def _ensure_group(doc):
    app = doc.Application
    tmp = os.path.join(tempfile.gettempdir(),
                       'LB_AreaFromRoom_SharedParams.txt')
    if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
        fh = open(tmp, 'w')
        fh.write(_SP_HEADER)
        fh.close()
    app.SharedParametersFilename = tmp
    sp = app.OpenSharedParameterFile()
    if sp is None:
        return None
    grp = sp.Groups.get_Item('AreaFromRoom')
    if grp is None:
        grp = sp.Groups.Create('AreaFromRoom')
    return grp


def _get_or_create_def(grp, defname, data_type):
    d = grp.Definitions.get_Item(defname)
    if d is not None:
        return d
    try:
        opts = ExternalDefinitionCreationOptions(defname, data_type)
        return grp.Definitions.Create(opts)
    except Exception:
        return None


def _bind(doc, definition):
    from Autodesk.Revit.DB import GroupTypeId
    app = doc.Application
    bmap = doc.ParameterBindings
    if bmap.Contains(definition):
        return True
    catset = CategorySet()
    catset.Insert(_areas_cat(doc))
    binding = app.Create.NewInstanceBinding(catset)
    try:
        return bmap.Insert(definition, binding, GroupTypeId.IdentityData)
    except Exception:
        try:
            return bmap.Insert(definition, binding)
        except Exception:
            return False


def ensure_area_param(doc, name, data_type, report=None):
    """Create (or reuse) a shared '<name>_Areas' parameter bound to Areas.
    Returns the area parameter name, or None. Restores the app's shared-
    parameter file setting afterwards."""
    if data_type is None:
        return None
    app = doc.Application
    try:
        original = app.SharedParametersFilename
    except Exception:
        original = None
    try:
        grp = _ensure_group(doc)
        if grp is None:
            return None
        defname = name + '_Areas'
        definition = _get_or_create_def(grp, defname, data_type)
        if definition is None or not _bind(doc, definition):
            return None
        return defname
    except Exception as ex:
        if report is not None:
            msg = getattr(ex, 'Message', '') or str(ex) or type(ex).__name__
            report.warn('Could not create "{}_Areas": {}.'.format(name, msg))
        return None
    finally:
        try:
            if original:
                app.SharedParametersFilename = original
        except Exception:
            pass


def _set_value(rp, ap):
    st = rp.StorageType
    if st == StorageType.String:
        ap.Set(rp.AsString() or '')
    elif st == StorageType.Integer:
        ap.Set(rp.AsInteger())
    elif st == StorageType.Double:
        ap.Set(rp.AsDouble())


def copy_bridged(src, dst, mapping):
    """Copy bridged values: src's '<name>' -> dst's '<name>_Areas'."""
    for rname, aname in mapping.items():
        try:
            rp = src.LookupParameter(rname)
            ap = dst.LookupParameter(aname)
            if rp is None or ap is None or ap.IsReadOnly or not rp.HasValue:
                continue
            _set_value(rp, ap)
        except Exception:
            pass
