# -*- coding: utf-8 -*-
"""
LB Door Handing — setup and populate the Door Handing schedule parameter.

Creates a 'Door Handing' shared parameter bound to the Doors category (if it
doesn't already exist), then populates it for every door in the model.

After running this once, the doc-changed hook keeps values current whenever
a door is flipped — no need to re-run unless new doors are loaded.
"""

import sys
import os

from pyrevit import revit, DB, forms

# ── Resolve lib path ──────────────────────────────────────────────────────────
# extension root  →  lib  →  door_handing
_EXT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_LIB = os.path.join(_EXT_ROOT, 'lib')
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from door_handing.handing import (
    PARAM_NAME,
    parameter_exists_on_doors,
    create_shared_parameter,
    write_handing,
)

# ── Guard ─────────────────────────────────────────────────────────────────────
doc = revit.doc

if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project file first.',
                title='LB - Door Handing', warn_icon=True)
    sys.exit()

# ── Create parameter if needed ────────────────────────────────────────────────
if not parameter_exists_on_doors(doc):
    if not create_shared_parameter(doc):
        forms.alert(
            "Could not create the '{name}' shared parameter.\n\n"
            "Possible causes:\n"
            "  • The parameter name conflicts with an existing one.\n"
            "  • You do not have write access to %TEMP%.\n\n"
            "Check the above and try again.".format(name=PARAM_NAME),
            title='LB - Door Handing', warn_icon=True,
        )
        sys.exit()

# ── Collect all door instances ────────────────────────────────────────────────
doors = list(
    DB.FilteredElementCollector(doc)
    .OfCategory(DB.BuiltInCategory.OST_Doors)
    .WhereElementIsNotElementType()
    .ToElements()
)

if not doors:
    forms.alert('No door instances found in this model.',
                title='LB - Door Handing')
    sys.exit()

# ── Populate handing on every door ────────────────────────────────────────────
with DB.Transaction(doc, 'LB - Setup Door Handing') as t:
    t.Start()
    for door in doors:
        write_handing(door)
    t.Commit()

# ── Summary ───────────────────────────────────────────────────────────────────
forms.alert(
    '{count} door{s} updated.\n\n'
    "Add the '{name}' column to your Door Schedule to display handing.\n\n"
    'From now on the value updates automatically whenever a door is flipped.'.format(
        count=len(doors),
        s='s' if len(doors) != 1 else '',
        name=PARAM_NAME,
    ),
    title='LB - Door Handing',
)
