# -*- coding: utf-8 -*-
"""
LB Shoebox — pyRevit push-button script.

Reads geometry for a selected Room (dimensions, orientation, floor level, and
each window's size / sill / position / reveal) via the Revit API, writes it to
a JSON handoff file, and launches the Shoebox popup in the shoebox conda env
(which has tkinter + Honeybee/Radiance/EnergyPlus).

Geometry-only scope: glazing performance (g-value, U, tau-vis) and shading
beyond the reveal are entered by the user in the popup.

Mirrors the Hatch Creator pattern: extraction happens here (only Revit can),
then a fully-detached CPython process hosts the UI and runs the simulation.
"""
import os
import io
import sys
import json
import math
import subprocess
from datetime import datetime

from pyrevit import revit, DB, forms

# ── Make the shared lib importable (for lb_shoebox.room_storage) ──────────────
_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, "lib")
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)
from lb_shoebox.room_storage import read_room_state, write_room_state, acquire_room

__title__ = "Shoebox\nDaylight/OH"
__doc__ = "Extract a room's geometry and assess daylight + overheating in the Shoebox tool."

# ── Config: where the shoebox env + popup live ────────────────────────────────
# Other LB users: set SHOEBOX_PYTHON / SHOEBOX_POPUP env vars (or edit the
# defaults below) to point at your conda env python and the Shoebox qt_app.py.
SHOEBOX_PYTHON = os.environ.get(
    "SHOEBOX_PYTHON",
    r"C:\Users\mike.gibbs\AppData\Local\miniconda3\envs\shoebox\python.exe")
# The integrated Qt + 3D app. (Fallback tkinter UI: ...\revit_bridge\popup.py)
SHOEBOX_POPUP = os.environ.get(
    "SHOEBOX_POPUP",
    r"C:\Users\mike.gibbs\Documents\Claude Code\shoebox\shoebox\revit_bridge\qt_app.py")
SCHEMA_VERSION = "shoebox-revit-extract/1"

FT_TO_M = 0.3048


# ── Small helpers ─────────────────────────────────────────────────────────────
def ft2m(v):
    return v * FT_TO_M


def eid(element_id):
    """ElementId as a hashable int. Revit 2024+ uses .Value (Int64); older .IntegerValue."""
    try:
        return element_id.Value
    except AttributeError:
        return element_id.IntegerValue


# ── Shared-drive state store (must mirror shoebox/revit_bridge/state_store.py) ──
def _sd_dir():
    base = os.environ.get("SHOEBOX_STATE_DIR", r"S:\IC Studio\AI\Shoebox\room_states")
    try:
        if not os.path.exists(base):
            os.makedirs(base)
        return base
    except Exception:
        local = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                             "Shoebox", "room_states")
        if not os.path.exists(local):
            os.makedirs(local)
        return local


def _sd_path(uid):
    safe = "".join(c for c in uid if c.isalnum() or c in "-_") or "room"
    return os.path.join(_sd_dir(), "room_{0}.json".format(safe))


def _sd_read(uid):
    p = _sd_path(uid)
    if not os.path.exists(p):
        return None
    try:
        with io.open(p, "r", encoding="utf-8") as fh:
            return json.loads(fh.read())
    except Exception:
        return None


