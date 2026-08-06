# -*- coding: utf-8 -*-
"""
WPF front end for the Keynote Manager.

Follows the same pattern as issue_register.dialog: the .xaml is parsed at
runtime with XamlReader, the window is held on self._win rather than
subclassing Window, controls are found with FindName and wired with .NET '+='.

The dialog performs NO model writes.  It returns an action and the edited model
to script.py, which owns the transaction.  That keeps every Revit modification
in the script's own API context and matches how issue_register works.

Editing model
-------------
NEW and KEYNOTE TEXT are editable TextBoxes.

The NEW box is pre-filled with the auto-computed key.  We remember what we put
there (self._autofilled); if the box no longer matches, the user has typed a
manual override, which wins over the computed value.  That is what makes both
the "auto renumber" and "type your own number" behaviours coexist without a
mode switch.

Because the grid is rebuilt on every structural change, edits must be harvested
out of the live controls before any rebuild — see _flush_edits().
"""

import io
import os

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')

import System.Windows.Markup as Markup
from System.Windows import (
    GridLength, GridUnitType, Thickness, Visibility,
    VerticalAlignment, FontWeights, TextTrimming,
    DragDrop, DragDropEffects, DataObject,
)
from System.Windows.Controls import (
    TextBlock, TextBox, CheckBox, Grid, ColumnDefinition, RowDefinition,
)
from System.Windows.Input import Cursors, Key
from System.Windows.Media import Brushes

# DragDropEffects.None cannot be written literally — None is a Python keyword.
# IronPython exposes it as None_, but fetch it defensively across versions.
_NO_EFFECT = getattr(DragDropEffects, 'None_',
                     getattr(DragDropEffects, 'None', 0))

from keynote_manager import renumber as rn
from keynote_manager import sync as ksync
from keynote_manager import settings as ksettings


