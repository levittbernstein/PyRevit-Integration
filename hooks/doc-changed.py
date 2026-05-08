# -*- coding: utf-8 -*-
"""
LB Door Handing — doc-changed hook.

Fires after every committed transaction.  Filters for modified or added door
instances and updates their 'Door Handing' parameter if the value has drifted
from what HandFlipped / FacingFlipped imply.

Infinite-loop prevention: write_handing() only calls p.Set() when the stored
value differs from the computed value.  On the second firing (triggered by our
own parameter write) all values already match, so we exit without writing and
the loop stops naturally.
"""

import sys
import os

from Autodesk.Revit.DB import FamilyInstance, BuiltInCategory, Transaction

# ── Resolve lib path ──────────────────────────────────────────────────────────
_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB       = os.path.join(_HOOKS_DIR, 'lib')
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from door_handing.handing import parameter_exists_on_doors, write_handing

# ── Event args ────────────────────────────────────────────────────────────────
# __eventargs__ is injected by pyRevit as a DocumentChangedEventArgs instance.
doc = __eventargs__.GetDocument()  # noqa: F821

if doc is None or doc.IsFamilyDocument:
    sys.exit()

# ── Quick exit if the parameter hasn't been set up yet ────────────────────────
if not parameter_exists_on_doors(doc):
    sys.exit()

# ── Find door instances among the changed elements ───────────────────────────
changed_ids = (
    list(__eventargs__.GetModifiedElementIds())  # noqa: F821
    + list(__eventargs__.GetAddedElementIds())   # noqa: F821
)

door_cat_id = int(BuiltInCategory.OST_Doors)

doors_to_update = []
for eid in changed_ids:
    el = doc.GetElement(eid)
    if not isinstance(el, FamilyInstance):
        continue
    cat = el.Category
    if cat is None:
        continue
    cat_id_int = getattr(cat.Id, 'Value', None) or getattr(cat.Id, 'IntegerValue', None)
    if int(cat_id_int) != door_cat_id:
        continue
    # write_handing returns without writing when the value is already correct,
    # but we still need the element in scope — collect it here so we can open
    # one transaction for all of them.
    p = el.LookupParameter('Door Handing')
    if p is None or p.IsReadOnly:
        continue
    from door_handing.handing import get_handing
    if (p.AsString() or '') != get_handing(el):
        doors_to_update.append(el)

if not doors_to_update:
    sys.exit()  # nothing to do — prevents unnecessary transaction + infinite loop

# ── Update in a single transaction ───────────────────────────────────────────
# Guard against InvalidOperationException: Revit temporarily locks the
# document during design-option edits, undo/redo, and certain external
# commands.  If we cannot start a transaction here, skip this firing —
# the hook will fire again once the model becomes modifiable.
try:
    with Transaction(doc, 'LB - Update Door Handing') as t:
        t.Start()
        for door in doors_to_update:
            write_handing(door)
        t.Commit()
except Exception:
    pass
