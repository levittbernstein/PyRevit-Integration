"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline

# Use more arc segments so the H/V projection approximation is fine-grained.
# Each segment is projected to horizontal or vertical (see below), so more
# segments = smoother apparent curve.
ARC_SEGMENTS = 8   # rough-polygon approximation — readable as a circle at arch scale


# ── Straight-line entry ────────────────────────────────────────────────────────

def _line_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h):
    """
    Convert a straight line segment to a PAT entry using the dominant-direction
    formula with the phase-correcting dx stagger.

    Horizontal-dominant (|cosθ| ≥ sinθ):
        period = tile_w/|cosθ|,  dy = tile_h·|cosθ|,  dx = tile_h·sinθ
        → tiles exactly in y

    Vertical-dominant (sinθ > |cosθ|):
        period = tile_h/sinθ,    dy = tile_w·sinθ,     dx = −tile_w·cosθ
        → tiles exactly in x
    """
    seg_len = math.hypot(x1 - x0, y1 - y0)
    if seg_len < 1e-9:
        return None

    angle_rad = math.atan2(y1 - y0, x1 - x0)
    angle_deg = math.degrees(angle_rad)
    if angle_deg < 0.0:
        angle_deg += 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0
    elif angle_deg >= 180.0:
        angle_deg -= 180.0
        x0, y0, x1, y1 = x1, y1, x0, y0

    theta = math.radians(angle_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    EPS   = 1e-6

    if abs(cos_t) >= sin_t:
        period = tile_w / max(abs(cos_t), EPS)
        dy     = tile_h * abs(cos_t)
        dx     = tile_h * sin_t
    else:
        period = tile_h / max(sin_t, EPS)
        dy     = tile_w * sin_t
        dx     = -tile_w * cos_t

    if dy < EPS or period < EPS:
        return None

    return {
        'angle': angle_deg,
        'ox': x0, 'oy': y0,
        'dx': dx, 'dy': dy,
        'dash': seg_len,
        'gap': -(period - seg_len),
    }


# ── Element dispatcher ─────────────────────────────────────────────────────────

def _element_to_pat_entries(el, tile_w, tile_h):
    t = el['type']

    if t == 'line':
        entry = _line_to_pat_entry(
            el['x0'], el['y0'], el['x1'], el['y1'], tile_w, tile_h)
        return [entry] if entry else []

    elif t in ('arc_cr', 'arc_3pt'):
        # Approximate the arc as a polyline then export each segment using the
        # same actual-angle entry function as straight lines.  With 8 segments
        # the arc appears as a recognisable rough polygon at architectural scale.
        # The y-axis is flipped (tile_h - y) so angles match Revit's y-up coord
        # system; without this, arcs appear mirrored vertically.
        pts = arc_to_polyline(
            el['cx'], el['cy'], el['r'],
            el['a_start'], el['a_end'], el['ccw'],
            segments=ARC_SEGMENTS,
        )
        entries = []
        for i in range(len(pts) - 1):
            x0, y0 = pts[i][0],   tile_h - pts[i][1]
            x1, y1 = pts[i+1][0], tile_h - pts[i+1][1]
            entry = _line_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h)
            if entry:
                entries.append(entry)
        return entries

    return []


# ── Formatter ──────────────────────────────────────────────────────────────────

def _fmt(v):
    return f"{v:.8g}"


# ── Public export function ─────────────────────────────────────────────────────

def export_pat(proj, path, scale=1.0):
    """
    Write a Revit MODEL hatch .pat file.

    scale   multiply every coordinate and length by this factor before writing.
            Use scale=1.0 for mm (default).  If the pattern appears too large
            in Revit at scale=1, try scale=0.001 (metres) or other values —
            the correct factor depends on your Revit project unit setup.
            Import the pattern with Scale=1.0 in Revit's Fill Pattern dialog.
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
            sc = scale
            lines_out.append(
                f"{_fmt(e['angle'])},"
                f"{_fmt(e['ox']*sc)},{_fmt(e['oy']*sc)},"
                f"{_fmt(e['dx']*sc)},{_fmt(e['dy']*sc)},"
                f"{_fmt(e['dash']*sc)},{_fmt(e['gap']*sc)}"
            )

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
