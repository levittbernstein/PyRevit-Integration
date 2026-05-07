# -*- coding: utf-8 -*-
"""Extract quality metrics from an open Revit document."""

import os

from Autodesk.Revit.DB import (
    BuiltInCategory,
    BuiltInParameter,
    DesignOption,
    ElementId,
    FilledRegion,
    FilteredElementCollector,
    FamilyInstance,
    Group,
    ImageInstance,
    ImageType,
    ImageTypeSource,
    ImportInstance,
    ReferencePlane,
    RevitLinkInstance,
    Sketch,
    View,
    ViewSheet,
    Wall,
)


def _safe(fn, fallback=''):
    """Execute fn(), returning fallback on any exception."""
    try:
        return fn()
    except Exception:
        return fallback


def _image_path(img_type):
    """Best-effort extraction of the file path from an ImageType."""
    try:
        param = img_type.get_Parameter(BuiltInParameter.RASTER_SYMBOL_FILENAME)
        if param:
            return param.AsString() or ''
    except Exception:
        pass
    try:
        return getattr(img_type, 'Path', '') or ''
    except Exception:
        return ''


# ── Public entry point ────────────────────────────────────────────────────────

def extract_metrics(doc, file_path):
    """Return an ordered dict of quality metrics for the given open document."""
    m = {}

    # ── File metadata ─────────────────────────────────────────────────────────
    m['File Name'] = os.path.basename(file_path)
    m['File Size (MB)'] = _safe(
        lambda: round(os.path.getsize(file_path) / 1048576.0, 2)
    )
    m['Revit Version'] = _safe(lambda: doc.Application.VersionNumber)

    # ── Warnings ──────────────────────────────────────────────────────────────
    m['Total Warnings'] = _safe(lambda: len(doc.GetWarnings()))

    # ── 3D / 2D elements ──────────────────────────────────────────────────────
    def _element_counts():
        count_3d = count_2d = 0
        for e in FilteredElementCollector(doc).WhereElementIsNotElementType().ToElements():
            if e.Category is None:
                continue
            if e.ViewSpecific:
                count_2d += 1
            else:
                count_3d += 1
        return count_3d, count_2d

    _ec = _safe(_element_counts, (0, 0))
    m['3D Elements'] = _ec[0]
    m['2D Elements'] = _ec[1]

    # ── Groups ────────────────────────────────────────────────────────────────
    def _group_counts():
        model_cat = ElementId(BuiltInCategory.OST_IOSModelGroups)
        detail_cat = ElementId(BuiltInCategory.OST_IOSDetailGroups)
        model_n = detail_n = 0
        for g in FilteredElementCollector(doc).OfClass(Group).ToElements():
            if g.Category is None:
                continue
            if g.Category.Id == model_cat:
                model_n += 1
            elif g.Category.Id == detail_cat:
                detail_n += 1
        return model_n, detail_n

    _gc = _safe(_group_counts, (0, 0))
    m['Model Groups'] = _gc[0]
    m['Detail Groups'] = _gc[1]

    # ── Custom object styles ──────────────────────────────────────────────────
    def _custom_styles():
        count = 0
        for cat in doc.Settings.Categories:
            if cat.Id.IntegerValue > 0:
                count += 1
            for sub in cat.SubCategories:
                if sub.Id.IntegerValue > 0:
                    count += 1
        return count

    m['Custom Object Styles'] = _safe(_custom_styles)

    # ── CAD imports / links ───────────────────────────────────────────────────
    # ImportInstance covers DWG/DXF/SAT — IsLinked distinguishes import vs link
    def _cad_counts():
        instances = FilteredElementCollector(doc).OfClass(ImportInstance).ToElements()
        return (
            sum(1 for i in instances if not i.IsLinked),
            sum(1 for i in instances if i.IsLinked),
        )

    _cad = _safe(_cad_counts, (0, 0))
    m['CAD Imports'] = _cad[0]
    m['CAD Links'] = _cad[1]

    # ── Image imports / links & PDF imports / links ───────────────────────────
    # ImageInstance covers raster images and PDFs (Revit 2022+).
    # ImageType.Source distinguishes imported vs linked; file extension separates PDF.
    def _image_pdf_counts():
        img_imports = img_links = pdf_imports = pdf_links = 0
        seen = set()
        for inst in FilteredElementCollector(doc).OfClass(ImageInstance).ToElements():
            type_id = inst.GetTypeId()
            if type_id in seen:
                continue
            seen.add(type_id)
            img_type = doc.GetElement(type_id)
            if not isinstance(img_type, ImageType):
                continue
            path = _image_path(img_type)
            is_pdf = path.lower().endswith('.pdf')
            source = img_type.Source
            if source == ImageTypeSource.Import:
                if is_pdf:
                    pdf_imports += 1
                else:
                    img_imports += 1
            elif source == ImageTypeSource.Link:
                if is_pdf:
                    pdf_links += 1
                else:
                    img_links += 1
        return img_imports, img_links, pdf_imports, pdf_links

    _ip = _safe(_image_pdf_counts, (0, 0, 0, 0))
    m['Image Imports'] = _ip[0]
    m['Image Links'] = _ip[1]
    m['PDF Imports'] = _ip[2]
    m['PDF Links'] = _ip[3]

    # ── Revit links ───────────────────────────────────────────────────────────
    m['Revit Links'] = _safe(
        lambda: FilteredElementCollector(doc).OfClass(RevitLinkInstance).GetElementCount()
    )

    # ── Sheets, views, views not on sheets, views with 'copy' ─────────────────
    def _view_counts():
        sheets = FilteredElementCollector(doc).OfClass(ViewSheet).ToElements()
        sheet_count = len(sheets)

        views_on_sheets = set()
        for sheet in sheets:
            for vid in sheet.GetAllPlacedViews():
                views_on_sheets.add(vid.IntegerValue)

        project_views = [
            v for v in FilteredElementCollector(doc).OfClass(View).ToElements()
            if not v.IsTemplate and not isinstance(v, ViewSheet)
        ]

        view_count = len(project_views)
        not_on_sheet = sum(
            1 for v in project_views if v.Id.IntegerValue not in views_on_sheets
        )
        copy_count = sum(1 for v in project_views if 'copy' in v.Name.lower())

        return sheet_count, view_count, not_on_sheet, copy_count

    _vc = _safe(_view_counts, (0, 0, 0, 0))
    m['Sheets'] = _vc[0]
    m['Views'] = _vc[1]
    m['Views Not on Sheets'] = _vc[2]
    m['Views with "Copy" in Name'] = _vc[3]

    # ── Walls with edited profiles ─────────────────────────────────────────────
    # Standard walls have no Sketch element; only Edit Profile walls do.
    def _edited_profile_walls():
        wall_ids = set()
        for sketch in FilteredElementCollector(doc).OfClass(Sketch).ToElements():
            try:
                owner = doc.GetElement(sketch.OwnerId)
                if isinstance(owner, Wall):
                    wall_ids.add(owner.Id.IntegerValue)
            except Exception:
                pass
        return len(wall_ids)

    m['Walls with Edited Profile'] = _safe(_edited_profile_walls)

    # ── In-place families ─────────────────────────────────────────────────────
    def _in_place_count():
        return sum(
            1 for fi in FilteredElementCollector(doc).OfClass(FamilyInstance).ToElements()
            if fi.Symbol is not None
            and fi.Symbol.Family is not None
            and fi.Symbol.Family.IsInPlace
        )

    m['In-Place Families'] = _safe(_in_place_count)

    # ── Filled regions ────────────────────────────────────────────────────────
    m['Filled Regions'] = _safe(
        lambda: FilteredElementCollector(doc).OfClass(FilledRegion).GetElementCount()
    )

    # ── Families not prefixed 'LB-' ───────────────────────────────────────────
    # Counts unique loaded family names (excludes in-place, system families)
    # that do not start with 'LB-'.
    def _non_lb_families():
        names = set()
        for fi in FilteredElementCollector(doc).OfClass(FamilyInstance).ToElements():
            if fi.Symbol is not None and fi.Symbol.Family is not None:
                fam = fi.Symbol.Family
                if not fam.IsInPlace:
                    names.add(fam.Name)
        return sum(1 for n in names if not n.startswith('LB-'))

    m['Families Not Prefixed "LB-"'] = _safe(_non_lb_families)

    # ── Reference planes ──────────────────────────────────────────────────────
    m['Reference Planes'] = _safe(
        lambda: FilteredElementCollector(doc).OfClass(ReferencePlane).GetElementCount()
    )

    # ── Design options ────────────────────────────────────────────────────────
    m['Design Options'] = _safe(
        lambda: FilteredElementCollector(doc).OfClass(DesignOption).GetElementCount()
    )

    return m
