"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline

ARC_SEGMENTS = 64


def _segment_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h):
    """
    Convert a line segment (mm) to a PAT line-family entry dict (mm).

    PAT format per line:  angle, ox, oy, dx, dy, dash, gap
      angle   degrees — direction of the line family
      ox, oy  origin of the first line (anchors where the dash starts)
      dx      stagger along the line between successive parallel lines
      dy      perpendicular spacing between parallel lines  (always > 0)
      dash    drawn length
      gap     skip length  (negative in .pat)

    ── Dominant-direction tiling ─────────────────────────────────────────────
    We choose period and dy based on whichever tile axis the segment crosses
    most directly.  This guarantees that the family tiles *exactly* in the
    dominant direction — one dash per tile, no drift, no scatter.

    |cosθ| ≥ sinθ  (≤45° from horizontal, "horizontal-dominant"):
        period  = tile_w / |cosθ|       tiles exactly in x
        dy      = tile_h · |cosθ|
        dx      = tile_h · sinθ         phase correction for y-tiling

    sinθ  > |cosθ| (>45° from horizontal, "vertical-dominant"):
        period  = tile_h / sinθ         tiles exactly in y
        dy      = tile_w · sinθ
        dx      = −tile_w · cosθ        phase correction for x-tiling

    dy · period = tile_w · tile_h in both cases → one dash per tile area.

    The dx values are derived so that shifting one full tile width (for
    vertical-dominant) or height (for horizontal-dominant) lands on the
    adjacent row of the family at exactly the right along-line phase —
    eliminating the positional drift that causes scattered arc segments.
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
    cos_t = math.cos(theta)   # ≥ 0 for θ∈[0°,90°], ≤ 0 for θ∈(90°,180°)
    sin_t = math.sin(theta)   # ≥ 0 for all θ∈[0°,180°)
    EPS   = 1e-6

    if abs(cos_t) >= sin_t:       # horizontal-dominant
        period = tile_w / max(abs(cos_t), EPS)
        dy     = tile_h * abs(cos_t)
        dx     = tile_h * sin_t
    else:                         # vertical-dominant
        period = tile_h / max(sin_t, EPS)
        dy     = tile_w * sin_t
        dx     = -tile_w * cos_t  # cos_t ≤ 0 here, so dx ≥ 0

    if dy < EPS or period < EPS:
        return None

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
    return f"{v:.8g}"


def export_pat(proj, path):
    """
    Write a Revit MODEL hatch .pat file.

    Coordinates are in millimetres — Revit metric projects read MODEL hatch
    .pat values in mm.  Import the pattern with Scale = 1.0 in Revit's Fill
    Pattern dialog for a 1:1 match with the tile dimensions you drew.
    """
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
                f"{_fmt(e['angle'])},"
                f"{_fmt(e['ox'])},{_fmt(e['oy'])},"
                f"{_fmt(e['dx'])},{_fmt(e['dy'])},"
                f"{_fmt(e['dash'])},{_fmt(e['gap'])}"
            )

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
