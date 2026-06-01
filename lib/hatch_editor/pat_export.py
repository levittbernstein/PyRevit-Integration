"""Export project to Revit/AutoCAD .pat format."""
import math
from .geometry import arc_to_polyline, polyline_to_pat_lines, dist


ARC_SEGMENTS = 48   # segments per arc for approximation


def _element_to_pat_entries(el, tile_w, tile_h):
    """Convert a single project element to a list of PAT line dicts."""
    t = el['type']

    if t == 'line':
        x0, y0, x1, y1 = el['x0'], el['y0'], el['x1'], el['y1']
        seg_len = dist((x0, y0), (x1, y1))
        if seg_len < 1e-6:
            return []
        angle_deg = math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360
        return [{
            'angle': angle_deg,
            'ox': x0, 'oy': y0,
            'dx': tile_w, 'dy': tile_h,
            'dash': seg_len,
            'gap': -(tile_w * 10),
        }]

    elif t in ('arc_cr', 'arc_3pt'):
        pts = arc_to_polyline(
            el['cx'], el['cy'], el['r'],
            el['a_start'], el['a_end'], el['ccw'],
            segments=ARC_SEGMENTS,
        )
        return polyline_to_pat_lines(pts, tile_w, tile_h)

    return []


def _fmt_num(v):
    """Format a float cleanly — drop unnecessary decimals."""
    if v == int(v):
        return str(int(v))
    return f"{v:.6g}"


def export_pat(proj, path):
    name = proj['name'].replace(' ', '_')
    tile_w = proj['tile_w']
    tile_h = proj['tile_h']

    lines_out = []
    lines_out.append(f"*{name},Hatch pattern — {proj['name']}")
    lines_out.append(";%TYPE=MODEL")

    for el in proj['elements']:
        entries = _element_to_pat_entries(el, tile_w, tile_h)
        for e in entries:
            angle = _fmt_num(e['angle'])
            ox    = _fmt_num(e['ox'])
            oy    = _fmt_num(e['oy'])
            dx    = _fmt_num(e['dx'])
            dy    = _fmt_num(e['dy'])
            dash  = _fmt_num(e['dash'])
            gap   = _fmt_num(e['gap'])
            lines_out.append(f"{angle},{ox},{oy},{dx},{dy},{dash},{gap}")

    with open(path, 'w') as f:
        f.write('\n'.join(lines_out) + '\n')

    return path
