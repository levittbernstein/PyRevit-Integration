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

        try:
            angle = _crop_angle(src.CropBox.Transform)
        except Exception:
            angle = 0.0

        if abs(angle) > 1.0e-6:
            # Rotated rectangular crop: a plan view ignores rotation set through
            # CropBox.Transform, so rotate the crop ELEMENT to match. Do NOT also
            # copy the crop shape — that boundary is already rotated, so copying
            # it here and rotating the element would skew the boundary twice
            # while the content only rotates once.
            try:
                dst.Document.Regenerate()
                if _match_rotated_crop(dst, src.CropBox, angle):
                    report.note('Matched the view\'s rotated crop.')
                else:
                    report.warn('The source crop is rotated but the rotation '
                                'could not be applied — rotate the crop by hand.')
            except Exception:
                pass
        else:
            _copy_nonrect_crop(src, dst)


def _iv(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _crop_angle(transform):
    """Rotation of a crop box's transform about Z, in radians."""
    bx = transform.BasisX
    return math.atan2(bx.Y, bx.X)


def _find_crop_element(view):
    """The view's crop region element, via the visibility-toggle trick (collect
    visible ids with the crop hidden, then shown; the difference is the crop).
    Done in a rolled-back sub-transaction so visibility is left untouched."""
    from Autodesk.Revit.DB import SubTransaction, FilteredElementCollector
    doc = view.Document
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
        return None
    return crop_id


def _cropbox_centre(cropbox):
    """World centre of a BoundingBoxXYZ (its transform IS respected on read for
    position — only rotation is dropped by ViewPlan on write)."""
    return cropbox.Transform.OfPoint(
        cropbox.Min.Add(cropbox.Max).Multiply(0.5))


def _match_rotated_crop(view, src_cropbox, angle):
    """Make the view's crop match a rotated source crop.

    ViewPlan drops rotation on CropBox assignment but keeps position, so:
      * read the destination crop's current centre (c0) from view.CropBox;
      * rotate the crop element about c0 (orientation only — centre stays at c0);
      * translate the crop so c0 moves onto the source crop's true centre.
    No reliance on the crop element's bounding box (which comes back empty)."""
    from Autodesk.Revit.DB import Line, XYZ, ElementTransformUtils
    doc = view.Document

    target = _cropbox_centre(src_cropbox)          # where the crop should sit
    try:
        c0 = _cropbox_centre(view.CropBox)         # where it sits now
    except Exception:
        return False

    crop_id = _find_crop_element(view)
    if crop_id is None:
        return False

    try:
        axis = Line.CreateBound(c0, c0.Add(XYZ.BasisZ))
        ElementTransformUtils.RotateElement(doc, crop_id, axis, angle)
    except Exception:
        return False

    delta = XYZ(target.X - c0.X, target.Y - c0.Y, 0.0)
    if delta.GetLength() > 1.0e-6:
        try:
            ElementTransformUtils.MoveElement(doc, crop_id, delta)
        except Exception:
            pass
    return True


def _copy_nonrect_crop(src, dst):
    """Copy a non-rectangular crop boundary if the source has one."""
    try:
        loops = src.GetCropRegionShapeManager().GetCropShape()
        if loops and loops.Count > 0:
            dst.GetCropRegionShapeManager().SetCropShape(loops[0])
    except Exception:
        pass
