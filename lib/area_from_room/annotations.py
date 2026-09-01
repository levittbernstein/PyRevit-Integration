# -*- coding: utf-8 -*-
"""Copy view-specific annotation from the floor plan into the area plan.

Grabs anything owned by and specific to the source view — detail lines, text,
generic annotations, detail components and detail groups — while skipping the
spatial elements and their tags (handled elsewhere) and viewports/cameras.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, ElementTransformUtils, Transform,
    CopyPasteOptions, BuiltInCategory,
)
from System.Collections.Generic import List
from Autodesk.Revit.DB import ElementId

_SKIP = set(int(b) for b in (
    BuiltInCategory.OST_Rooms, BuiltInCategory.OST_RoomTags,
    BuiltInCategory.OST_Areas, BuiltInCategory.OST_AreaTags,
    BuiltInCategory.OST_AreaSchemeLines, BuiltInCategory.OST_Viewports,
    BuiltInCategory.OST_Cameras,
))


def _cat_int(cat):
    cid = cat.Id
    return getattr(cid, 'Value', None) or getattr(cid, 'IntegerValue', None)


def copy_annotations(doc, src, dst, report):
    ids = []
    for el in FilteredElementCollector(doc, src.Id).WhereElementIsNotElementType():
        try:
            if not el.ViewSpecific or el.OwnerViewId != src.Id:
                continue
            cat = el.Category
            if cat is None or _cat_int(cat) in _SKIP:
                continue
            ids.append(el.Id)
        except Exception:
            continue

    if not ids:
        report.count('annotations copied', 0)
        return 0

    # Try one batch copy; if the batch trips over a single awkward element
    # (e.g. a dimension referencing missing geometry), fall back to per-element.
    def _copy(id_iterable):
        lst = List[ElementId]()
        for i in id_iterable:
            lst.Add(i)
        return ElementTransformUtils.CopyElements(
            src, lst, dst, Transform.Identity, CopyPasteOptions())

    copied = 0
    try:
        copied = len(list(_copy(ids)))
    except Exception:
        for i in ids:
            try:
                copied += len(list(_copy([i])))
            except Exception:
                pass

    report.count('annotations copied', copied)
    if copied < len(ids):
        report.warn('{} of {} annotation element(s) did not copy.'
                    .format(len(ids) - copied, len(ids)))
    return copied
