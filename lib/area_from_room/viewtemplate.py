# -*- coding: utf-8 -*-
"""Reproduce the floor plan's graphic look on the area plan, and create a
matching area-plan view template named "<source template>_Area Plan".

Two things happen:
  1. the floor plan's *effective* graphics (category V/G + filters, which
     already include its template) are copied straight onto the area plan, so
     it looks the same immediately;
  2. we then try to mint a reusable view template FROM the area plan. There is
     no cross-type route (a floor-plan template can't be assigned to an area
     plan), so this only works if the running Revit exposes a "create template
     from view" API. If it doesn't, the look is still applied and we say so.
"""

from Autodesk.Revit.DB import (
    View, FilteredElementCollector, ElementId, RevitLinkInstance, Element,
)


def _copy_filters(src, dst, report):
    moved = 0
    try:
        filter_ids = src.GetFilters()
    except Exception:
        return
    for fid in filter_ids:
        try:
            dst.AddFilter(fid)
        except Exception:
            pass
        try:
            dst.SetFilterOverrides(fid, src.GetFilterOverrides(fid))
        except Exception:
            pass
        try:
            dst.SetFilterVisibility(fid, src.GetFilterVisibility(fid))
        except Exception:
            pass
        moved += 1


def _copy_category_graphics(doc, src, dst):
    cats = doc.Settings.Categories
    todo = []
    for cat in cats:
        todo.append(cat)
        try:
            for sub in cat.SubCategories:
                todo.append(sub)
        except Exception:
            pass
    for cat in todo:
        try:
            cid = cat.Id
        except Exception:
            continue
        try:
            if src.CanCategoryBeHidden(cid):
                dst.SetCategoryHidden(cid, src.GetCategoryHidden(cid))
        except Exception:
            pass
        try:
            ogs = src.GetCategoryOverrides(cid)
            if ogs is not None:
                dst.SetCategoryOverrides(cid, ogs)
        except Exception:
            pass


def _link_name(link):
    try:
        return link.Name
    except Exception:
        return '?'


def _get_link_overrides(view, lid):
    try:
        return view.GetLinkOverrides(lid)
    except Exception:
        return None


def _get_element_overrides(view, lid):
    try:
        return view.GetElementOverrides(lid)
    except Exception:
        return None


