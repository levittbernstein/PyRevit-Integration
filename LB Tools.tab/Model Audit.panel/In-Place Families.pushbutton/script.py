# -*- coding: utf-8 -*-
"""Find all in-place families and isolate them in a dedicated 3D view."""

from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List

doc = revit.doc
uidoc = revit.uidoc
output = script.get_output()

VIEW_NAME = "LB - In-Place Families"


def get_3d_view_type_id(document):
    collector = DB.FilteredElementCollector(document).OfClass(DB.ViewFamilyType)
    for vft in collector:
        if vft.ViewFamily == DB.ViewFamily.ThreeDimensional:
            return vft.Id
    return None


def get_or_create_3d_view(document, name):
    for view in DB.FilteredElementCollector(document).OfClass(DB.View3D):
        if not view.IsTemplate and view.Name == name:
            return view

    type_id = get_3d_view_type_id(document)
    if not type_id:
        forms.alert("No 3D view type found in this project.", exitscript=True)

    with DB.Transaction(document, "LB - Create In-Place Families View") as t:
        t.Start()
        new_view = DB.View3D.CreateIsometric(document, type_id)
        new_view.Name = name
        t.Commit()
    return new_view


# --- Collect all in-place family instances ---
instances = DB.FilteredElementCollector(doc) \
    .OfClass(DB.FamilyInstance) \
    .WhereElementIsNotElementType() \
    .ToElements()

in_place_ids = List[DB.ElementId]()
in_place_names = []

for inst in instances:
    try:
        fam = inst.Symbol.Family
        if fam and fam.IsInPlace:
            in_place_ids.Add(inst.Id)
            in_place_names.append(fam.Name)
    except Exception:
        pass

if in_place_ids.Count == 0:
    forms.alert("No in-place families found in this project.", title="LB - In-Place Families")
    import sys; sys.exit()

# --- Report to output window ---
output.print_md("## In-Place Families — {} instance(s) found".format(in_place_ids.Count))
unique_names = sorted(set(in_place_names))
for name in unique_names:
    count = in_place_names.count(name)
    output.print_md("- **{}** ({} instance{})".format(name, count, "s" if count > 1 else ""))

# --- Get or create the 3D view and isolate ---
view = get_or_create_3d_view(doc, VIEW_NAME)

with DB.Transaction(doc, "LB - Isolate In-Place Families") as t:
    t.Start()
    if view.IsInTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate):
        view.DisableTemporaryViewMode(DB.TemporaryViewMode.TemporaryHideIsolate)
    view.IsolateElementsTemporary(in_place_ids)
    t.Commit()

# --- Switch to the view ---
uidoc.ActiveView = view

forms.alert(
    "{} in-place famil{} found across {} unique famil{}.\n\nView '{}' is now active.".format(
        in_place_ids.Count,
        "y" if in_place_ids.Count == 1 else "ies",
        len(unique_names),
        "y" if len(unique_names) == 1 else "ies",
        VIEW_NAME
    ),
    title="LB - In-Place Families"
)
