"""Main application window."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
import os

from .canvas import HatchCanvas
from . import project as proj_mod
from .pat_export import export_pat


class HatchApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Hatch Editor")
        self.geometry("1200x800")
        self._proj = proj_mod.new_project()
        self._proj_path = None
        self._dirty = False

        self._build_menu()
        self._build_toolbar()
        self._build_main()
        self._build_statusbar()

        self._canvas.set_project(self._proj)
        self._canvas.on_change = self._mark_dirty
        self._update_title()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build_menu(self):
        mb = tk.Menu(self)
        self.config(menu=mb)

        fm = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="File", menu=fm)
        fm.add_command(label="New…",            command=self._new,          accelerator="Ctrl+N")
        fm.add_command(label="Open…",           command=self._open,         accelerator="Ctrl+O")
        fm.add_command(label="Save",            command=self._save,         accelerator="Ctrl+S")
        fm.add_command(label="Save As…",        command=self._save_as)
        fm.add_separator()
        fm.add_command(label="Export .pat…",    command=self._export_pat,   accelerator="Ctrl+E")
        fm.add_separator()
        fm.add_command(label="Quit",            command=self._on_close)

        em = tk.Menu(mb, tearoff=0)
        mb.add_cascade(label="Edit", menu=em)
        em.add_command(label="Delete selected", command=self._canvas_delete, accelerator="Del")
        em.add_command(label="Clear all",       command=self._clear_all)
        em.add_separator()
        em.add_command(label="Tile settings…",  command=self._tile_settings)

        self.bind_all("<Control-n>", lambda e: self._new())
        self.bind_all("<Control-o>", lambda e: self._open())
        self.bind_all("<Control-s>", lambda e: self._save())
        self.bind_all("<Control-e>", lambda e: self._export_pat())

    def _build_toolbar(self):
        tb = ttk.Frame(self, relief='raised', padding=4)
        tb.pack(side='top', fill='x')

        self._tool_var = tk.StringVar(value='select')

        tools = [
            ('Select',       'select'),
            ('Line',         'line'),
            ('Arc C+R',      'arc_cr'),
            ('Arc 3-Point',  'arc_3pt'),
        ]

        for label, tool in tools:
            rb = ttk.Radiobutton(tb, text=label, variable=self._tool_var,
                                 value=tool, command=self._set_tool)
            rb.pack(side='left', padx=4)

        ttk.Separator(tb, orient='vertical').pack(side='left', fill='y', padx=8)

        ttk.Button(tb, text="Export .pat", command=self._export_pat).pack(side='left', padx=4)

        # Tool hints
        self._hint_var = tk.StringVar(value='')
        ttk.Label(tb, textvariable=self._hint_var, foreground='#666666').pack(side='right', padx=8)

        self._tool_var.trace_add('write', lambda *_: self._update_hint())
        self._update_hint()

    def _build_main(self):
        frame = ttk.Frame(self)
        frame.pack(side='top', fill='both', expand=True)

        self._canvas = HatchCanvas(frame, self._proj, width=900, height=700)
        self._canvas.pack(side='left', fill='both', expand=True)

        # Side panel
        side = ttk.Frame(frame, padding=8, width=200)
        side.pack(side='right', fill='y')
        side.pack_propagate(False)

        ttk.Label(side, text="Elements", font=('', 10, 'bold')).pack(anchor='w')
        self._el_list = tk.Listbox(side, width=22, selectmode='browse')
        self._el_list.pack(fill='both', expand=True, pady=4)
        self._el_list.bind('<<ListboxSelect>>', self._on_list_select)

        ttk.Button(side, text="Delete selected", command=self._canvas_delete).pack(fill='x', pady=2)
        ttk.Button(side, text="Clear all",       command=self._clear_all).pack(fill='x', pady=2)
        ttk.Separator(side).pack(fill='x', pady=6)
        ttk.Button(side, text="Tile settings…",  command=self._tile_settings).pack(fill='x', pady=2)

    def _build_statusbar(self):
        sb = ttk.Frame(self, relief='sunken', padding=2)
        sb.pack(side='bottom', fill='x')
        self._status_var = tk.StringVar(value='Ready')
        ttk.Label(sb, textvariable=self._status_var).pack(side='left')
        self._canvas._status_cb = self._set_status

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    def _set_tool(self):
        tool = self._tool_var.get()
        self._canvas.set_tool(tool)
        self._update_hint()

    def _update_hint(self):
        hints = {
            'select':   'Click to select. Delete key removes selected element.',
            'line':     'Click start point, then end point.',
            'arc_cr':   'Click centre → click rim (sets radius) → click end point.',
            'arc_3pt':  'Click start → click mid-point → click end.',
        }
        self._hint_var.set(hints.get(self._tool_var.get(), ''))

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def _new(self):
        if not self._confirm_discard():
            return
        d = TileSettingsDialog(self, title="New Project")
        if d.result is None:
            return
        name, tw, th, grid = d.result
        self._proj = proj_mod.new_project(tw, th, grid, name)
        self._proj_path = None
        self._dirty = False
        self._canvas.set_project(self._proj)
        self._refresh_list()
        self._update_title()

    def _open(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open Hatch Project",
            filetypes=[("Hatch project", "*.hatch"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            p = proj_mod.load_project(path)
        except Exception as ex:
            messagebox.showerror("Open failed", str(ex))
            return
        self._proj = p
        self._proj_path = path
        self._dirty = False
        self._canvas.set_project(self._proj)
        self._refresh_list()
        self._update_title()

    def _save(self):
        if self._proj_path:
            proj_mod.save_project(self._proj, self._proj_path)
            self._dirty = False
            self._update_title()
        else:
            self._save_as()

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save Hatch Project",
            defaultextension=".hatch",
            filetypes=[("Hatch project", "*.hatch"), ("All files", "*.*")],
        )
        if not path:
            return
        proj_mod.save_project(self._proj, path)
        self._proj_path = path
        self._dirty = False
        self._update_title()

    def _export_pat(self):
        if not self._proj['elements']:
            messagebox.showwarning("Export", "No elements to export.")
            return

        default_scale = self._proj.get('_export_scale', 1.0 / 25.4)
        d = ExportDialog(self, default_scale)
        if d.result is None:
            return
        scale = d.result
        self._proj['_export_scale'] = scale   # remember for next export

        name = self._proj.get('name', 'Untitled').replace(' ', '_')
        path = filedialog.asksaveasfilename(
            title="Export .pat",
            initialfile=f"{name}.pat",
            defaultextension=".pat",
            filetypes=[("PAT hatch file", "*.pat"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            export_pat(self._proj, path, scale=scale)
            messagebox.showinfo(
                "Export complete",
                f"Saved: {os.path.basename(path)}\n\n"
                f"Import into Revit with Fill Pattern Scale = 1.0\n"
                f"Export scale used: {scale}",
            )
        except Exception as ex:
            messagebox.showerror("Export failed", str(ex))

    # ------------------------------------------------------------------
    # Edit helpers
    # ------------------------------------------------------------------

    def _canvas_delete(self, event=None):
        self._canvas._delete_selected()
        self._refresh_list()

    def _clear_all(self):
        if not self._proj['elements']:
            return
        if messagebox.askyesno("Clear all", "Remove all elements?"):
            self._proj['elements'].clear()
            self._canvas._selected.clear()
            self._canvas.redraw()
            self._refresh_list()
            self._mark_dirty()

    def _tile_settings(self):
        d = TileSettingsDialog(self, title="Tile Settings",
                               name=self._proj.get('name', 'Untitled'),
                               tw=self._proj.get('tile_w', 100),
                               th=self._proj.get('tile_h', 100),
                               grid=self._proj.get('grid', 10))
        if d.result is None:
            return
        name, tw, th, grid = d.result
        self._proj['name'] = name
        self._proj['tile_w'] = tw
        self._proj['tile_h'] = th
        self._proj['grid'] = grid
        self._canvas.redraw()
        self._mark_dirty()
        self._update_title()

    # ------------------------------------------------------------------
    # Side panel list
    # ------------------------------------------------------------------

    def _refresh_list(self):
        self._el_list.delete(0, 'end')
        labels = {
            'line':    'Line',
            'arc_cr':  'Arc (C+R)',
            'arc_3pt': 'Arc (3pt)',
        }
        for i, el in enumerate(self._proj['elements']):
            self._el_list.insert('end', f"{i+1}. {labels.get(el['type'], el['type'])}")

    def _on_list_select(self, event):
        sel = self._el_list.curselection()
        if sel:
            self._canvas._selected = {sel[0]}
            self._canvas.redraw()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _mark_dirty(self):
        self._dirty = True
        self._update_title()
        self._refresh_list()

    def _update_title(self):
        name = self._proj.get('name', 'Untitled')
        tw = self._proj.get('tile_w', 100)
        th = self._proj.get('tile_h', 100)
        dirty = '*' if self._dirty else ''
        self.title(f"Hatch Editor — {dirty}{name}  ({tw}×{th}mm)")

    def _set_status(self, msg):
        self._status_var.set(msg)

    def _confirm_discard(self):
        if not self._dirty:
            return True
        return messagebox.askyesno("Unsaved changes", "Discard unsaved changes?")

    def _on_close(self):
        if self._confirm_discard():
            self.destroy()


# ------------------------------------------------------------------
# Tile settings dialog
# ------------------------------------------------------------------

class TileSettingsDialog(tk.Toplevel):
    def __init__(self, parent, title="Tile Settings",
                 name="Untitled", tw=100.0, th=100.0, grid=10.0):
        super().__init__(parent)
        self.title(title)
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        pad = dict(padx=8, pady=4)

        ttk.Label(self, text="Pattern name:").grid(row=0, column=0, sticky='w', **pad)
        self._name = ttk.Entry(self, width=20)
        self._name.insert(0, name)
        self._name.grid(row=0, column=1, **pad)

        ttk.Label(self, text="Tile width (mm):").grid(row=1, column=0, sticky='w', **pad)
        self._tw = ttk.Entry(self, width=10)
        self._tw.insert(0, str(tw))
        self._tw.grid(row=1, column=1, **pad)

        ttk.Label(self, text="Tile height (mm):").grid(row=2, column=0, sticky='w', **pad)
        self._th = ttk.Entry(self, width=10)
        self._th.insert(0, str(th))
        self._th.grid(row=2, column=1, **pad)

        ttk.Label(self, text="Snap grid (mm):").grid(row=3, column=0, sticky='w', **pad)
        self._grid = ttk.Entry(self, width=10)
        self._grid.insert(0, str(grid))
        self._grid.grid(row=3, column=1, **pad)

        bf = ttk.Frame(self)
        bf.grid(row=4, column=0, columnspan=2, pady=8)
        ttk.Button(bf, text="OK",     command=self._ok).pack(side='left', padx=6)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side='left', padx=6)

        self.transient(parent)
        self.wait_window()

    def _ok(self):
        try:
            name = self._name.get().strip() or "Untitled"
            tw   = float(self._tw.get())
            th   = float(self._th.get())
            grid = float(self._grid.get())
            assert tw > 0 and th > 0 and grid >= 0
        except Exception:
            messagebox.showerror("Invalid input", "Please enter valid positive numbers.", parent=self)
            return
        self.result = (name, tw, th, grid)
        self.destroy()


# ------------------------------------------------------------------
# Export scale dialog
# ------------------------------------------------------------------

class ExportDialog(tk.Toplevel):
    """Ask for an export scale factor before writing the .pat file."""

    PRESETS = [
        ("0.03937 — inches/mm (Revit default)",  1.0 / 25.4),
        ("1.0  — mm",                            1.0),
        ("0.001 — metres",                       0.001),
        ("0.00328 — feet (1/304.8)",             1.0 / 304.8),
    ]

    def __init__(self, parent, last_scale=1.0):
        super().__init__(parent)
        self.title("Export .pat — scale")
        self.resizable(False, False)
        self.grab_set()
        self.result = None

        pad = dict(padx=10, pady=4)

        msg = (
            "Revit MODEL hatches read .pat values in inches internally.\n"
            "The default scale (1/25.4) converts your mm drawing to inches.\n\n"
            "Import into Revit with Fill Pattern Scale = 1.0.\n"
            "If the size is still wrong, measure one tile in Revit and\n"
            "set scale = drawn_size_mm / displayed_size_mm."
        )
        ttk.Label(self, text=msg, justify='left').grid(
            row=0, column=0, columnspan=2, sticky='w', **pad)

        ttk.Label(self, text="Preset:").grid(row=1, column=0, sticky='w', **pad)
        self._preset_var = tk.StringVar()
        preset_labels = [p[0] for p in self.PRESETS]
        cb = ttk.Combobox(self, textvariable=self._preset_var,
                          values=preset_labels, state='readonly', width=32)
        cb.grid(row=1, column=1, sticky='w', **pad)
        cb.bind('<<ComboboxSelected>>', self._on_preset)

        ttk.Label(self, text="Custom scale:").grid(row=2, column=0, sticky='w', **pad)
        self._scale_entry = ttk.Entry(self, width=16)
        self._scale_entry.insert(0, str(last_scale))
        self._scale_entry.grid(row=2, column=1, sticky='w', **pad)

        bf = ttk.Frame(self)
        bf.grid(row=3, column=0, columnspan=2, pady=10)
        ttk.Button(bf, text="Export", command=self._ok).pack(side='left', padx=6)
        ttk.Button(bf, text="Cancel", command=self.destroy).pack(side='left', padx=6)

        self.transient(parent)
        self.wait_window()

    def _on_preset(self, event=None):
        label = self._preset_var.get()
        for lbl, val in self.PRESETS:
            if lbl == label:
                self._scale_entry.delete(0, 'end')
                self._scale_entry.insert(0, str(val))
                break

    def _ok(self):
        try:
            scale = float(self._scale_entry.get())
            assert scale > 0
        except Exception:
            messagebox.showerror("Invalid scale", "Enter a positive number.", parent=self)
            return
        self.result = scale
        self.destroy()
