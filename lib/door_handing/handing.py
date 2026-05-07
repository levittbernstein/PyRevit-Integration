# -*- coding: utf-8 -*-
"""
Shared logic for the LB Door Handing tool.

Computes door handing from HandFlipped / FacingFlipped and manages the
'Door Handing' shared parameter binding.  Runs under IronPython inside Revit.
"""

import os
import sys

from System import Guid

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    BuiltInCategory,
    FamilyInstance,
)

# ── Constants ─────────────────────────────────────────────────────────────────

PARAM_NAME  = 'Door Handing'
GROUP_NAME  = 'LB Door Parameters'

# Stable GUID — must never change once deployed so the parameter is
# recognised consistently across models and workstations.
PARAM_GUID  = Guid('F1A2B3C4-D5E6-7890-ABCD-EF0123456789')


# ── Handing calculation ───────────────────────────────────────────────────────

def get_handing(door):
    """Return the handing string for a door FamilyInstance.

    Viewed from the 'from room' (exterior / push) face:

        RH  – hinge right, opens inward    ← Revit standard family default
        LH  – hinge left,  opens inward
        RHR – hinge right, opens outward   (reverse swing)
        LHR – hinge left,  opens outward

    HandFlipped  mirrors the hinge side.
    FacingFlipped mirrors the swing direction (inward ↔ outward).

    If your door families default to LH rather than RH, swap the first
    two return values below.
    """
    h = door.HandFlipped
    f = door.FacingFlipped

    if   not h and not f: return 'RH'
    elif     h and not f: return 'LH'
    elif not h and     f: return 'RHR'
    else:                 return 'LHR'


# ── Parameter helpers ─────────────────────────────────────────────────────────

def parameter_exists_on_doors(doc):
    """Return True if Door Handing is already bound to door instances."""
    door = (
        FilteredElementCollector(doc)
        .OfCategory(BuiltInCategory.OST_Doors)
        .WhereElementIsNotElementType()
        .FirstElement()
    )
    return door is not None and door.LookupParameter(PARAM_NAME) is not None


def write_handing(door):
    """Write the computed handing value to the door's Door Handing parameter.

    No-ops silently if the parameter is absent or read-only (e.g. not yet
    set up in this model).
    """
    p = door.LookupParameter(PARAM_NAME)
    if p is None or p.IsReadOnly:
        return
    expected = get_handing(door)
    # Only write if the value is actually changing — avoids triggering a
    # redundant document-changed event from the hook.
    if (p.AsString() or '') != expected:
        p.Set(expected)


def create_shared_parameter(doc):
    """Create and bind the Door Handing shared parameter to the Doors category.

    Uses a scratch shared-parameter file written to %TEMP% so the user's
    own shared-parameter file is never altered.  Restores the original path
    regardless of success or failure.

    Returns True on success, False otherwise.
    """
    app      = doc.Application
    original = app.SharedParametersFilename
    tmp_file = os.path.join(
        os.environ.get('TEMP', os.environ.get('TMP', os.getcwd())),
        'LBTools_DoorHanding_SharedParams.txt',
    )

    try:
        _write_param_file(tmp_file)

        app.SharedParametersFilename = tmp_file
        def_file = app.OpenSharedParameterFile()
        if def_file is None:
            return False

        grp = def_file.Groups.get_Item(GROUP_NAME) or def_file.Groups.Create(GROUP_NAME)

        defn = grp.Definitions.get_Item(PARAM_NAME)
        if defn is None:
            from Autodesk.Revit.DB import ExternalDefinitionCreationOptions
            opts = _make_defn_options(PARAM_NAME)
            opts.GUID            = PARAM_GUID
            opts.UserModifiable  = False  # calculated — users must not overwrite
            defn = grp.Definitions.Create(opts)

        if defn is None:
            return False

        cats = app.Create.NewCategorySet()
        cats.Insert(doc.Settings.Categories.get_Item(BuiltInCategory.OST_Doors))
        binding = app.Create.NewInstanceBinding(cats)

        from Autodesk.Revit.DB import Transaction
        with Transaction(doc, 'LB - Bind Door Handing Parameter') as t:
            t.Start()
            ok = doc.ParameterBindings.Insert(defn, binding, _param_group())
            if ok:
                t.Commit()
                return True
            t.RollBack()
            return False

    except Exception:
        return False
    finally:
        app.SharedParametersFilename = original


# ── Internal helpers ──────────────────────────────────────────────────────────

def _make_defn_options(name):
    """Return an ExternalDefinitionCreationOptions for a text parameter.

    Supports both the SpecTypeId API (Revit 2022+) and the legacy
    ParameterType API (older versions).
    """
    from Autodesk.Revit.DB import ExternalDefinitionCreationOptions
    try:
        from Autodesk.Revit.DB import SpecTypeId
        return ExternalDefinitionCreationOptions(name, SpecTypeId.String.Text)
    except (ImportError, AttributeError):
        from Autodesk.Revit.DB import ParameterType
        return ExternalDefinitionCreationOptions(name, ParameterType.Text)


def _param_group():
    """Return the parameter group for Identity Data.

    Revit 2023 deprecated BuiltInParameterGroup in favour of GroupTypeId;
    both still work through 2027 but we try the new API first.
    """
    try:
        from Autodesk.Revit.DB import GroupTypeId
        return GroupTypeId.IdentityData
    except (ImportError, AttributeError):
        from Autodesk.Revit.DB import BuiltInParameterGroup
        return BuiltInParameterGroup.PG_IDENTITY_DATA


def _write_param_file(path):
    """Write a minimal Revit shared-parameter file if it doesn't exist yet."""
    if os.path.exists(path):
        return
    with open(path, 'w') as fh:
        fh.write(
            '# Revit shared parameter file - LB Door Handing\r\n'
            '# Do not edit manually.\r\n'
            '*META\tVERSION\tMINVERSION\r\n'
            '*META\t2\t1\r\n'
            '*GROUP\tID\tNAME\r\n'
            '*GROUP\t1\t{group}\r\n'
            '*PARAM\tGUID\tNAME\tDATATYPE\tDATACATEGORY\tGROUP\t'
            'VISIBLE\tDESCRIPTION\tUSERMODIFIABLE\tHIDEWHENNOVALUE\r\n'
            '*PARAM\t{guid}\t{name}\tTEXT\t\t1\t1\t\t0\t0\r\n'.format(
                group=GROUP_NAME,
                guid=str(PARAM_GUID),
                name=PARAM_NAME,
            )
        )