def _sd_write(uid, state):
    try:
        with io.open(_sd_path(uid), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _newest(a, b):
    """The state with the later 'saved_at'. Either may be None."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a.get("saved_at", "") >= b.get("saved_at", "") else b


def _str_param(el, bip):
    try:
        p = el.get_Parameter(bip)
        return p.AsString() if p else None
    except Exception:
        return None


def _len_param(el, names, bips):
    """First positive length (feet) from named params, then BuiltInParameters."""
    for nm in names:
        try:
            p = el.LookupParameter(nm)
            if p and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
                return p.AsDouble()
        except Exception:
            pass
    for bip in bips:
        try:
            p = el.get_Parameter(bip)
            if p and p.StorageType == DB.StorageType.Double and p.AsDouble() > 0:
                return p.AsDouble()
        except Exception:
            pass
    return None


def _plan_dir(wall):
    """Unit direction of a wall's location curve in plan (X, Y)."""
    crv = wall.Location.Curve
    d = crv.GetEndPoint(1) - crv.GetEndPoint(0)
    n = math.hypot(d.X, d.Y)
    if n == 0:
        return (1.0, 0.0)
    return (d.X / n, d.Y / n)


def _bbox_extent_on(bb, ax, ay):
    """Range of the world AABB corners projected onto plan axis (ax, ay)."""
    xs = (bb.Min.X, bb.Max.X)
    ys = (bb.Min.Y, bb.Max.Y)
    projs = [x * ax + y * ay for x in xs for y in ys]
    return max(projs) - min(projs)


def _name_via_param(el, bips):
    """Read a name from parameters — avoids the overloaded Element.Name property
    (which can throw under IronPython on newer Revit)."""
    if el is None:
        return None
    for bip in bips:
        try:
            p = el.get_Parameter(bip)
            if p:
                s = p.AsString()
                if s:
                    return s
        except Exception:
            pass
    return None


def _solids_from(geo):
    """Recursively collect non-empty Solids from a GeometryElement, descending
    into GeometryInstances (which return world-coordinate geometry)."""
    out = []
    if geo is None:
        return out
    for g in geo:
        try:
            if isinstance(g, DB.Solid):
                if g.Faces.Size > 0 and g.Volume > 0:
                    out.append(g)
            elif isinstance(g, DB.GeometryInstance):
                out += _solids_from(g.GetInstanceGeometry())
        except Exception:
            pass
    return out


def _tessellate(elem, opt):
    """Triangulate an element's solids -> (verts [(x,y,z) feet], tris [(i,j,k)])."""
    verts, tris = [], []
    try:
        geo = elem.get_Geometry(opt)
    except Exception:
        return verts, tris
    for solid in _solids_from(geo):
        for face in solid.Faces:
            try:
                m = face.Triangulate()
            except Exception:
                m = None
            if m is None:
                continue
            base = len(verts)
            for vi in range(m.Vertices.Count):
                p = m.Vertices[vi]
                verts.append((p.X, p.Y, p.Z))
            for ti in range(m.NumTriangles):
                t = m.get_Triangle(ti)
                tris.append((base + t.get_Index(0), base + t.get_Index(1), base + t.get_Index(2)))
    return verts, tris


def _floor_from_level_name(name):
    """Best-effort storey number from a level name. Ground/GF -> 0."""
    if not name:
        return None
    low = name.lower()
    if "ground" in low or low.strip() in ("gf", "g", "00"):
        return 0
    if "basement" in low or low.startswith("b"):
        return 0
    digits = ""
    for ch in name:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if digits:
        try:
            return int(digits)
        except Exception:
            return None
    return None


# ── Pick the room ─────────────────────────────────────────────────────────────
doc = revit.doc
selection = revit.get_selection()
rooms = [e for e in selection if isinstance(e, DB.Architecture.Room)]

if not rooms:
    forms.alert("Select a Room first, then run Shoebox.\n\n"
                "Tip: click the room (not just the room tag) so the Room element is selected.",
                title="Shoebox", warn_icon=True)
    raise SystemExit

if len(rooms) > 1:
    forms.alert("{0} rooms selected — Shoebox assesses one at a time. "
                "Using the first.".format(len(rooms)), title="Shoebox", warn_icon=True)

room = rooms[0]
warnings = []

# ── Ownership: check the room out from central so two users can't clash ───────
_can_edit, _owner = acquire_room(doc, room)
if not _can_edit:
    forms.alert(
        "This room is being worked on in Shoebox by:\n\n    {0}\n\n"
        "Their changes save into the model when they sync to central. "
        "Please wait until then before opening it.".format(_owner),
        title="Shoebox — room checked out", warn_icon=True)
    raise SystemExit

# ── Boundary: vertices + bounding walls ───────────────────────────────────────
opts = DB.SpatialElementBoundaryOptions()
loops = room.GetBoundarySegments(opts)
verts = []          # (x, y) in feet — all loops, for width/depth
wall_by_id = {}     # int id -> Wall
footprint_ft = []   # outer loop only, tessellated (handles arcs/curved walls)
footprint_z_ft = None  # boundary world Z = true room floor (more reliable than level.Elevation)
for li, loop in enumerate(loops):
    for seg in loop:
        crv = seg.GetCurve()
        p0 = crv.GetEndPoint(0)
        verts.append((p0.X, p0.Y))
        if li == 0:
            if footprint_z_ft is None:
                footprint_z_ft = p0.Z
            try:
                for pp in crv.Tessellate():
                    footprint_ft.append((pp.X, pp.Y))
            except Exception:
                footprint_ft.append((p0.X, p0.Y))
        el = doc.GetElement(seg.ElementId)
        if isinstance(el, DB.Wall):
            wall_by_id[eid(el.Id)] = el

if not verts:
    forms.alert("Could not read room boundary. Is the room placed/enclosed?",
                title="Shoebox", warn_icon=True)
    raise SystemExit

# ── Openings (windows + doors) hosted in this room's bounding walls ───────────
# Glazed doors (balcony/patio) are OST_Doors, so we collect both categories.
all_wins = []
for _cat in (DB.BuiltInCategory.OST_Windows, DB.BuiltInCategory.OST_Doors):
    all_wins += list(DB.FilteredElementCollector(doc)
                     .OfCategory(_cat)
                     .WhereElementIsNotElementType()
                     .ToElements())

# A window belongs to this room if Revit reports it bounding the room via the
# phase-based From/To room — this is ROOM-SPECIFIC, so it excludes a neighbour's
# windows when a long wall is shared by several rooms. Only if that yields
# nothing do we fall back to host-wall membership AND a check that the window
# actually sits inside this room's footprint (so the shared-wall neighbours
# still don't leak in).
room_id = eid(room.Id)
try:
    room_phase = doc.GetElement(room.CreatedPhaseId)
except Exception:
    room_phase = None


def _room_of(w, getter, prop):
    try:
        r = getattr(w, getter)(room_phase) if room_phase else getattr(w, prop)
        return eid(r.Id) if r is not None else None
    except Exception:
        return None


def _phase_belongs(w):
    return (_room_of(w, "get_FromRoom", "FromRoom") == room_id or
            _room_of(w, "get_ToRoom", "ToRoom") == room_id)


def _poly_contains(px, py, poly):
    inside, n, j = False, len(poly), len(poly) - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


_cx = sum(x for x, y in footprint_ft) / len(footprint_ft) if footprint_ft else 0.0
_cy = sum(y for x, y in footprint_ft) / len(footprint_ft) if footprint_ft else 0.0


def _loc_in_room(w):
    try:
        loc = w.Location
        p = loc.Point if isinstance(loc, DB.LocationPoint) else None
        if p is None:
            return False
        dx, dy = _cx - p.X, _cy - p.Y           # nudge toward the centroid so a
        d = (dx * dx + dy * dy) ** 0.5 or 1.0   # point on the wall lands inside
        return _poly_contains(p.X + dx / d * 0.5, p.Y + dy / d * 0.5, footprint_ft)
    except Exception:
        return False


room_wins = [w for w in all_wins if _phase_belongs(w)]
if not room_wins:
    room_wins = [w for w in all_wins
                 if getattr(w, "Host", None) is not None
                 and eid(w.Host.Id) in wall_by_id and _loc_in_room(w)]

if not room_wins:
    forms.alert("No windows found for this room.\n\n"
                "Shoebox needs at least one window. Check the window is hosted in "
                "a wall that bounds this room (not an adjacent room).",
                title="Shoebox", warn_icon=True)
    raise SystemExit


# ── Choose the facade wall (most glazing). Warn if multi-aspect. ──────────────
def _win_area_ft2(w):
    bb = w.get_BoundingBox(None)
    if not bb:
        return 0.0
    return (bb.Max.X - bb.Min.X + bb.Max.Y - bb.Min.Y) * (bb.Max.Z - bb.Min.Z)


wins_by_host = {}   # host id (or None) -> [windows]
for w in room_wins:
    host = getattr(w, "Host", None)
    wins_by_host.setdefault(eid(host.Id) if host is not None else None, []).append(w)

facade_key = max(wins_by_host.keys(),
                 key=lambda k: sum(_win_area_ft2(w) for w in wins_by_host[k]))
facade_wins = wins_by_host[facade_key]
facade_host = getattr(facade_wins[0], "Host", None)

if len(wins_by_host) > 1:
    warnings.append(
        "Windows found on {0} walls (multi-aspect). Shoebox is single-aspect — "
        "using the most-glazed wall ({1} window(s)). Review the others separately."
        .format(len(wins_by_host), len(facade_wins)))

# ── Facade direction: from the wall curve if usable, else the window facing ───
dx = dy = None
if isinstance(facade_host, DB.Wall):
    try:
        dx, dy = _plan_dir(facade_host)
    except Exception:
        dx = dy = None
if dx is None:
    n0 = facade_wins[0].FacingOrientation       # along-wall = facing rotated 90 deg in plan
    ax, ay = -n0.Y, n0.X
    mag = math.hypot(ax, ay)
    dx, dy = (ax / mag, ay / mag) if mag else (1.0, 0.0)

# ── Room width (along facade) / depth (perpendicular) ─────────────────────────
px, py = -dy, dx     # in-plan perpendicular
proj_d = [vx * dx + vy * dy for (vx, vy) in verts]
proj_p = [vx * px + vy * py for (vx, vy) in verts]
min_d = min(proj_d)
width_m = ft2m(max(proj_d) - min_d)
depth_m = ft2m(max(proj_p) - min(proj_p))

# ── Height + floor level ──────────────────────────────────────────────────────
try:
    height_m = ft2m(room.UnboundedHeight)
    if height_m <= 0 or height_m > 6:
        height_m = 2.7
        warnings.append("Room height looked wrong; defaulted to 2.7 m — adjust in popup.")
except Exception:
    height_m = 2.7
    warnings.append("Could not read room height; defaulted to 2.7 m — adjust in popup.")

level = doc.GetElement(room.LevelId) if room.LevelId else None
level_name = level.Name if level else None
floor_level = _floor_from_level_name(level_name)
if floor_level is None:
    floor_level = 1
    warnings.append("Couldn't infer floor level from '{0}' — set to 1. "
                    "Set 0 for ground floor (changes night-vent rules).".format(level_name))
level_base_ft = level.Elevation if level else 0.0

# ── Orientation (true-north bearing of the glazing) ──────────────────────────
try:
    n = facade_wins[0].FacingOrientation
    bearing_proj = math.degrees(math.atan2(n.X, n.Y)) % 360.0
    try:
        pp = doc.ActiveProjectLocation.GetProjectPosition(DB.XYZ.Zero)
        true_corr = math.degrees(pp.Angle)
    except Exception:
        true_corr = 0.0
    orientation = (bearing_proj + true_corr) % 360.0
    warnings.append("Orientation estimated as {0:.0f}deg (true N). "
                    "Verify against the plan and edit if needed.".format(orientation))
except Exception:
    orientation = 180.0
    warnings.append("Could not read orientation; defaulted to 180deg (south) — verify.")

# ── Per-window extraction ────────────────────────────────────────────────────
windows_out = []
for w in facade_wins:
    try:
        sym = doc.GetElement(w.GetTypeId())

        # Bounding box (model coords) — the family-agnostic size fallback
        try:
            bb = w.get_BoundingBox(None)
        except Exception:
            bb = None

        # Width / height — try params first, then bbox
        w_ft = (_len_param(w, ["Width", "Rough Width"], [DB.BuiltInParameter.FAMILY_WIDTH_PARAM])
                or _len_param(sym, ["Width", "Rough Width"], [DB.BuiltInParameter.FAMILY_WIDTH_PARAM])
                or (_bbox_extent_on(bb, dx, dy) if bb else None))
        h_ft = (_len_param(w, ["Height", "Rough Height"], [DB.BuiltInParameter.FAMILY_HEIGHT_PARAM])
                or _len_param(sym, ["Height", "Rough Height"], [DB.BuiltInParameter.FAMILY_HEIGHT_PARAM])
                or ((bb.Max.Z - bb.Min.Z) if bb else None))
        if not w_ft or not h_ft:
            warnings.append("Skipped a window: no Width/Height params and "
                            "{0} bounding box.".format("no" if bb is None else "unusable"))
            continue

        # Sill
        sill_ft = None
        try:
            sp = w.get_Parameter(DB.BuiltInParameter.INSTANCE_SILL_HEIGHT_PARAM)
            if sp:
                sill_ft = sp.AsDouble()
        except Exception:
            sill_ft = None
        if sill_ft is None:
            sill_ft = (bb.Min.Z - level_base_ft) if bb else (0.9 / FT_TO_M)

        # Centre along facade, from the room's left edge (min projection on d)
        try:
            loc = w.Location
            lp = loc.Point if isinstance(loc, DB.LocationPoint) else (bb.Min if bb else None)
        except Exception:
            lp = bb.Min if bb else None
        center_ft = ((lp.X * dx + lp.Y * dy) - min_d) if lp is not None else (width_m / FT_TO_M / 2)

        # Reveal ~ host wall thickness (proxy; user adjusts)
        try:
            wh = getattr(w, "Host", None)
            reveal_m = ft2m(wh.Width) if isinstance(wh, DB.Wall) else 0.0
        except Exception:
            reveal_m = 0.0

        windows_out.append({
            "width": round(ft2m(w_ft), 4),
            "height": round(ft2m(h_ft), 4),
            "sill_height": round(max(0.0, ft2m(sill_ft)), 4),
            "center_x": round(max(0.01, ft2m(center_ft)), 4),
            "reveal_depth": round(max(0.0, reveal_m), 4),
            "family_name": _name_via_param(w, [DB.BuiltInParameter.ALL_MODEL_FAMILY_NAME]),
            "type_name": _name_via_param(w, [DB.BuiltInParameter.ALL_MODEL_TYPE_NAME,
                                             DB.BuiltInParameter.SYMBOL_NAME_PARAM]),
        })
    except Exception as exc:
        warnings.append("Error reading a window: {0}".format(exc))

if not windows_out:
    detail = "\n- ".join(warnings[-6:]) if warnings else "(no detail captured)"
    forms.alert("Found windows but couldn't extract geometry.\n\nWhy:\n- {0}".format(detail),
                title="Shoebox", warn_icon=True)
    raise SystemExit

warnings.append("Reveal depth estimated from wall thickness — adjust per window in the popup.")

# ── Output folder + names ─────────────────────────────────────────────────────
out_dir = os.path.join(os.environ.get("TEMP", os.path.expanduser("~")), "shoebox_extracts")
if not os.path.exists(out_dir):
    os.makedirs(out_dir)
_room_name = _str_param(room, DB.BuiltInParameter.ROOM_NAME)
safe_name = "".join(c for c in (_room_name or "room") if c.isalnum() or c in "-_") or "room"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# ── Tessellate the facade openings (windows + doors) -> OBJ (world metres) ─────
mesh_obj_path = None
frame = None
try:
    opt = DB.Options()
    opt.ComputeReferences = False
    opt.IncludeNonVisibleObjects = False
    opt.DetailLevel = DB.ViewDetailLevel.Fine
    all_v, all_t = [], []
    for w in facade_wins:
        v, t = _tessellate(w, opt)
        base = len(all_v)
        all_v += v
        all_t += [(a + base, b + base, c + base) for (a, b, c) in t]
    if all_v and all_t:
        mesh_obj_path = os.path.join(out_dir, "mesh_{0}_{1}.obj".format(safe_name, stamp))
        with io.open(mesh_obj_path, "w", encoding="utf-8") as mf:
            for (x, y, z) in all_v:
                mf.write(u"v {0:.5f} {1:.5f} {2:.5f}\n".format(x * FT_TO_M, y * FT_TO_M, z * FT_TO_M))
            for (a, b, c) in all_t:
                mf.write(u"f {0} {1} {2}\n".format(a + 1, b + 1, c + 1))
        # Facade frame (world metres) for snap-to-parameter authoring. The wall
        # position (wp) must be the ROOM BOUNDARY face on the window side, not
        # the window's centreline — so the calc plane sits on the boundary.
        proj_p_bnd = [vx * px + vy * py for (vx, vy) in verts]
        minp, maxp = min(proj_p_bnd), max(proj_p_bnd)
        loc0 = facade_wins[0].Location
        lp0 = loc0.Point if isinstance(loc0, DB.LocationPoint) else None
        win_p = (lp0.X * px + lp0.Y * py) if lp0 is not None else (minp + maxp) / 2.0
        wp = minp if abs(win_p - minp) <= abs(win_p - maxp) else maxp
        room_centroid_p = sum(proj_p_bnd) / len(proj_p_bnd)
        in_sign = 1.0 if room_centroid_p > wp else -1.0
        ox, oy = min_d * dx + wp * px, min_d * dy + wp * py
        floor_z_ft = footprint_z_ft if footprint_z_ft is not None else level_base_ft
        frame = {
            "origin": [round(ox * FT_TO_M, 4), round(oy * FT_TO_M, 4), round(floor_z_ft * FT_TO_M, 4)],
            "x_axis": [round(dx, 6), round(dy, 6), 0.0],
            "in_axis": [round(px * in_sign, 6), round(py * in_sign, 6), 0.0],
        }
    else:
        warnings.append("Could not tessellate opening geometry — 3D mesh import skipped.")
except Exception as exc:
    warnings.append("Mesh export failed ({0}) — continuing without 3D mesh.".format(exc))

# ── Build the handoff dict (must match RevitExtract field names) ──────────────
extract = {
    "schema_version": SCHEMA_VERSION,
    "project": doc.Title,
    "room_name": _room_name,
    "room_number": _str_param(room, DB.BuiltInParameter.ROOM_NUMBER),
    "revit_level": level_name,
    "unique_id": room.UniqueId,
    "width": round(width_m, 4),
    "depth": round(depth_m, 4),
    "height": round(height_m, 4),
    "floor_level": floor_level,
    "orientation": round(orientation, 2),
    "windows": windows_out,
    "mesh_obj": mesh_obj_path,
    "frame": frame,
    "footprint": [[round(x * FT_TO_M, 4), round(y * FT_TO_M, 4)] for (x, y) in footprint_ft],
    "base_z": round((footprint_z_ft if footprint_z_ft is not None else level_base_ft) * FT_TO_M, 4),
    "warnings": warnings,
}

# ── Reconcile saved state: model (Extensible Storage) <-> shared drive ────────
# The model carries the per-room state in the .rvt (portable, syncs to all
# users via central); the shared drive is the live channel the 3D tool reads/
# writes. Newest (by 'saved_at') wins; the winner is written to both so the
# tool restores it and the model is brought up to date.
uid = room.UniqueId
_es_state = read_room_state(room)
_sd_state = _sd_read(uid)
_winner = _newest(_es_state, _sd_state)
if _winner is not None:
    if _winner is not _sd_state:
        _sd_write(uid, _winner)                       # tool reads this on launch
    if _winner is not _es_state:
        _t = DB.Transaction(doc, "LB Shoebox - save room state")
        _t.Start()
        try:
            write_room_state(room, _winner)           # bring the model up to date
            _t.Commit()
        except Exception as _exc:
            _t.RollBack()
            warnings.append("Could not write room state to model ({0}).".format(_exc))

# ── Write JSON ────────────────────────────────────────────────────────────────
out_path = os.path.join(out_dir, "extract_{0}_{1}.json".format(safe_name, stamp))
with io.open(out_path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(extract, ensure_ascii=False, indent=2))

# ── Launch the popup detached (outlives the pyRevit script host) ──────────────
if not os.path.exists(SHOEBOX_PYTHON):
    forms.alert("Shoebox Python env not found:\n{0}\n\n"
                "Edit SHOEBOX_PYTHON at the top of this script.".format(SHOEBOX_PYTHON),
                title="Shoebox", warn_icon=True)
    raise SystemExit
if not os.path.exists(SHOEBOX_POPUP):
    forms.alert("Shoebox popup not found:\n{0}\n\n"
                "Edit SHOEBOX_POPUP at the top of this script.".format(SHOEBOX_POPUP),
                title="Shoebox", warn_icon=True)
    raise SystemExit

# DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (raw ints — IronPython lacks the names)
subprocess.Popen([SHOEBOX_PYTHON, SHOEBOX_POPUP, out_path],
                 creationflags=0x00000008 | 0x00000200)
