"""
Export project to Revit/AutoCAD .pat format.

── Why this is non-trivial ────────────────────────────────────────────────────
A .pat MODEL hatch is a set of infinite, regularly-spaced dashed lines.  For a
drawn segment to repeat correctly across a W×H tile, its line family must be
invariant under both tile-translation vectors (W,0) and (0,H).  This is only
possible when the segment's direction is *commensurate* with the tile lattice —
i.e. a rational slope p·W : q·H.  Arbitrary (irrational) angles make the
perpendicular line offsets dense, which is why earlier versions produced
scattered, mis-placed arc segments.

── The approach ───────────────────────────────────────────────────────────────
1. Snap every vertex (lines and arc-approximation points alike) to a sub-grid of
   the tile, so each segment direction becomes an exact integer ratio that tiles.
2. For each segment, compute the *exact* line family from the tile lattice:
       T  (period)  = length of the smallest lattice vector along the segment
       dy (spacing) = (W·H) / T            [covolume / period]
       dx (stagger) = projection of the complementary lattice basis vector
   This is mathematically guaranteed to tile (verified by a self-check).
3. As insurance, any segment whose family fails the self-check is decomposed
   into an L-shaped horizontal+vertical pair, which always tiles.

Coordinates are written in inches (mm ÷ 25.4 by default) because Revit MODEL
hatches use inches as the internal unit; import with Fill Pattern Scale = 1.0.
"""
import math
from .geometry import arc_to_polyline

ARC_SEGMENTS = 48      # arc approximation points (snapped + de-duplicated after)
SUBDIV       = 4       # sub-grid divisions per project snap-grid step
_TILE_OK_TOL = 1e-5


def _ext_gcd(a, b):
    """Extended Euclid: return (g, x, y) with a*x + b*y = g."""
    if b == 0:
        return (abs(a), 1 if a >= 0 else -1, 0)
    g, x, y = _ext_gcd(b, a % b)
    return (g, y, x - (a // b) * y)


def _smallest_lattice_dir(ax, by):
    """
    Smallest integers (m, k) such that (m·W, k·H) is parallel to a segment whose
    direction is (ax, by) in real mm.  ax, by are already grid-commensurate, so
    the ratio ax:by is rational and a small solution exists.
    """
    if abs(by) < 1e-12:
        return (1, 0)
    if abs(ax) < 1e-12:
        return (0, 1)
    ratio = ax / by
    for k in range(1, 4096):
        m = ratio * k
        if abs(m - round(m)) < 1e-9:
            m = int(round(m))
            g = math.gcd(abs(m), k) or 1
            return (m // g, k // g)
    return None


def _grid_family(ix0, iy0, ix1, iy1, W, H, gw, gh):
    """
    Build a PAT line-family dict for a grid-snapped segment
    (integer grid coords ix/iy, cell size gw×gh).  Returns None if degenerate.
    """
    dix, diy = ix1 - ix0, iy1 - iy0
    if dix == 0 and diy == 0:
        return None

    # real direction
    ax, by = dix * gw, diy * gh
    sol = _smallest_lattice_dir(ax, by)
    if sol is None:
        return None
    m, k = sol

    T = math.hypot(m * W, k * H)        # period along the line
    if T < 1e-9:
        return None
    dy = (W * H) / T                    # perpendicular spacing (covolume / period)

    ux, uy = (m * W) / T, (k * H) / T   # unit vector along the line

    # complementary lattice basis vector (r·W, s·H) with det = ±1
    _, r0, s0 = _ext_gcd(m, k)
    r, s = -s0, r0
    dx = (r * W) * ux + (s * H) * uy    # stagger between successive parallel lines

    dash = math.hypot(ax, by)
    angle = math.degrees(math.atan2(k * H, m * W)) % 180.0

    return {
        'angle': angle,
        'ox': ix0 * gw, 'oy': iy0 * gh,
        'dx': dx, 'dy': dy,
        'dash': dash, 'gap': -(T - dash),
        '_T': T,
    }


def _family_tiles(f, W, H):
    """Self-check: does this family reproduce its dash under (W,0) and (0,H)?"""
    if f is None:
        return True
    th = math.radians(f['angle'])
    c, s = math.cos(th), math.sin(th)
    ox, oy, dx, dy, T = f['ox'], f['oy'], f['dx'], f['dy'], f['_T']

    def covered(x, y):
        rx, ry = x - ox, y - oy
        perp = rx * (-s) + ry * c
        n = perp / dy
        if abs(n - round(n)) > _TILE_OK_TOL:
            return False
        along = (rx * c + ry * s) - round(n) * dx
        kk = along / T
        return abs(kk - round(kk)) < _TILE_OK_TOL

    return covered(ox + W, oy) and covered(ox, oy + H)


def _segment_families(ix0, iy0, ix1, iy1, W, H, gw, gh):
    """One segment → list of tiling families (with L-shape fallback)."""
    f = _grid_family(ix0, iy0, ix1, iy1, W, H, gw, gh)
    if _family_tiles(f, W, H):
        return [f] if f else []

    # Fallback: L-shape (horizontal then vertical) — axis-aligned always tiles.
    out = []
    fh = _grid_family(ix0, iy0, ix1, iy0, W, H, gw, gh)
    fv = _grid_family(ix1, iy0, ix1, iy1, W, H, gw, gh)
    for ff in (fh, fv):
        if ff:
            out.append(ff)
    return out


def _grid_params(proj):
    """Resolve sub-grid resolution and integer divisions for the tile."""
    W = proj['tile_w']
    H = proj['tile_h']
    grid = proj.get('grid', 10) or 0
    if grid > 0:
        res = grid / SUBDIV
    else:
        res = min(W, H) / 24.0
    Nx = max(1, int(round(W / res)))
    Ny = max(1, int(round(H / res)))
    gw, gh = W / Nx, H / Ny             # exact cell size (divides tile evenly)
    return W, H, gw, gh


def _element_to_pat_entries(el, W, H, gw, gh):
    """
    Convert an element to PAT families.  All y values are flipped (H - y) so the
    exported pattern matches the editor view (canvas is y-down, .pat is y-up).
    """
    def gx(x):
        return int(round(x / gw))

    def gy(y):
        return int(round((H - y) / gh))   # flip y for Revit's y-up convention

    t = el['type']

    if t == 'line':
        return _segment_families(
            gx(el['x0']), gy(el['y0']),
            gx(el['x1']), gy(el['y1']),
            W, H, gw, gh)

    elif t in ('arc_cr', 'arc_3pt'):
        pts = arc_to_polyline(
            el['cx'], el['cy'], el['r'],
            el['a_start'], el['a_end'], el['ccw'],
            segments=ARC_SEGMENTS,
        )
        # snap + de-duplicate consecutive vertices
        snapped = []
        for x, y in pts:
            g = (gx(x), gy(y))
            if not snapped or g != snapped[-1]:
                snapped.append(g)

        families = []
        for i in range(len(snapped) - 1):
            families += _segment_families(
                snapped[i][0], snapped[i][1],
                snapped[i + 1][0], snapped[i + 1][1],
                W, H, gw, gh)
        return families

    return []


def _fmt(v):
    return f"{v:.8g}"


def export_pat(proj, path, scale=1.0 / 25.4):
    """
    Write a Revit MODEL hatch .pat file.

    scale   multiplies every coordinate/length before writing.  Default
            1/25.4 converts the mm drawing to inches (Revit's internal hatch
            unit).  Import into Revit with Fill Pattern Scale = 1.0; an 80mm
            tile then appears as 80mm.
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
