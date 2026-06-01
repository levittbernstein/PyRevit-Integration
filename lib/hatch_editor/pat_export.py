"""
Export project to Revit/AutoCAD .pat format.

── Why this is non-trivial ────────────────────────────────────────────────────
A .pat MODEL hatch is a set of infinite, regularly-spaced dashed lines.  For a
drawn segment to repeat correctly across a W×H tile, its line family must be
invariant under both tile-translation vectors (W,0) and (0,H).  This is only
possible when the segment's direction is *commensurate* with the tile lattice —
a rational slope p·W : q·H.  Arbitrary (irrational) angles make the line offsets
dense, which is why naive arc export scatters.

── The approach ───────────────────────────────────────────────────────────────
1. Snap every vertex (lines and arc-approximation points) to a fine sub-grid of
   the tile, so each segment direction is an exact lattice-commensurate ratio.
2. For each segment, build the *exact* line family from the tile lattice:
       (m,k)  smallest integers with (m·W, k·H) parallel to the segment
       T      = |(m·W, k·H)|                         period along the line
       dy     = (W·H) / T                            perpendicular spacing
       dx     = projection of the complementary lattice basis vector (det ±1)
   Anchored at the segment endpoint that comes first along the line direction,
   so the dash covers the segment exactly and consecutive segments join up.
3. Every family is self-checked for tile-invariance.

A finer sub-grid yields more distinct directions → smoother curves.  At ~0.5mm
resolution an arc resolves to ~12-24 directions, reading as a smooth curve.

Coordinates are written in inches (mm ÷ 25.4): Revit MODEL hatches use inches
internally even in metric projects.  Import with Fill Pattern Scale = 1.0.
"""
import math
from .geometry import arc_to_polyline

# Arcs are resampled to ~TARGET_SEG_MM chords on a GRID_RES_MM sub-grid.
# Why these values matter:
#  • Dashes must be long enough that Revit renders them as dashes, not solid
#    lines.  Over-sampling (tiny 0.5mm chords) makes Revit draw the whole line
#    family solid → a "furry fan".  ~4mm chords render correctly.
#  • A 1mm grid gives enough direction variety for smooth curves (7-11 angles
#    on a typical arc) while keeping dy healthy (≈20mm+), so the family stays
#    one-dash-per-tile with no clutter.
GRID_RES_MM   = 1.0        # export sub-grid resolution (mm)
# Revit appears to enforce a minimum rendered dash length (~8mm): dashes shorter
# than that get lengthened, overshooting their chord and producing tangential
# "spurs"/fans at arc vertices. Keep chords above that threshold.
TARGET_SEG_MM = 10.0       # target arc chord length (mm) — above Revit's min dash
_TOL          = 1e-5
_MM_TO_IN     = 1.0 / 25.4


def _resample_polyline(pts, target_len):
    """Resample a polyline to roughly equal chords of `target_len`."""
    if len(pts) < 2:
        return pts
    # cumulative arc length
    cum = [0.0]
    for i in range(1, len(pts)):
        cum.append(cum[-1] + math.hypot(pts[i][0] - pts[i-1][0],
                                        pts[i][1] - pts[i-1][1]))
    total = cum[-1]
    if total < 1e-9:
        return [pts[0]]
    n = max(2, int(round(total / target_len)) + 1)
    out = []
    j = 0
    for s in range(n):
        d = total * s / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        t = 0.0 if seg < 1e-12 else (d - cum[j]) / seg
        x = pts[j][0] + t * (pts[j + 1][0] - pts[j][0])
        y = pts[j][1] + t * (pts[j + 1][1] - pts[j][1])
        out.append((x, y))
    return out


