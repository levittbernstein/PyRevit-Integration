# -*- coding: utf-8 -*-
"""Copy plan-view settings from the floor plan to the area plan:
crop, scope box, view range, scale, detail level, discipline, phase."""

from Autodesk.Revit.DB import BuiltInParameter, ElementId, StorageType


def _copy_param(src, dst, bip):
    sp = src.get_Parameter(bip)
    dp = dst.get_Parameter(bip)
    if sp is None or dp is None or dp.IsReadOnly:
        return
    try:
        st = sp.StorageType
        if st == StorageType.ElementId:
            dp.Set(sp.AsElementId())
        elif st == StorageType.Integer:
            dp.Set(sp.AsInteger())
        elif st == StorageType.Double:
            dp.Set(sp.AsDouble())
        elif st == StorageType.String:
            dp.Set(sp.AsString() or '')
    except Exception:
        pass


def copy_view_settings(src, dst, report):
    for attr in ('Scale', 'DetailLevel', 'Discipline'):
        try:
            setattr(dst, attr, getattr(src, attr))
        except Exception:
            pass

    _copy_param(src, dst, BuiltInParameter.VIEW_PHASE)
    _copy_param(src, dst, BuiltInParameter.VIEW_PHASE_FILTER)

    try:
        dst.SetViewRange(src.GetViewRange())
    except Exception as ex:
        report.warn('View range not copied: {}'.format(ex))

    # A scope box drives the crop automatically; otherwise copy the crop shape.
    scope = None
    try:
        sp = src.get_Parameter(BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
        if sp is not None:
            scope = sp.AsElementId()
    except Exception:
        pass

    try:
        dst.CropBoxActive = src.CropBoxActive
        dst.CropBoxVisible = src.CropBoxVisible
    except Exception:
        pass

    if scope is not None and scope != ElementId.InvalidElementId:
        _copy_param(src, dst, BuiltInParameter.VIEWER_VOLUME_OF_INTEREST_CROP)
    else:
        try:
            dst.CropBox = src.CropBox
        except Exception:
            pass
        _copy_nonrect_crop(src, dst)


def _copy_nonrect_crop(src, dst):
    """Copy a non-rectangular crop boundary if the source has one."""
    try:
        loops = src.GetCropRegionShapeManager().GetCropShape()
        if loops and loops.Count > 0:
            dst.GetCropRegionShapeManager().SetCropShape(loops[0])
    except Exception:
        pass
