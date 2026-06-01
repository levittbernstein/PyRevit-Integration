"""Geometry primitives and arc-to-line conversion."""
import math


def snap(value, grid):
    if grid <= 0:
        return value
    return round(value / grid) * grid


def dist(p1, p2):
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])


def midpoint(p1, p2):
    return ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)


def circle_from_three_points(p1, p2, p3):
    """Return (cx, cy, r) or None if collinear."""
    ax, ay = p1
    bx, by = p2
    cx, cy = p3
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = dist((ux, uy), p1)
    return (ux, uy, r)


def arc_angles(cx, cy, p_start, p_end, p_mid):
    """
    Return (start_angle_deg, end_angle_deg) going through p_mid,
    measured CCW from positive-x axis.
    """
    a_start = math.degrees(math.atan2(p_start[1] - cy, p_start[0] - cx)) % 360
    a_end   = math.degrees(math.atan2(p_end[1]   - cy, p_end[0]   - cx)) % 360
    a_mid   = math.degrees(math.atan2(p_mid[1]   - cy, p_mid[0]   - cx)) % 360

    # Determine sweep direction
    def _in_arc(a, a0, a1, ccw):
        if ccw:
            if a0 <= a1:
                return a0 <= a <= a1
            return a >= a0 or a <= a1
        else:
            if a1 <= a0:
                return a1 <= a <= a0
            return a >= a1 or a <= a0

    if _in_arc(a_mid, a_start, a_end, ccw=True):
        return (a_start, a_end, True)   # CCW
    return (a_start, a_end, False)       # CW


def arc_to_polyline(cx, cy, r, a_start_deg, a_end_deg, ccw=True, segments=32):
    """
    Approximate an arc as a list of (x, y) points.
    a_start_deg / a_end_deg are standard math angles (CCW from +x).
    """
    a0 = math.radians(a_start_deg)
    a1 = math.radians(a_end_deg)

    if ccw:
        if a1 <= a0:
            a1 += 2 * math.pi
    else:
        if a0 <= a1:
            a0 += 2 * math.pi
        a0, a1 = a1, a0   # swap so we iterate CCW then flip

    pts = []
    for i in range(segments + 1):
        t = i / segments
        angle = a0 + t * (a1 - a0)
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))

    if not ccw:
        pts.reverse()
    return pts


def polyline_to_pat_lines(pts, tile_w, tile_h):
    """
    Convert a polyline into a list of PAT line definitions.
    Each segment becomes one dash-line entry.
    Returns list of dicts: {angle, ox, oy, dx, dy, dash, gap}
    """
    entries = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        seg_len = dist((x0, y0), (x1, y1))
        if seg_len < 1e-6:
            continue

        angle_rad = math.atan2(y1 - y0, x1 - x0)
        angle_deg = math.degrees(angle_rad) % 360

        # Revit .pat: perpendicular offset direction is 90° CCW from line angle
        # We need dx (shift along line per tile row) and dy (perpendicular spacing)
        # For individual segment lines we use a large dy so they don't repeat visibly
        # within the tile — the tile repetition handles that.
        entries.append({
            'angle': angle_deg,
            'ox': x0,
            'oy': y0,
            'dx': tile_w,   # shift along line by full tile width each row
            'dy': tile_h,   # perpendicular repeat = full tile height
            'dash': seg_len,
            'gap': -(tile_w * 10),  # large negative gap = draw dash once per tile
        })
    return entries