def _ext_gcd(a, b):
    if b == 0:
        return (abs(a), 1 if a >= 0 else -1, 0)
    g, x, y = _ext_gcd(b, a % b)
    return (g, y, x - (a // b) * y)


def _smallest_lattice_dir(ax, by, W, H):
    """
    Smallest integers (m, k) with (m·W, k·H) parallel to a segment of real
    direction (ax, by).  Parallel ⇔ m·W·by = k·H·ax ⇔ m/k = (H·ax)/(W·by).
    (ax, by) is grid-commensurate, so a small exact solution exists.
    """
    if abs(by) < 1e-12:
        return (1, 0)
    if abs(ax) < 1e-12:
        return (0, 1)
    ratio = (H * ax) / (W * by)
    for k in range(1, 8192):
        m = ratio * k
        if abs(m - round(m)) < 1e-9:
            m = int(round(m))
            g = math.gcd(abs(m), k) or 1
            return (m // g, k // g)
    return None


def _grid_family(ix0, iy0, ix1, iy1, W, H, gw, gh):
    """Build a tile-invariant PAT family for a grid-snapped segment, or None."""
    dix, diy = ix1 - ix0, iy1 - iy0
    if dix == 0 and diy == 0:
        return None

    ax, by = dix * gw, diy * gh
    sol = _smallest_lattice_dir(ax, by, W, H)
    if sol is None:
        return None
    m, k = sol

    T = math.hypot(m * W, k * H)
    if T < 1e-9:
        return None
    dy = (W * H) / T
    ux, uy = (m * W) / T, (k * H) / T

    _, r0, s0 = _ext_gcd(m, k)
    r, s = -s0, r0
    dx = (r * W) * ux + (s * H) * uy

    dash = math.hypot(ax, by)
    angle = math.degrees(math.atan2(k * H, m * W)) % 180.0

    # Anchor at the endpoint that comes first along the (normalised) direction,
    # so the dash extends *onto* the segment — otherwise half the segments draw
    # their dash backwards and the polyline fails to join up.
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    ax0, ay0 = ix0 * gw, iy0 * gh
    bx0, by0 = ix1 * gw, iy1 * gh
    if (ax0 * ca + ay0 * sa) <= (bx0 * ca + by0 * sa):
        ox, oy = ax0, ay0
    else:
        ox, oy = bx0, by0

    return {
        'angle': angle,
        'ox': ox, 'oy': oy,
        'dx': dx, 'dy': dy,
        'dash': dash, 'gap': -(T - dash),
        '_T': T,
    }


def _family_tiles(f, W, H):
    """Self-check: family reproduces its dash under (W,0) and (0,H)."""
    if f is None:
        return True
    th = math.radians(f['angle'])
    c, s = math.cos(th), math.sin(th)
    ox, oy, dx, dy, T = f['ox'], f['oy'], f['dx'], f['dy'], f['_T']

    def covered(x, y):
        rx, ry = x - ox, y - oy
        n = (rx * (-s) + ry * c) / dy
        if abs(n - round(n)) > _TOL:
            return False
        along = (rx * c + ry * s) - round(n) * dx
        kk = along / T
        return abs(kk - round(kk)) < _TOL

    return covered(ox + W, oy) and covered(ox, oy + H)


def _arc_segment_families(ix0, iy0, ix1, iy1, W, H, gw, gh):
    """
    Arc segment → its exact tiling family.

    No L-shape fallback: decomposing a steep segment into H+V steps would
    create a short (< Revit-min-dash) leg that Revit lengthens into a spur.
    The direct family tiles to one dash per tile even at small dy; the worst
    case for an unusually steep segment is a faint parallel echo, which is
    far less objectionable than a spur.
    """
    f = _grid_family(ix0, iy0, ix1, iy1, W, H, gw, gh)
    if f is None or not _family_tiles(f, W, H):
        return []
    return [f]


def _grid_params(proj):
    """Resolve the export sub-grid (~GRID_RES_MM, dividing the tile evenly)."""
    W = proj['tile_w']
    H = proj['tile_h']
    Nx = max(1, int(round(W / GRID_RES_MM)))
    Ny = max(1, int(round(H / GRID_RES_MM)))
    return W, H, W / Nx, H / Ny


def _element_to_pat_entries(el, W, H, gw, gh):
    """Element → PAT families.  y is flipped (H - y): canvas y-down, .pat y-up."""
    def gx(x):
        return int(round(x / gw))

    def gy(y):
        return int(round((H - y) / gh))

    t = el['type']

    if t == 'line':
        # Straight lines are drawn exactly (no low-dy fallback): a shallow line
        # legitimately has many parallel tile-copies; stepping it would be wrong.
        f = _grid_family(gx(el['x0']), gy(el['y0']),
                         gx(el['x1']), gy(el['y1']), W, H, gw, gh)
        return [f] if f else []

    elif t in ('arc_cr', 'arc_3pt'):
        # Dense sample, then resample to ~TARGET_SEG_MM chords so dashes are
        # long enough for Revit to render them as dashes (not solid lines).
        dense = arc_to_polyline(
            el['cx'], el['cy'], el['r'],
            el['a_start'], el['a_end'], el['ccw'],
            segments=200,
        )
        pts = _resample_polyline(dense, TARGET_SEG_MM)

        snapped = []
        for x, y in pts:
            g = (gx(x), gy(y))
            if not snapped or g != snapped[-1]:
                snapped.append(g)

        families = []
        for i in range(len(snapped) - 1):
            families += _arc_segment_families(
                snapped[i][0], snapped[i][1],
                snapped[i + 1][0], snapped[i + 1][1],
                W, H, gw, gh)
        return families

    return []


def _fmt(v):
    return f"{v:.8g}"


def export_pat(proj, path, scale=_MM_TO_IN):
    """
    Write a Revit MODEL hatch .pat file (coordinates in inches).
    Import into Revit with Fill Pattern Scale = 1.0; an 80mm tile appears 80mm.
    """
    name = proj['name'].replace(' ', '_')
    W, H, gw, gh = _grid_params(proj)

    lines_out = [
        f"*{name},{proj['name']}",
        ";%TYPE=MODEL",
    ]

    for el in proj['elements']:
        for e in _element_to_pat_entries(el, W, H, gw, gh):
            if e is None:
                continue
            sc = scale
            lines_out.append(
                f"{_fmt(e['angle'])},"
                f"{_fmt(e['ox'] * sc)},{_fmt(e['oy'] * sc)},"
                f"{_fmt(e['dx'] * sc)},{_fmt(e['dy'] * sc)},"
                f"{_fmt(e['dash'] * sc)},{_fmt(e['gap'] * sc)}"
            )

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