def _load_xaml(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


# grip, check, old key, new key, keynote text, category
_COLS = [18, 24, 56, 78, None, 92]

_HEADER_H = 22
_GROUP_H  = 24
_ROW_H    = 25

_UNCATEGORISED = u'(uncategorised)'
_DRAG_FORMAT   = 'LBKeynoteKey'


class KeynoteDialog(object):

    def __init__(self, model, meta, file_path, refs_by_key, ref_stats,
                 settings_key=None):
        self._model     = model
        self._meta      = meta
        self._path      = file_path
        self._refs      = refs_by_key
        self._ref_stats = ref_stats
        self._skey      = settings_key

        self._action     = 'close'
        self._key_map    = {}

        self._checks     = {}   # key -> CheckBox
        self._newboxes   = {}   # key -> TextBox (new key)
        self._textboxes  = {}   # key -> TextBox (keynote text)
        self._autofilled = {}   # key -> the value WE wrote into the new box
        self._overrides  = {}   # key -> user-typed new key

        self._grip_keys   = {}  # grip TextBlock -> key
        self._row_spans   = []  # (top, bottom, kind, ref) for drop targeting
        self._assign_keys = []
        self._dup_groups  = []

        self._win = _load_xaml(
            os.path.join(os.path.dirname(__file__), 'dialog.xaml'))
        self._container = self._win.FindName('KeynoteContainer')

        self._setup_file_info()
        self._setup_dragdrop()
        # Restore preferences BEFORE wiring, so setting IsChecked does not fire
        # the change handler while the grid does not yet exist.
        self._restore_settings()
        self._wire()
        self._refresh_all()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _setup_file_info(self):
        t = self._win.FindName('FilePathText')
        if t is not None:
            t.Text = self._path or '(no keynote file assigned)'

        m = self._win.FindName('FileMetaText')
        if m is not None:
            m.Text = (
                '{}  |  {} keynotes, {} categories  |  '
                'model references: {} tags, {} types, {} materials'.format(
                    self._meta.describe() if self._meta else 'unknown encoding',
                    len(self._model.all_entries()),
                    len(self._model.categories),
                    self._ref_stats.get('tag', 0),
                    self._ref_stats.get('type', 0),
                    self._ref_stats.get('material', 0)))

    def _setup_dragdrop(self):
        if self._container is None:
            return
        # A Grid with no Background is invisible to hit-testing, so Drop would
        # never fire.
        self._container.Background = Brushes.Transparent
        self._container.AllowDrop = True
        self._container.DragOver += self._on_drag_over
        self._container.Drop += self._on_drop

    def _wire(self):
        w = self._win

        def hook(name, handler, event='Click'):
            ctrl = w.FindName(name)
            if ctrl is None:
                return
            if event == 'Click':
                ctrl.Click += handler
            elif event == 'Checked':
                ctrl.Checked += handler
                ctrl.Unchecked += handler
            elif event == 'TextChanged':
                ctrl.TextChanged += handler

        hook('AddCatBtn',     self._on_add_category)
        hook('DelCatBtn',     self._on_del_category)
        hook('CatUpBtn',      self._on_cat_up)
        hook('CatDownBtn',    self._on_cat_down)

        hook('AddKeynoteBtn', self._on_add_keynote)
        hook('EntryUpBtn',    self._on_entry_up)
        hook('EntryDownBtn',  self._on_entry_down)
        hook('AssignBtn',     self._on_assign)
        hook('UnassignBtn',   self._on_unassign)
        hook('SortBtn',       self._on_sort)
        hook('ResetKeysBtn',  self._on_reset_keys)
        hook('SelectAllBtn',  self._on_select_all)
        hook('SelectNoneBtn', self._on_select_none)

        hook('MergeBtn',      self._on_merge)

        hook('PreviewBtn',    self._on_preview)
        hook('UpdateBtn',     self._on_update)
        hook('CloseBtn',      self._on_close)

        hook('UsePrefixCb',   self._on_option_changed, 'Checked')
        hook('RenumUncatCb',  self._on_option_changed, 'Checked')
        hook('PaddingBox',    self._on_option_changed, 'TextChanged')

        # Enter in the new-keynote box adds it, so a run of keynotes can be
        # typed without reaching for the mouse.
        box = w.FindName('NewKeynoteText')
        if box is not None:
            box.KeyDown += self._on_new_keynote_key

    # ── Remembered preferences ────────────────────────────────────────────────

    def _restore_settings(self):
        values = ksettings.load(self._skey)

        cb = self._win.FindName('UsePrefixCb')
        if cb is not None:
            cb.IsChecked = bool(values.get('use_prefix', True))

        box = self._win.FindName('PaddingBox')
        if box is None:
            return
        try:
            box.Text = str(int(values.get('padding', rn.DEFAULT_PADDING)))
        except (TypeError, ValueError):
            box.Text = str(rn.DEFAULT_PADDING)

    def _persist_settings(self):
        ksettings.save(self._skey, {
            'use_prefix': self._use_prefix(),
            'padding':    self._padding(),
        })

    # ── Options ───────────────────────────────────────────────────────────────

    def _use_prefix(self):
        cb = self._win.FindName('UsePrefixCb')
        return bool(cb.IsChecked) if cb is not None else True

    def _renumber_uncategorised(self):
        cb = self._win.FindName('RenumUncatCb')
        return bool(cb.IsChecked) if cb is not None else False

    def _padding(self):
        box = self._win.FindName('PaddingBox')
        if box is None:
            return rn.DEFAULT_PADDING
        try:
            return max(1, min(6, int(str(box.Text).strip())))
        except (ValueError, TypeError):
            return rn.DEFAULT_PADDING

    # ── Harvesting live edits ─────────────────────────────────────────────────

    def _flush_edits(self):
        """
        Pull edits out of the live TextBoxes into the model and override map.

        Must run before any grid rebuild, or in-progress typing is lost.
        """
        for key, box in self._textboxes.items():
            entry = self._model.entry_by_key(key)
            if entry is not None:
                new_text = str(box.Text).strip()
                if new_text != entry.text:
                    entry.text = new_text

        for key, box in self._newboxes.items():
            typed = str(box.Text).strip()
            auto  = self._autofilled.get(key, u'')
            if typed == auto:
                continue                      # untouched — stay on auto
            if not typed:
                self._overrides.pop(key, None)  # cleared — back to auto
            else:
                self._overrides[key] = typed

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _refresh_all(self):
        self._refresh_categories()
        self._refresh_assign_combo()
        self._build_keynote_grid()
        self._refresh_duplicates()
        self._recompute()

    def _refresh_categories(self):
        lb = self._win.FindName('CategoryList')
        if lb is None:
            return
        selected = lb.SelectedIndex
        lb.Items.Clear()
        for cat in self._model.categories:
            lb.Items.Add(u'{}   {}   ({} keynotes)'.format(
                cat.key, cat.text or u'(no name)', len(cat.children)))
        if 0 <= selected < lb.Items.Count:
            lb.SelectedIndex = selected

    def _refresh_assign_combo(self):
        combo = self._win.FindName('AssignCombo')
        if combo is None:
            return
        combo.Items.Clear()
        self._assign_keys = []
        for cat in self._model.categories:
            combo.Items.Add(u'{}  {}'.format(cat.key, cat.text or u''))
            self._assign_keys.append(cat.key)
        if combo.Items.Count:
            combo.SelectedIndex = 0

    def _selected_category(self):
        lb = self._win.FindName('CategoryList')
        if lb is None or lb.SelectedIndex < 0:
            return None
        if lb.SelectedIndex >= len(self._model.categories):
            return None
        return self._model.categories[lb.SelectedIndex]

    # ── Keynote grid ──────────────────────────────────────────────────────────

    def _build_keynote_grid(self):
        container = self._container
        if container is None:
            return

        scroller = self._win.FindName('KeynoteScroller')
        offset = scroller.VerticalOffset if scroller is not None else 0

        checked_keys = set(k for k, cb in self._checks.items() if cb.IsChecked)

        container.Children.Clear()
        container.RowDefinitions.Clear()
        container.ColumnDefinitions.Clear()
        self._checks    = {}
        self._newboxes  = {}
        self._textboxes = {}
        self._grip_keys = {}
        self._row_spans = []

        for width in _COLS:
            cd = ColumnDefinition()
            cd.Width = (GridLength(1, GridUnitType.Star) if width is None
                        else GridLength(width))
            container.ColumnDefinitions.Add(cd)

        state = {'row': 0, 'y': 0.0}

        def add_row(height, kind=None, ref=None):
            rd = RowDefinition()
            rd.Height = GridLength(height)
            container.RowDefinitions.Add(rd)
            r = state['row']
            if kind is not None:
                self._row_spans.append(
                    (state['y'], state['y'] + height, kind, ref))
            state['row'] += 1
            state['y'] += height
            return r

        def place(ctrl, r, c):
            Grid.SetRow(ctrl, r)
            Grid.SetColumn(ctrl, c)
            container.Children.Add(ctrl)
            return ctrl

        def label(text, r, c, bold=False, brush=None, size=11, trim=False):
            tb = TextBlock()
            tb.Text = text or u''
            tb.FontSize = size
            tb.Margin = Thickness(3, 2, 3, 2)
            tb.VerticalAlignment = VerticalAlignment.Center
            if bold:
                tb.FontWeight = FontWeights.SemiBold
            if brush is not None:
                tb.Foreground = brush
            if trim:
                tb.TextTrimming = TextTrimming.CharacterEllipsis
                tb.ToolTip = text or u''
            return place(tb, r, c)

        def editbox(value, r, c, width=None):
            box = TextBox()
            box.Text = value or u''
            box.FontSize = 11
            box.Height = 21
            box.Margin = Thickness(2, 2, 3, 2)
            box.VerticalContentAlignment = VerticalAlignment.Center
            box.BorderBrush = Brushes.Gainsboro
            if width:
                box.Width = width
            return place(box, r, c)

        # Header
        r = add_row(_HEADER_H)
        for idx, text in enumerate(
                [u'', u'', u'KEY', u'NEW', u'KEYNOTE TEXT', u'CATEGORY']):
            label(text, r, idx, bold=True, brush=Brushes.Gray, size=10)

        def group_row(title, count, cat_key):
            gr = add_row(_GROUP_H, 'group', cat_key)
            tb = label(u'{}   ({})'.format(title, count), gr, 0,
                       bold=True, size=11)
            Grid.SetColumnSpan(tb, len(_COLS))
            tb.Foreground = Brushes.White
            tb.Background = Brushes.DimGray
            tb.Padding = Thickness(6, 2, 6, 2)
            tb.ToolTip = 'Drop a keynote here to move it into this group'

        def entry_row(entry, cat_key):
            er = add_row(_ROW_H, 'entry', entry.key)

            grip = label(u'≡', er, 0, brush=Brushes.Silver, size=13)
            grip.Cursor = Cursors.SizeAll
            grip.ToolTip = 'Drag to reorder, or onto a category header'
            grip.MouseLeftButtonDown += self._on_grip_down
            self._grip_keys[grip] = entry.key

            cb = CheckBox()
            cb.Margin = Thickness(3, 5, 0, 0)
            cb.IsChecked = entry.key in checked_keys
            place(cb, er, 1)
            self._checks[entry.key] = cb

            label(entry.key, er, 2)
            self._newboxes[entry.key]  = editbox(u'', er, 3)
            self._textboxes[entry.key] = editbox(entry.text, er, 4)
            label(cat_key or u'', er, 5, brush=Brushes.Gray, size=10, trim=True)

        for cat in self._model.categories:
            group_row(u'{}  {}'.format(cat.key, cat.text or u''),
                      len(cat.children), cat.key)
            for child in cat.children:
                entry_row(child, cat.key)

        if self._model.uncategorised:
            group_row(_UNCATEGORISED, len(self._model.uncategorised), None)
            for entry in self._model.uncategorised:
                entry_row(entry, u'')

        if scroller is not None:
            scroller.ScrollToVerticalOffset(offset)

    def _clear_checks(self):
        """
        Untick everything.

        Must run BEFORE a grid rebuild: _build_keynote_grid() carries the
        current ticks across a rebuild, so clearing afterwards would be undone
        by the next refresh.
        """
        for cb in self._checks.values():
            cb.IsChecked = False

    def _checked(self):
        """Keys of every ticked row, in display order."""
        ordered = []
        for cat in self._model.categories:
            ordered.extend(e.key for e in cat.children)
        ordered.extend(e.key for e in self._model.uncategorised)
        return [k for k in ordered
                if k in self._checks and self._checks[k].IsChecked]

    # ── Drag and drop ─────────────────────────────────────────────────────────

    def _on_grip_down(self, sender, e):
        key = self._grip_keys.get(sender)
        if not key:
            return
        self._flush_edits()
        try:
            DragDrop.DoDragDrop(sender, DataObject(_DRAG_FORMAT, key),
                                DragDropEffects.Move)
        except Exception:
            pass

    def _on_drag_over(self, sender, e):
        present = False
        try:
            present = e.Data.GetDataPresent(_DRAG_FORMAT)
        except Exception:
            pass
        e.Effects = DragDropEffects.Move if present else _NO_EFFECT
        e.Handled = True

    def _target_at(self, y):
        for top, bottom, kind, ref in self._row_spans:
            if top <= y < bottom:
                return kind, ref
        return None, None

    def _on_drop(self, sender, e):
        try:
            if not e.Data.GetDataPresent(_DRAG_FORMAT):
                return
            source_key = e.Data.GetData(_DRAG_FORMAT)
        except Exception:
            return

        y = e.GetPosition(self._container).Y
        kind, ref = self._target_at(y)
        if kind is None or source_key == ref:
            return

        self._flush_edits()

        if kind == 'group':
            # Dropped on a header — append to that group.
            self._model.move_to(source_key, ref, None)
            where = ref or 'uncategorised'
        else:
            target = self._model.entry_by_key(ref)
            if target is None:
                return
            cat_key = target.parent or None
            dest = (self._model.uncategorised if cat_key is None
                    else self._model.find_category(cat_key).children)
            self._model.move_to(source_key, cat_key, dest.index(target))
            where = cat_key or 'uncategorised'

        self._refresh_all()
        self._status('Moved {} into {}.'.format(source_key, where))
        e.Handled = True

    # ── Live key preview ──────────────────────────────────────────────────────

    def _recompute(self):
        use_prefix = self._use_prefix()

        auto = self._model.compute_keys(
            use_prefix=use_prefix,
            padding=self._padding(),
            renumber_uncategorised=self._renumber_uncategorised())

        # Manual entries win over the computed value.
        combined = dict(auto)
        for key, typed in self._overrides.items():
            combined[key] = typed
        # A "change" that lands on the same key is not a change.
        self._key_map = dict((o, n) for o, n in combined.items() if o != n)

        for key, box in self._newboxes.items():
            if key in self._overrides:
                box.Text = self._overrides[key]
                box.Foreground = Brushes.DarkOrange
                box.ToolTip = 'Manually set. Clear the box to return to auto.'
                self._autofilled[key] = None
            else:
                value = auto.get(key, u'')
                box.Text = value
                self._autofilled[key] = value
                box.Foreground = Brushes.SeaGreen if value else Brushes.Black
                box.ToolTip = ('Auto-numbered. Type here to set it yourself.'
                               if value else
                               'Unchanged. Type a key here to renumber it.')

        hint = self._win.FindName('ModeHintText')
        if hint is not None:
            hint.Text = ('Keys become <category><number>, e.g. R01'
                         if use_prefix else
                         'Flat sequential numbering — all keynotes renumbered')

        cb = self._win.FindName('RenumUncatCb')
        if cb is not None:
            cb.IsEnabled = use_prefix

        problems = self._model.validate_new_keys(self._key_map)
        affected = sum(len(self._refs.get(k, [])) for k in self._key_map)
        n_manual = len(self._overrides)
        n_added  = len(self._model.added)

        if problems:
            self._status(u'{} problem(s) must be fixed: {}'.format(
                len(problems), problems[0]), error=True)
        elif not self._model.has_changes(self._key_map):
            self._status(u'No changes yet. Add a keynote, create a category and '
                         u'assign keynotes to it, type a key in the NEW column, '
                         u'or enable flat renumbering.')
        else:
            parts = []
            if self._key_map:
                extra = u' ({} manual)'.format(n_manual) if n_manual else u''
                parts.append(u'{} key change(s){}'.format(
                    len(self._key_map), extra))
            if n_added:
                parts.append(u'{} new keynote(s)'.format(n_added))
            self._status(u'{} pending, affecting {} model reference(s). Nothing '
                         u'is written until you press Update model.'.format(
                             u' and '.join(parts), affected))

    def _status(self, text, error=False):
        tb = self._win.FindName('StatusText')
        if tb is None:
            return
        tb.Text = text
        tb.Foreground = Brushes.Firebrick if error else Brushes.Black

    # ── Duplicates ────────────────────────────────────────────────────────────

    def _refresh_duplicates(self):
        panel = self._win.FindName('DuplicatePanel')
        lb    = self._win.FindName('DuplicateList')
        info  = self._win.FindName('DuplicateInfo')
        if panel is None or lb is None:
            return

        self._dup_groups = self._model.find_duplicate_text()
        lb.Items.Clear()

        if not self._dup_groups:
            panel.Visibility = Visibility.Collapsed
            return

        panel.Visibility = Visibility.Visible
        if info is not None:
            info.Text = (
                '{} keynote text(s) are used by more than one key. Two keys for '
                'one item means tags in different views may disagree about '
                'which key describes it.'.format(len(self._dup_groups)))

        for text, entries in self._dup_groups:
            lb.Items.Add(u'{}   —   keys {}'.format(
                text, u', '.join(e.key for e in entries)))

    # ── Category handlers ─────────────────────────────────────────────────────

    def _on_add_category(self, sender, e):
        key_box  = self._win.FindName('NewCatKey')
        name_box = self._win.FindName('NewCatName')
        key  = str(key_box.Text).strip() if key_box is not None else ''
        name = str(name_box.Text).strip() if name_box is not None else ''

        err = self._model.validate_category_key(key)
        if err:
            self._status(err, error=True)
            return

        self._flush_edits()
        self._model.add_category(key, name)
        if key_box is not None:
            key_box.Text = ''
        if name_box is not None:
            name_box.Text = ''
        self._refresh_all()
        self._status('Added category "{}". Tick keynotes and press Assign, or '
                     'drag them onto its header.'.format(key))

    def _on_del_category(self, sender, e):
        cat = self._selected_category()
        if cat is None:
            self._status('Select a category to delete.', error=True)
            return
        self._flush_edits()
        n = len(cat.children)
        self._model.remove_category(cat.key)
        self._refresh_all()
        self._status('Deleted category "{}". {} keynote(s) returned to '
                     'uncategorised.'.format(cat.key, n))

    def _on_cat_up(self, sender, e):
        self._move_category(-1)

    def _on_cat_down(self, sender, e):
        self._move_category(1)

    def _move_category(self, delta):
        cat = self._selected_category()
        if cat is None:
            self._status('Select a category to move.', error=True)
            return
        self._flush_edits()
        if self._model.move_category(cat.key, delta):
            self._refresh_all()
            lb = self._win.FindName('CategoryList')
            if lb is not None:
                found = self._model.find_category(cat.key)
                if found is not None:
                    lb.SelectedIndex = self._model.categories.index(found)

    # ── Adding keynotes ───────────────────────────────────────────────────────

    def _on_new_keynote_key(self, sender, e):
        if e.Key == Key.Enter:
            self._on_add_keynote(sender, e)
            e.Handled = True

    def _on_add_keynote(self, sender, e):
        box = self._win.FindName('NewKeynoteText')
        text = str(box.Text).strip() if box is not None else ''
        if not text:
            self._status('Type the keynote text first.', error=True)
            return

        # Goes into whichever category is selected above, so adding several to
        # one category needs no extra step.
        cat = self._selected_category()
        cat_key = cat.key if cat is not None else None

        self._flush_edits()
        try:
            entry = self._model.add_entry(
                text, category_key=cat_key, padding=self._padding())
        except ValueError as exc:
            self._status(str(exc), error=True)
            return

        if box is not None:
            box.Text = ''
            box.Focus()

        self._refresh_all()
        self._status('Added keynote {} in {}. It is written to the keynote file '
                     'when you press Update model.'.format(
                         entry.key, cat_key or 'uncategorised'))

    # ── Entry handlers ────────────────────────────────────────────────────────

    def _on_entry_up(self, sender, e):
        self._move_entries(-1)

    def _on_entry_down(self, sender, e):
        self._move_entries(1)

    def _move_entries(self, delta):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to move first, or drag '
                         'the ≡ handle.', error=True)
            return
        self._flush_edits()
        # Moving down must process bottom-up (and vice versa) or the items
        # tread on each other and relative order is lost.
        for key in (keys if delta < 0 else list(reversed(keys))):
            self._model.move_entry(key, delta)
        self._build_keynote_grid()
        self._recompute()

    def _on_assign(self, sender, e):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to assign first.',
                         error=True)
            return
        combo = self._win.FindName('AssignCombo')
        if combo is None or combo.SelectedIndex < 0:
            self._status('Create a category first (key + name, then Add), '
                         'then select it here.', error=True)
            return
        self._flush_edits()
        cat_key = self._assign_keys[combo.SelectedIndex]
        self._model.assign(keys, cat_key)
        # The selection is spent once assigned — leaving it ticked makes the
        # next assignment silently re-move the same keynotes.
        self._clear_checks()
        self._refresh_all()
        self._status('Assigned {} keynote(s) to "{}".'.format(
            len(keys), cat_key))

    def _on_unassign(self, sender, e):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to remove from their '
                         'category first.', error=True)
            return
        self._flush_edits()
        self._model.assign(keys, None)
        self._clear_checks()
        self._refresh_all()
        self._status('Removed {} keynote(s) from their category.'.format(
            len(keys)))

    def _on_sort(self, sender, e):
        cat = self._selected_category()
        self._flush_edits()
        self._model.sort_group(cat.key if cat is not None else None, by='text')
        self._build_keynote_grid()
        self._recompute()
        self._status('Sorted {} by keynote text.'.format(
            '"{}"'.format(cat.key) if cat is not None else 'uncategorised'))

    def _on_reset_keys(self, sender, e):
        if not self._overrides:
            self._status('No manual keys to reset.')
            return
        n = len(self._overrides)
        self._overrides = {}
        self._recompute()
        self._status('Cleared {} manual key(s) — back to auto-numbering.'
                     .format(n))

    def _on_select_all(self, sender, e):
        for cb in self._checks.values():
            cb.IsChecked = True

    def _on_select_none(self, sender, e):
        self._clear_checks()

    def _on_merge(self, sender, e):
        lb = self._win.FindName('DuplicateList')
        if lb is None or lb.SelectedIndex < 0:
            self._status('Select a duplicate group to merge.', error=True)
            return
        if lb.SelectedIndex >= len(self._dup_groups):
            return

        _text, entries = self._dup_groups[lb.SelectedIndex]
        keys = sorted((en.key for en in entries), key=rn._natural_key)
        keep, drop = keys[0], keys[1:]

        self._flush_edits()
        self._model.merge(keep, drop)
        for key in drop:
            self._overrides.pop(key, None)
        self._refresh_all()
        self._status('Merged {} into {}. All references to {} will be '
                     'repointed.'.format(', '.join(drop), keep,
                                         ', '.join(drop)))

    def _on_option_changed(self, sender, e):
        self._flush_edits()
        self._recompute()

    # ── Preview / Update / Close ──────────────────────────────────────────────

    def _on_preview(self, sender, e):
        self._flush_edits()
        self._recompute()
        if not self._model.has_changes(self._key_map):
            self._status('Nothing to preview — no changes pending.', error=True)
            return

        report = ksync.plan(self._model, self._refs, self._key_map)
        try:
            self._print_preview(report)
            self._status('Preview written to the pyRevit output window '
                         '({} changes).'.format(report['key_changes']))
        except Exception as exc:
            self._status('Could not open the output window: {}'.format(exc),
                         error=True)

    def _print_preview(self, report):
        from pyrevit import script
        out = script.get_output()

        out.print_md('# LB Keynote Manager — preview')
        out.print_md('**Nothing has been written.** This is what *Update model* '
                     'would do.')
        out.print_md(
            '- **{key_changes}** key change(s)\n'
            '- **{added}** new keynote(s)\n'
            '- **{merges}** merge(s)\n'
            '- References to update: **{t}** tags, **{ty}** types, '
            '**{m}** materials\n'
            '- **{unreferenced}** changed key(s) are not referenced anywhere '
            'in this model'.format(
                key_changes=report['key_changes'],
                added=len(report['added']),
                merges=report['merges'],
                t=report['totals']['tag'],
                ty=report['totals']['type'],
                m=report['totals']['material'],
                unreferenced=report['unreferenced']))

        if report['added']:
            out.print_md('## New keynotes')
            out.print_table(
                table_data=[[k, (t or '')[:80]] for k, t in report['added']],
                columns=['Key', 'Keynote text'])

        rows = [[r['old'], r['new'],
                 'merge' if r['merged'] else '',
                 str(r['tags']), str(r['types']), str(r['materials']),
                 (r['text'] or '')[:70]]
                for r in report['rows']]
        out.print_table(
            table_data=rows,
            columns=['Old', 'New', '', 'Tags', 'Types', 'Materials',
                     'Keynote text'])

    def _on_update(self, sender, e):
        self._flush_edits()
        self._recompute()
        if not self._model.has_changes(self._key_map):
            self._status('Nothing to update — no changes pending.', error=True)
            return
        problems = self._model.validate_new_keys(self._key_map)
        if problems:
            self._status('Cannot update: {}'.format(problems[0]), error=True)
            return
        self._action = 'update'
        self._win.Close()

    def _on_close(self, sender, e):
        self._action = 'close'
        self._win.Close()

    # ── Public ────────────────────────────────────────────────────────────────

    def show(self):
        """
        Returns (action, state).

        action is 'update' or 'close'.  state carries everything script.py needs
        to run the apply, so the dialog itself never opens a transaction.
        """
        self._win.ShowDialog()
        # Saved on either exit path — Update or Close — so the preference sticks
        # even when the user only came in to look.
        self._persist_settings()
        return self._action, {
            'model':   self._model,
            'meta':    self._meta,
            'path':    self._path,
            'key_map': self._key_map,
            'refs':    self._refs,
        }
