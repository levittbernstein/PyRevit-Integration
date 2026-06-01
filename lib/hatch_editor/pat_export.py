"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline, dist

ARC_SEGMENTS = 48


def _segment_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h):
    """
    Convert a single line segment to a PAT line-family entry.

    PAT format: angle, ox, oy, dx, dy, dash, gap
      angle     line direction in degrees
      ox, oy    origin of the first line in this family
      dx        stagger along the line between successive parallel lines
      dy        perpendicular spacing between parallel lines (must be > 0)
      dash      drawn length
      gap       skipped length (negative in .pat)

    The repeat vector used is T2 = (0, tile_h), so dy = tile_h·|cos θ| and
    dx = tile_h·sin θ. The along-line period is tile_w·|cos θ| + tile_h·|sin θ|,
    which equals the projection of the tile perimeter onto the line and is
    always ≥ the maximum possible segment length inside the tile.

    Angles are normalised to [0°, 180°) so dy is always positive.
    """
    seg_len = math.hypot(x1 - x0, y1 - y0)
    if seg_len < 1e-9:
        return None

    # Normalise direction to [0°, 180°) — a line's direction is ambiguous by 180°
    angle_rad = math.atan2(y1 - y0, x1 - x0)
    angle_deg = math.degrees(angle_rad)
    if angle_deg < 0.0:
        angle_deg += 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0
    elif angle_deg >= 180.0:
        angle_deg -= 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0

    theta  = math.radians(angle_deg)
    cos_t  = math.cos(theta)   # ≥ 0 for θ in [0°, 90°], ≤ 0 for (90°, 180°)
    sin_t  = math.sin(theta)   # ≥ 0 for all θ in [0°, 180°)
    EPS    = 1e-6

    # Period along line = projection of the tile onto the line direction.
    # dy * period = tile_w * tile_h guarantees exactly one dash per tile area
    # for ALL angles.  The old formula dy = tile_h * |cos θ| collapsed to ~0
    # for near-vertical segments (θ ≈ 90°), causing Revit to draw dozens of
    # unwanted parallel copies per tile — producing the scattered-lines look.
    period = tile_w * abs(cos_t) + tile_h * sin_t
    if period < EPS:
        return None
    dy = (tile_w * tile_h) / period
    dx = 0.0  # no stagger; dy ≥ min(tile_w, tile_h)/√2, so at most one copy per tile

    # gap is the space between the end of this dash and the start of the next
    gap = -(period - seg_len)

    return {
        'angle': angle_deg,
        'ox': x0, 'oy': y0,
        'dx': dx,  'dy': dy,
        'dash': seg_len,
        'gap': gap,
    }


def _element_to_pat_entries(el, tile_w, tile_h):
    t = el['type']

    if t == 'line':
        entry = _segment_to_pat_entry(
            el['x0'], el['y0'], el['x1'], el['y1'], tile_w, tile_h)
        return [entry] if entry else []

    elif t in ('arc_cr', 'arc_3pt'):
        pts = arc_to_polyline(
            el['cx'], el['cy'], el['r'],
            el['a_start'], el['a_end'], el['ccw'],
            segments=ARC_SEGMENTS,
        )
        entries = []
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            entry = _segment_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h)
            if entry:
                entries.append(entry)
        return entries

    return []


def _fmt(v):
    """Format a float: drop trailing zeros, cap at 6 significant figures."""
    rounded = float(f"{v:.6g}")
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded}"


def export_pat(proj, path):
    name   = proj['name'].replace(' ', '_')
    tile_w = proj['tile_w']
    tile_h = proj['tile_h']

    lines_out = [
        f"*{name},{proj['name']}",
        ";%TYPE=MODEL",
    ]

    for el in proj['elements']:
        for e in _element_to_pat_entries(el, tile_w, tile_h):
            lines_out.append(
                f"{_fmt(e['angle'])},{_fmt(e['ox'])},{_fmt(e['oy'])},"
                f"{_fmt(e['dx'])},{_fmt(e['dy'])},{_fmt(e['dash'])},{_fmt(e['gap'])}"
            )

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
