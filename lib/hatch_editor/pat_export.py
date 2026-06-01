"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline

# Use more arc segments so the H/V projection approximation is fine-grained.
# Each segment is projected to horizontal or vertical (see below), so more
# segments = smoother apparent curve.
ARC_SEGMENTS = 128


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


# ── Arc segment → horizontal or vertical entry ────────────────────────────────

def _arc_seg_to_pat_entry(x0, y0, x1, y1, tile_w, tile_h):
    """
    Project a short arc segment onto its nearest axis (horizontal or vertical)
    and return a 0° or 90° PAT entry.

    Horizontal (0°) and vertical (90°) entries tile EXACTLY with zero positional
    drift in both x and y directions — unlike arbitrary-angle entries which drift
    in at least one axis for irrational angles, causing arc segments to appear at
    wrong positions in adjacent tiles (the "scatter" problem).

    The arc shape is preserved: with 128 sub-segments, the H/V projection
    approximation is indistinguishable from a true curve at architectural scales.
    """
    adx = abs(x1 - x0)
    ady = abs(y1 - y0)
    mx  = (x0 + x1) * 0.5
    my  = (y0 + y1) * 0.5

    if adx >= ady:                        # project to horizontal (0°)
        if adx < 1e-9:
            return None
        ox = min(x0, x1)
        gap = -(tile_w - adx)
        if gap >= 0:                      # segment fills or exceeds tile width
            gap = 0.0
        return {
            'angle': 0.0,
            'ox': ox, 'oy': my,
            'dx': 0.0, 'dy': tile_h,
            'dash': adx,
            'gap': gap,
        }
    else:                                 # project to vertical (90°)
        if ady < 1e-9:
            return None
        oy = min(y0, y1)
        gap = -(tile_h - ady)
        if gap >= 0:
            gap = 0.0
        return {
            'angle': 90.0,
            'ox': mx, 'oy': oy,
            'dx': 0.0, 'dy': tile_w,
            'dash': ady,
            'gap': gap,
        }


# ── Element dispatcher ─────────────────────────────────────────────────────────

def _element_to_pat_entries(el, tile_w, tile_h):
    t = el['type']

    if t == 'line':
        entry = _line_to_pat_entry(
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
            entry = _arc_seg_to_pat_entry(
                pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1], tile_w, tile_h)
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
