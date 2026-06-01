"""Project save/load as JSON."""
import json


def new_project(tile_w=100.0, tile_h=100.0, grid=10.0, name="Untitled"):
    return {
        'name': name,
        'tile_w': tile_w,
        'tile_h': tile_h,
        'grid': grid,
        'elements': [],   # list of element dicts
    }


def save_project(proj, path):
    with open(path, 'w') as f:
        json.dump(proj, f, indent=2)


def load_project(path):
    with open(path, 'r') as f:
        return json.load(f)


# Element schemas
def make_line(x0, y0, x1, y1):
    return {'type': 'line', 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1}


def make_arc_cr(cx, cy, r, a_start, a_end, ccw):
    """Arc defined by centre + radius + angles."""
    return {
        'type': 'arc_cr',
        'cx': cx, 'cy': cy, 'r': r,
        'a_start': a_start, 'a_end': a_end, 'ccw': ccw,
    }


def make_arc_3pt(x0, y0, xm, ym, x1, y1, cx, cy, r, a_start, a_end, ccw):
    """Arc defined by 3 points (stored with resolved geometry for easy re-export)."""
    return {
        'type': 'arc_3pt',
        'x0': x0, 'y0': y0, 'xm': xm, 'ym': ym, 'x1': x1, 'y1': y1,
        'cx': cx, 'cy': cy, 'r': r,
        'a_start': a_start, 'a_end': a_end, 'ccw': ccw,
    }
