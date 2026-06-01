"""Tkinter drawing canvas for the hatch tile editor."""
import math
import tkinter as tk
from .geometry import snap, dist, circle_from_three_points, arc_angles, arc_to_polyline
from . import project as proj_mod

TILE_REPEAT = 3          # how many tile repetitions to show each side
CANVAS_PAD  = 40         # px padding around the repeated tile grid
HANDLE_R    = 5          # selection handle radius px
LINE_COL    = '#1a6fbe'
ARC_COL     = '#1a6fbe'
GRID_COL    = '#cccccc'
TILE_COL    = '#888888'
SEL_COL     = '#e05a00'
GHOST_COL   = '#aaaaaa'
REPEAT_COL  = '#bbccdd'
BG_COL      = '#f7f7f7'
ARC_SEG     = 64


class HatchCanvas(tk.Canvas):
    def __init__(self, parent, proj, **kw):
        super().__init__(parent, bg=BG_COL, cursor='crosshair', **kw)
        self._proj = proj
        self._tool = 'select'
        self._zoom = 4.0          # px per mm
        self._pan_x = CANVAS_PAD
        self._pan_y = CANVAS_PAD
        self._selected = set()    # indices into proj['elements']
        self._hover = None

        # Tool state
        self._ghost_items = []    # temporary canvas items for preview
        self._tool_state = {}

        # Callbacks
        self.on_change = None     # called when elements list changes

        self.bind('<Configure>', self._on_resize)
        self.bind('<ButtonPress-1>', self._on_lmb_press)
        self.bind('<B1-Motion>', self._on_lmb_drag)
        self.bind('<ButtonRelease-1>', self._on_lmb_release)
        self.bind('<Motion>', self._on_motion)
        self.bind('<ButtonPress-2>', self._start_pan)
        self.bind('<B2-Motion>', self._do_pan)
        self.bind('<ButtonPress-3>', self._start_pan)
        self.bind('<B3-Motion>', self._do_pan)
        self.bind('<MouseWheel>', self._on_scroll)
        self.bind('<Delete>', self._delete_selected)
        self.bind('<BackSpace>', self._delete_selected)
        self.bind('<Escape>', self._cancel_tool)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_project(self, proj):
        self._proj = proj
        self._selected.clear()
        self._tool_state = {}
        self._clear_ghost()
        self._center_view()
        self.redraw()

    def set_tool(self, tool):
        self._cancel_tool(None)
        self._tool = tool
        cursors = {
            'select': 'arrow',
            'line': 'crosshair',
            'arc_cr': 'crosshair',
            'arc_3pt': 'crosshair',
        }
        self.config(cursor=cursors.get(tool, 'crosshair'))

    def redraw(self):
        self.delete('all')
        self._draw_grid()
        self._draw_repeat()
        self._draw_tile_border()
        self._draw_elements()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _mm_to_px(self, x, y):
        """Model mm → canvas px."""
        return (self._pan_x + x * self._zoom,
                self._pan_y + y * self._zoom)

    def _px_to_mm(self, px, py):
        """Canvas px → model mm (un-snapped)."""
        return ((px - self._pan_x) / self._zoom,
                (py - self._pan_y) / self._zoom)

    def _snapped_mm(self, px, py):
        grid = self._proj.get('grid', 10)
        x, y = self._px_to_mm(px, py)
        return (snap(x, grid), snap(y, grid))

    def _tile_w(self):
        return self._proj.get('tile_w', 100)

    def _tile_h(self):
        return self._proj.get('tile_h', 100)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw_grid(self):
        grid = self._proj.get('grid', 10)
        if grid <= 0:
            return
        tw, th = self._tile_w(), self._tile_h()
        # draw grid only inside the primary tile
        x0, y0 = self._mm_to_px(0, 0)
        x1, y1 = self._mm_to_px(tw, th)
        gx = 0
        while gx <= tw + 1e-6:
            px, _ = self._mm_to_px(gx, 0)
            self.create_line(px, y0, px, y1, fill=GRID_COL, tags='grid')
            gx += grid
        gy = 0
        while gy <= th + 1e-6:
            _, py = self._mm_to_px(0, gy)
            self.create_line(x0, py, x1, py, fill=GRID_COL, tags='grid')
            gy += grid

    def _draw_tile_border(self):
        tw, th = self._tile_w(), self._tile_h()
        x0, y0 = self._mm_to_px(0, 0)
        x1, y1 = self._mm_to_px(tw, th)
        self.create_rectangle(x0, y0, x1, y1,
                               outline=TILE_COL, width=2, fill='', tags='tile_border')

    def _draw_repeat(self):
        """Draw ghost copies of elements in surrounding tile repetitions."""
        tw, th = self._tile_w(), self._tile_h()
        for ix in range(-TILE_REPEAT, TILE_REPEAT + 1):
            for iy in range(-TILE_REPEAT, TILE_REPEAT + 1):
                if ix == 0 and iy == 0:
                    continue
                ox, oy = ix * tw, iy * th
                for el in self._proj['elements']:
                    self._draw_element(el, color=REPEAT_COL, offset=(ox, oy), tags='repeat')

    def _draw_elements(self):
        for i, el in enumerate(self._proj['elements']):
            selected = i in self._selected
            color = SEL_COL if selected else LINE_COL
            self._draw_element(el, color=color, tags=f'el_{i}', selected=selected)

    def _draw_element(self, el, color=LINE_COL, offset=(0, 0), tags='', selected=False):
        ox, oy = offset
        t = el['type']

        if t == 'line':
            x0, y0 = self._mm_to_px(el['x0'] + ox, el['y0'] + oy)
            x1, y1 = self._mm_to_px(el['x1'] + ox, el['y1'] + oy)
            self.create_line(x0, y0, x1, y1, fill=color, width=2, tags=tags)
            if selected:
                self._draw_handle(el['x0'] + ox, el['y0'] + oy)
                self._draw_handle(el['x1'] + ox, el['y1'] + oy)

        elif t in ('arc_cr', 'arc_3pt'):
            pts = arc_to_polyline(
                el['cx'] + ox, el['cy'] + oy, el['r'],
                el['a_start'], el['a_end'], el['ccw'],
                segments=ARC_SEG,
            )
            flat = []
            for x, y in pts:
                px, py = self._mm_to_px(x, y)
                flat += [px, py]
            if len(flat) >= 4:
                self.create_line(flat, fill=color, width=2, smooth=False, tags=tags)
            if selected:
                self._draw_handle(el['cx'] + ox, el['cy'] + oy, square=True)
                for ax, ay in [pts[0], pts[-1]]:
                    self._draw_handle(ax, ay)

    def _draw_handle(self, mx, my, square=False):
        px, py = self._mm_to_px(mx, my)
        r = HANDLE_R
        if square:
            self.create_rectangle(px - r, py - r, px + r, py + r,
                                   fill='white', outline=SEL_COL, width=2, tags='handle')
        else:
            self.create_oval(px - r, py - r, px + r, py + r,
                             fill='white', outline=SEL_COL, width=2, tags='handle')

    def _clear_ghost(self):
        for item in self._ghost_items:
            self.delete(item)
        self._ghost_items = []

    def _ghost_line(self, x0, y0, x1, y1):
        p0 = self._mm_to_px(x0, y0)
        p1 = self._mm_to_px(x1, y1)
        item = self.create_line(*p0, *p1, fill=GHOST_COL, width=1, dash=(4, 4))
        self._ghost_items.append(item)

    def _ghost_arc(self, cx, cy, r, a_start, a_end, ccw):
        pts = arc_to_polyline(cx, cy, r, a_start, a_end, ccw, segments=ARC_SEG)
        flat = []
        for x, y in pts:
            px, py = self._mm_to_px(x, y)
            flat += [px, py]
        if len(flat) >= 4:
            item = self.create_line(flat, fill=GHOST_COL, width=1, dash=(4, 4))
            self._ghost_items.append(item)

    def _ghost_circle(self, cx, cy, r):
        pts = arc_to_polyline(cx, cy, r, 0, 360, True, segments=ARC_SEG)
        flat = []
        for x, y in pts:
            px, py = self._mm_to_px(x, y)
            flat += [px, py]
        if len(flat) >= 4:
            item = self.create_line(flat, fill=GHOST_COL, width=1, dash=(2, 4))
            self._ghost_items.append(item)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_resize(self, event):
        self.redraw()

    def _on_motion(self, event):
        mx, my = self._snapped_mm(event.x, event.y)
        self._update_ghost(mx, my)
        if hasattr(self, '_status_cb') and self._status_cb:
            self._status_cb(f"x={mx:.1f}mm  y={my:.1f}mm")

    def _on_lmb_press(self, event):
        self.focus_set()
        mx, my = self._snapped_mm(event.x, event.y)

        if self._tool == 'select':
            self._do_select(event.x, event.y)
        elif self._tool == 'line':
            self._tool_line_click(mx, my)
        elif self._tool == 'arc_cr':
            self._tool_arc_cr_click(mx, my)
        elif self._tool == 'arc_3pt':
            self._tool_arc_3pt_click(mx, my)

    def _on_lmb_drag(self, event):
        pass  # could add drag-select box later

    def _on_lmb_release(self, event):
        pass

    def _start_pan(self, event):
        self._pan_start = (event.x, event.y, self._pan_x, self._pan_y)

    def _do_pan(self, event):
        sx, sy, px0, py0 = self._pan_start
        self._pan_x = px0 + (event.x - sx)
        self._pan_y = py0 + (event.y - sy)
        self.redraw()
        self._clear_ghost()

    def _on_scroll(self, event):
        factor = 1.1 if event.delta > 0 else 0.9
        mx, my = self._px_to_mm(event.x, event.y)
        self._zoom *= factor
        self._pan_x = event.x - mx * self._zoom
        self._pan_y = event.y - my * self._zoom
        self.redraw()

    def _delete_selected(self, event=None):
        if not self._selected:
            return
        self._proj['elements'] = [
            el for i, el in enumerate(self._proj['elements'])
            if i not in self._selected
        ]
        self._selected.clear()
        self.redraw()
        self._notify_change()

    def _cancel_tool(self, event):
        self._tool_state = {}
        self._clear_ghost()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _do_select(self, px, py):
        hit = self._hit_test(px, py)
        if hit is None:
            self._selected.clear()
        elif hit in self._selected:
            self._selected.discard(hit)
        else:
            self._selected = {hit}
        self.redraw()

    def _hit_test(self, px, py):
        """Return index of element under cursor, or None."""
        TOL = 8  # px
        mx, my = self._px_to_mm(px, py)
        best_i, best_d = None, TOL / self._zoom

        for i, el in enumerate(self._proj['elements']):
            d = self._element_dist(el, mx, my)
            if d < best_d:
                best_d, best_i = d, i
        return best_i

    def _element_dist(self, el, mx, my):
        t = el['type']
        if t == 'line':
            return _dist_point_segment(mx, my, el['x0'], el['y0'], el['x1'], el['y1'])
        elif t in ('arc_cr', 'arc_3pt'):
            pts = arc_to_polyline(el['cx'], el['cy'], el['r'],
                                  el['a_start'], el['a_end'], el['ccw'], segments=ARC_SEG)
            return min(_dist_point_segment(mx, my, pts[j][0], pts[j][1],
                                           pts[j+1][0], pts[j+1][1])
                       for j in range(len(pts) - 1))
        return 1e9

    # ------------------------------------------------------------------
    # Tool: Line (2-click)
    # ------------------------------------------------------------------

    def _tool_line_click(self, mx, my):
        st = self._tool_state
        if 'p0' not in st:
            st['p0'] = (mx, my)
        else:
            x0, y0 = st['p0']
            if dist((x0, y0), (mx, my)) > 1e-6:
                self._proj['elements'].append(proj_mod.make_line(x0, y0, mx, my))
                self._notify_change()
            self._tool_state = {}
            self._clear_ghost()
            self.redraw()

    def _update_ghost_line(self, mx, my):
        st = self._tool_state
        if 'p0' in st:
            self._clear_ghost()
            self._ghost_line(*st['p0'], mx, my)

    # ------------------------------------------------------------------
    # Tool: Arc centre+radius (3-click: centre → rim point → end point)
    # ------------------------------------------------------------------

    def _tool_arc_cr_click(self, mx, my):
        st = self._tool_state
        if 'centre' not in st:
            st['centre'] = (mx, my)
        elif 'start' not in st:
            cx, cy = st['centre']
            r = dist((cx, cy), (mx, my))
            if r < 1e-6:
                return
            st['r'] = r
            st['start'] = (mx, my)
        else:
            cx, cy = st['centre']
            r = st['r']
            p_start = st['start']
            p_end = (mx, my)
            a_start = math.degrees(math.atan2(p_start[1] - cy, p_start[0] - cx)) % 360
            a_end   = math.degrees(math.atan2(p_end[1]   - cy, p_end[0]   - cx)) % 360
            # Default CCW; user can't choose here — they see preview
            ccw = True
            self._proj['elements'].append(
                proj_mod.make_arc_cr(cx, cy, r, a_start, a_end, ccw)
            )
            self._notify_change()
            self._tool_state = {}
            self._clear_ghost()
            self.redraw()

    def _update_ghost_arc_cr(self, mx, my):
        st = self._tool_state
        cx_d, cy_d = st.get('centre', (None, None))
        if cx_d is None:
            return
        self._clear_ghost()
        if 'start' not in st:
            # Show radius line
            self._ghost_line(cx_d, cy_d, mx, my)
            r = dist((cx_d, cy_d), (mx, my))
            if r > 1e-6:
                self._ghost_circle(cx_d, cy_d, r)
        else:
            cx, cy = cx_d, cy_d
            r = st['r']
            p_start = st['start']
            a_start = math.degrees(math.atan2(p_start[1] - cy, p_start[0] - cx)) % 360
            a_end   = math.degrees(math.atan2(my - cy, mx - cx)) % 360
            self._ghost_arc(cx, cy, r, a_start, a_end, ccw=True)
            self._ghost_circle(cx, cy, r)

    # ------------------------------------------------------------------
    # Tool: Arc 3-point (3-click: start → mid → end)
    # ------------------------------------------------------------------

    def _tool_arc_3pt_click(self, mx, my):
        st = self._tool_state
        if 'p0' not in st:
            st['p0'] = (mx, my)
        elif 'pm' not in st:
            st['pm'] = (mx, my)
        else:
            p0, pm, p1 = st['p0'], st['pm'], (mx, my)
            result = circle_from_three_points(p0, pm, p1)
            if result is None:
                self._tool_state = {}
                self._clear_ghost()
                return  # collinear — ignore
            cx, cy, r = result
            a_start, a_end, ccw = arc_angles(cx, cy, p0, p1, pm)
            self._proj['elements'].append(
                proj_mod.make_arc_3pt(*p0, *pm, *p1, cx, cy, r, a_start, a_end, ccw)
            )
            self._notify_change()
            self._tool_state = {}
            self._clear_ghost()
            self.redraw()

    def _update_ghost_arc_3pt(self, mx, my):
        st = self._tool_state
        if 'p0' not in st:
            return
        self._clear_ghost()
        p0 = st['p0']
        if 'pm' not in st:
            self._ghost_line(*p0, mx, my)
        else:
            pm = st['pm']
            result = circle_from_three_points(p0, pm, (mx, my))
            if result:
                cx, cy, r = result
                a_start, a_end, ccw = arc_angles(cx, cy, p0, (mx, my), pm)
                self._ghost_arc(cx, cy, r, a_start, a_end, ccw)
            else:
                self._ghost_line(*p0, *pm)
                self._ghost_line(*pm, mx, my)

    # ------------------------------------------------------------------
    # Ghost dispatcher
    # ------------------------------------------------------------------

    def _update_ghost(self, mx, my):
        if self._tool == 'line':
            self._update_ghost_line(mx, my)
        elif self._tool == 'arc_cr':
            self._update_ghost_arc_cr(mx, my)
        elif self._tool == 'arc_3pt':
            self._update_ghost_arc_3pt(mx, my)

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _center_view(self):
        tw, th = self._tile_w(), self._tile_h()
        try:
            cw, ch = self.winfo_width(), self.winfo_height()
        except Exception:
            cw, ch = 800, 600
        total_w = tw * (2 * TILE_REPEAT + 1) * self._zoom + 2 * CANVAS_PAD
        total_h = th * (2 * TILE_REPEAT + 1) * self._zoom + 2 * CANVAS_PAD
        self._pan_x = (cw - total_w) / 2 + CANVAS_PAD + TILE_REPEAT * tw * self._zoom
        self._pan_y = (ch - total_h) / 2 + CANVAS_PAD + TILE_REPEAT * th * self._zoom

    def _notify_change(self):
        if self.on_change:
            self.on_change()


# ------------------------------------------------------------------
# Geometry helper — point to segment distance
# ------------------------------------------------------------------

def _dist_point_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return dist((px, py), (ax, ay))
    t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return dist((px, py), (ax + t * dx, ay + t * dy))