def _eid(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _copy_link_graphics(doc, gsrc, view, dst, report):
    """Copy each linked model's per-view graphics onto dst.

    Display settings (By Host / By Linked View + linked view id) can be stored
    on the link INSTANCE or TYPE, and on a templated floor plan they live on the
    template — so we read from the template first, then the view, at both levels.

    Halftone: the API can't READ a link's halftone (it always returns false), so
    we instead FORCE halftone on for any link shown "By Linked View" — the
    background-context convention — which matches the intended look.
    """
    from Autodesk.Revit.DB import OverrideGraphicSettings

    disp = 0
    ovr = 0
    halftoned = 0
    errs = []
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        lid = link.Id
        name = _link_name(link)
        try:
            tid = link.GetTypeId()
        except Exception:
            tid = None

        # Find the display settings on instance or type, template or view.
        settings = _get_link_overrides(gsrc, lid) or _get_link_overrides(view, lid)
        settings_id = lid
        if settings is None and tid is not None:
            settings = (_get_link_overrides(gsrc, tid)
                        or _get_link_overrides(view, tid))
            settings_id = tid

        is_bylink = False
        if settings is not None:
            try:
                is_bylink = (str(settings.LinkVisibilityType) == 'ByLinkView')
            except Exception:
                pass
            try:
                dst.SetLinkOverrides(settings_id, settings)
                disp += 1
            except Exception as ex:
                errs.append('display "{}": {}'.format(name, ex))

        # Element overrides on the instance, plus forced halftone if By Linked
        # View.
        ogs = (_get_element_overrides(gsrc, lid)
               or _get_element_overrides(view, lid)
               or OverrideGraphicSettings())
        if is_bylink:
            try:
                ogs.SetHalftone(True)
                halftoned += 1
            except Exception:
                pass
        try:
            dst.SetElementOverrides(lid, ogs)
            ovr += 1
        except Exception as ex:
            errs.append('override "{}": {}'.format(name, ex))

    if disp or ovr:
        if halftoned:
            report.note('Linked model graphics copied (shown by linked view, '
                        'in halftone).')
        else:
            report.note('Linked model graphics copied.')
    for e in errs[:4]:
        report.warn('Linked model {}'.format(e))


def _copy_custom_view_params(src, tpl):
    """Copy custom (non-built-in) project parameter values from one view/template
    to another — e.g. the "View Type" browser-organisation parameter. Built-in
    parameters (name, scale, V/G, …) are left to CreateViewTemplate."""
    from Autodesk.Revit.DB import BuiltInParameter, StorageType
    for p in src.Parameters:
        try:
            if p.Definition.BuiltInParameter != BuiltInParameter.INVALID:
                continue
            if not p.HasValue:
                continue
            name = p.Definition.Name
        except Exception:
            continue
        tp = tpl.LookupParameter(name)
        if tp is None or tp.IsReadOnly or tp.StorageType != p.StorageType:
            continue
        try:
            st = p.StorageType
            if st == StorageType.String:
                tp.Set(p.AsString() or '')
            elif st == StorageType.Integer:
                tp.Set(p.AsInteger())
            elif st == StorageType.Double:
                tp.Set(p.AsDouble())
            elif st == StorageType.ElementId:
                tp.Set(p.AsElementId())
        except Exception:
            pass


def _graphics_source(doc, src):
    """Where to read the floor plan's graphics from: its applied view template
    (the real store for a templated view — link display included), else the
    view itself."""
    tid = src.ViewTemplateId
    if tid is not None and tid != ElementId.InvalidElementId:
        tpl = doc.GetElement(tid)
        if tpl is not None:
            return tpl
    return src


def _find_template(doc, name):
    """An existing view template with this exact name, or None."""
    for v in FilteredElementCollector(doc).OfClass(View):
        try:
            if v.IsTemplate and v.Name == name:
                return v
        except Exception:
            continue
    return None


def _template_base_name(doc, src):
    tid = src.ViewTemplateId
    if tid is not None and tid != ElementId.InvalidElementId:
        tpl = doc.GetElement(tid)
        if tpl is not None and tpl.Name:
            return tpl.Name
    return src.Name


def _unique_view_name(doc, base):
    existing = set(v.Name for v in FilteredElementCollector(doc).OfClass(View))
    name = base
    n = 2
    while name in existing:
        name = '{} {}'.format(base, n)
        n += 1
    return name


def apply_look(doc, src, dst, report, suffix='_Area Plan'):
    gsrc = _graphics_source(doc, src)
    try:
        _copy_category_graphics(doc, gsrc, dst)
    except Exception as ex:
        report.warn('Category graphics not fully copied: {}'.format(ex))
    try:
        _copy_filters(gsrc, dst, report)
    except Exception as ex:
        report.warn('View filters not fully copied: {}'.format(ex))
    try:
        _copy_link_graphics(doc, gsrc, src, dst, report)
    except Exception as ex:
        report.warn('Linked model graphics skipped: {}'.format(ex))

    target_name = _template_base_name(doc, src) + suffix

    # Reuse an existing template of this name rather than making duplicates —
    # several views that share a source template all map to one output template.
    existing = _find_template(doc, target_name)
    if existing is not None:
        try:
            dst.ViewTemplateId = existing.Id
            report.note('Reused view template "{}".'.format(target_name))
            return
        except Exception:
            pass

    # Otherwise mint a reusable template from the (now matching) view.
    # View.CreateViewTemplate() exists in Revit 2020+.
    try:
        checker = getattr(dst, 'IsViewValidForTemplateCreation', None)
        if callable(checker) and not dst.IsViewValidForTemplateCreation():
            report.warn('View not valid for template creation; look copied '
                        'directly. Save one via "Create Template from Current '
                        'View" named "{}".'.format(target_name))
            return
        tpl = dst.CreateViewTemplate()
        # Name it exactly target_name where possible, so the next view reuses it.
        try:
            tpl.Name = target_name
        except Exception:
            tpl.Name = _unique_view_name(doc, target_name)
        dst.ViewTemplateId = tpl.Id
        # Carry the old template's custom parameters (e.g. "View Type") across —
        # CreateViewTemplate copies graphics but not project-parameter values.
        _copy_custom_view_params(gsrc, tpl)
        report.note('Created + applied view template "{}".'.format(tpl.Name))
    except Exception as ex:
        report.warn('View template creation failed ({}); graphic look was still '
                    'copied directly onto the view.'.format(ex))
