# -*- coding: utf-8 -*-
"""Copy plan-view settings between plan views:
crop (including a rotated crop), scope box, view range, scale, detail level,
discipline, phase."""

import math

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
        # A plan view ignores rotation set through CropBox.Transform, so if the
        # source crop is rotated, rotate the destination crop element to match.
        try:
            angle = _crop_angle(src.CropBox.Transform)
            if abs(angle) > 1.0e-6:
                dst.Document.Regenerate()
                if _rotate_crop(dst, angle):
                    report.note('Matched the view\'s rotated crop.')
                else:
                    report.warn('The source crop is rotated but the rotation '
                                'could not be applied — rotate the crop by hand.')
        except Exception:
            pass


def _iv(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _crop_angle(transform):
    """Rotation of a crop box's transform about Z, in radians."""
    bx = transform.BasisX
    return math.atan2(bx.Y, bx.X)


def _rotate_crop(view, angle):
    """Rotate a plan view's crop region by `angle` (radians). Finds the crop
    element via the visibility-toggle trick, then RotateElement about its
    centre. Returns True on success."""
    from Autodesk.Revit.DB import (
        SubTransaction, FilteredElementCollector, Line, XYZ,
        ElementTransformUtils,
    )
    doc = view.Document

    # Identify the crop element: collect visible ids with the crop hidden, then
    # shown; the difference is the crop element. Do it in a rolled-back
    # sub-transaction so the visibility state is left untouched.
    crop_id = None
    sub = SubTransaction(doc)
    try:
        sub.Start()
        view.CropBoxVisible = False
        doc.Regenerate()
        hidden = set(_iv(i) for i in
                     FilteredElementCollector(doc, view.Id).ToElementIds())
        view.CropBoxVisible = True
        doc.Regenerate()
        for eid in FilteredElementCollector(doc, view.Id).ToElementIds():
            if _iv(eid) not in hidden:
                crop_id = eid
                break
        sub.RollBack()
    except Exception:
        try:
            sub.RollBack()
        except Exception:
            pass
        return False

    if crop_id is None:
        return False
    try:
        bbox = view.CropBox
        centre = bbox.Transform.OfPoint(bbox.Min.Add(bbox.Max).Multiply(0.5))
        axis = Line.CreateBound(centre, centre.Add(XYZ.BasisZ))
        ElementTransformUtils.RotateElement(doc, crop_id, axis, angle)
        return True
    except Exception:
        return False


def _copy_nonrect_crop(src, dst):
    """Copy a non-rectangular crop boundary if the source has one."""
    try:
        loops = src.GetCropRegionShapeManager().GetCropShape()
        if loops and loops.Count > 0:
            dst.GetCropRegionShapeManager().SetCropShape(loops[0])
    except Exception:
        pass
