# -*- coding: utf-8 -*-
"""Mirror each room's boundary as AreaBoundaryLines in the area plan.

Room boundary loops (Room.GetBoundarySegments) are flattened onto the level
plane and de-duplicated so a wall shared by two rooms yields a single boundary
line. De-duplication is also seeded with the area plan's EXISTING boundary
lines, so re-running the tool never lays a second line on top of one already
there.
"""

from Autodesk.Revit.DB import (
    SpatialElementBoundaryOptions, SketchPlane, Plane, XYZ, Line, Arc,
    FilteredElementCollector, CurveElement,
)


def _flat(pt, z):
    return XYZ(pt.X, pt.Y, z)


def _flatten_curve(curve, z):
    """Project a boundary curve onto the z-plane. Lines and arcs are handled
    directly; anything else is tessellated into straight segments."""
    try:
        if isinstance(curve, Line):
            return [Line.CreateBound(_flat(curve.GetEndPoint(0), z),
                                     _flat(curve.GetEndPoint(1), z))]
        if isinstance(curve, Arc):
            p0 = _flat(curve.GetEndPoint(0), z)
            p1 = _flat(curve.GetEndPoint(1), z)
            pm = _flat(curve.Evaluate(0.5, True), z)
            return [Arc.Create(p0, p1, pm)]
        pts = curve.Tessellate()
        out = []
        for i in range(len(pts) - 1):
            a = _flat(pts[i], z)
            b = _flat(pts[i + 1], z)
            if a.DistanceTo(b) > 1e-6:
                out.append(Line.CreateBound(a, b))
        return out
    except Exception:
        return []


def _key(curve):
    """Undirected endpoint key for de-duplication (3-decimal feet ~ 0.3 mm)."""
    a = curve.GetEndPoint(0)
    b = curve.GetEndPoint(1)
    ra = (round(a.X, 3), round(a.Y, 3))
    rb = (round(b.X, 3), round(b.Y, 3))
    return tuple(sorted([ra, rb]))


def existing_boundary_keys(doc, area_plan):
    """Endpoint keys of area boundary lines already in the view."""
    keys = set()
    for ce in FilteredElementCollector(doc, area_plan.Id).OfClass(CurveElement):
        try:
            c = ce.GeometryCurve
            if c is not None:
                keys.add(_key(c))
        except Exception:
            continue
    return keys


def create_area_boundaries(doc, rooms, area_plan, level, report,
                           existing_keys=None):
    """Create de-duplicated area boundary lines mirroring the rooms.

    existing_keys: endpoint keys already present in the view — seeded so a
    re-run does not duplicate boundaries.
    """
    z = level.Elevation
    sketch = SketchPlane.Create(
        doc, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z)))
    opts = SpatialElementBoundaryOptions()

    seen = set(existing_keys or [])
    created = 0
    skipped_existing = 0
    failed = 0

    for room in rooms:
        try:
            loops = room.GetBoundarySegments(opts)
        except Exception:
            loops = None
        if not loops:
            continue
        for loop in loops:
            for seg in loop:
                for flat in _flatten_curve(seg.GetCurve(), z):
                    k = _key(flat)
                    if k in seen:
                        skipped_existing += 1
                        continue
                    seen.add(k)
                    try:
                        doc.Create.NewAreaBoundaryLine(sketch, flat, area_plan)
                        created += 1
                    except Exception:
                        failed += 1

    report.count('boundary lines', created)
    if skipped_existing:
        report.count('boundaries already present', skipped_existing)
    if failed:
        report.warn('{} boundary segment(s) could not be drawn.'.format(failed))
    return created
