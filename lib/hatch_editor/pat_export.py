"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline, dist

ARC_SEGMENTS = 48

# Revit MODEL hatch .pat files use feet as the internal coordinate unit,
# regardless of the project's display units.  All mm values must be divided
# by 304.8 before writing.
_MM_TO_FEET = 1.0 / 304.8


def _segment_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h):
    """
    Convert a line segment (in mm) to a PAT line-family entry dict (also mm).

    PAT format: angle, ox, oy, dx, dy, dash, gap
      angle   line direction in degrees
      ox, oy  origin of the first line in the family
      dx      stagger along the line between successive parallel lines
      dy      perpendicular spacing between parallel lines (> 0)
      dash    drawn dash length
      gap     skip length (negative in .pat convention)

    Period strategy — dominant direction:
      Use the tile dimension that the line crosses most directly to set
      the repeat period. This guarantees the pattern tiles exactly in
      the primary axis, minimising positional drift in adjacent tiles.

      |cos θ| ≥ sin θ  (≤45° from horizontal)  →  period = tile_w / |cos θ|
      sin θ  >  |cos θ| (>45° from horizontal)  →  period = tile_h / sin θ

      dy = tile_w * tile_h / period  (ensures exactly one dash per tile area)
      dx = 0  (no stagger; dy is always close to the tile size)
    """
    seg_len = math.hypot(x1 - x0, y1 - y0)
    if seg_len < 1e-9:
        return None

    # Normalise angle to [0°, 180°): a line's direction is ambiguous by 180°.
    angle_rad = math.atan2(y1 - y0, x1 - x0)
    angle_deg = math.degrees(angle_rad)
    if angle_deg < 0.0:
        angle_deg += 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0
    elif angle_deg >= 180.0:
        angle_deg -= 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0

    theta = math.radians(angle_deg)
    cos_t = math.cos(theta)   # ≥ 0 for θ ∈ [0°, 90°], ≤ 0 for (90°, 180°)
    sin_t = math.sin(theta)   # ≥ 0 for all θ ∈ [0°, 180°)
    EPS   = 1e-6

    # Dominant-direction period: tiles exactly in whichever axis the line
    # crosses most directly, minimising positional error in adjacent tiles.
    if abs(cos_t) >= sin_t:          # closer to horizontal
        period = tile_w / max(abs(cos_t), EPS)
    else:                            # closer to vertical
        period = tile_h / max(sin_t, EPS)

    dy = (tile_w * tile_h) / period  # one dash per tile area
    dx = 0.0

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
    """Format a float cleanly — up to 8 significant figures, no trailing zeros."""
    s = f"{v:.8g}"
    return s


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
            # Convert all mm values to feet (Revit internal unit for MODEL hatches)
            sc = _MM_TO_FEET
            lines_out.append(
                f"{_fmt(e['angle'])},"
                f"{_fmt(e['ox']*sc)},{_fmt(e['oy']*sc)},"
                f"{_fmt(e['dx']*sc)},{_fmt(e['dy']*sc)},"
                f"{_fmt(e['dash']*sc)},{_fmt(e['gap']*sc)}"
            )

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
