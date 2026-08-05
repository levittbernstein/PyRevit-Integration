# -*- coding: utf-8 -*-
"""
WPF front end for the Keynote Manager.

Follows the same pattern as issue_register.dialog: the .xaml is parsed at
runtime with XamlReader, the window is held on self._win rather than
subclassing Window, controls are found with FindName and wired with .NET '+='.

The dialog performs NO model writes.  It returns an action and the edited model
to script.py, which owns the transaction.  That keeps every Revit modification
in the script's own API context and matches how issue_register works.
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
)
from System.Windows.Controls import (
    TextBlock, CheckBox, Grid, ColumnDefinition, RowDefinition,
)
from System.Windows.Media import Brushes

from keynote_manager import renumber as rn
from keynote_manager import sync as ksync


def _load_xaml(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


# Column widths for the keynote grid: check, old key, arrow, new key, text, category
_COLS = [26, 62, 16, 74, None, 96]

_UNCATEGORISED = u'(uncategorised)'


class KeynoteDialog(object):

    def __init__(self, model, meta, file_path, refs_by_key, ref_stats):
        self._model     = model
        self._meta      = meta
        self._path      = file_path
        self._refs      = refs_by_key
        self._ref_stats = ref_stats

        self._action     = 'close'
        self._key_map    = {}
        self._checks     = {}   # keynote key -> CheckBox
        self._newlabels  = {}   # keynote key -> TextBlock showing the new key
        self._assign_keys = []  # category keys parallel to AssignCombo items
        self._dup_groups  = []  # parallel to DuplicateList items

        self._win = _load_xaml(
            os.path.join(os.path.dirname(__file__), 'dialog.xaml'))

        self._setup_file_info()
        self._wire()
        self._refresh_all()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _setup_file_info(self):
        t = self._win.FindName('FilePathText')
        if t is not None:
            t.Text = self._path or '(no keynote file assigned)'

        m = self._win.FindName('FileMetaText')
        if m is not None:
            n_entries = len(self._model.all_entries())
            m.Text = (
                '{}  |  {} keynotes, {} categories  |  '
                'model references: {} tags, {} types, {} materials'.format(
                    self._meta.describe() if self._meta else 'unknown encoding',
                    n_entries, len(self._model.categories),
                    self._ref_stats.get('tag', 0),
                    self._ref_stats.get('type', 0),
                    self._ref_stats.get('material', 0)))

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

        hook('EntryUpBtn',    self._on_entry_up)
        hook('EntryDownBtn',  self._on_entry_down)
        hook('AssignBtn',     self._on_assign)
        hook('UnassignBtn',   self._on_unassign)
        hook('SortBtn',       self._on_sort)
        hook('SelectAllBtn',  self._on_select_all)
        hook('SelectNoneBtn', self._on_select_none)

        hook('MergeBtn',      self._on_merge)

        hook('PreviewBtn',    self._on_preview)
        hook('UpdateBtn',     self._on_update)
        hook('CloseBtn',      self._on_close)

        hook('UsePrefixCb',   self._on_option_changed, 'Checked')
        hook('RenumUncatCb',  self._on_option_changed, 'Checked')
        hook('PaddingBox',    self._on_option_changed, 'TextChanged')

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
        container = self._win.FindName('KeynoteContainer')
        if container is None:
            return

        checked_keys = set(k for k, cb in self._checks.items() if cb.IsChecked)

        container.Children.Clear()
        container.RowDefinitions.Clear()
        container.ColumnDefinitions.Clear()
        self._checks    = {}
        self._newlabels = {}

        for width in _COLS:
            cd = ColumnDefinition()
            cd.Width = (GridLength(1, GridUnitType.Star) if width is None
                        else GridLength(width))
            container.ColumnDefinitions.Add(cd)

        row = [0]  # boxed so nested helpers can increment it

        def add_row(height):
            rd = RowDefinition()
            rd.Height = GridLength(height)
            container.RowDefinitions.Add(rd)
            r = row[0]
            row[0] += 1
            return r

        def cell(text, r, c, bold=False, brush=None, size=11, trim=False):
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
                # Long keynote text must not force the grid wider than the
                # panel — ellipsise instead of wrapping or overflowing.
                tb.TextTrimming = TextTrimming.CharacterEllipsis
                tb.ToolTip = text or u''
            Grid.SetRow(tb, r)
            Grid.SetColumn(tb, c)
            container.Children.Add(tb)
            return tb

        # Header
        r = add_row(22)
        for idx, label in enumerate(
                [u'', u'KEY', u'', u'NEW', u'KEYNOTE TEXT', u'CATEGORY']):
            cell(label, r, idx, bold=True, brush=Brushes.Gray, size=10)

        def group(title, count):
            r = add_row(24)
            tb = cell(u'{}   ({})'.format(title, count), r, 0, bold=True, size=11)
            Grid.SetColumnSpan(tb, len(_COLS))
            tb.Foreground = Brushes.White
            tb.Background = Brushes.DimGray
            tb.Padding = Thickness(6, 2, 6, 2)

        def entry_row(entry, cat_key):
            r = add_row(22)

            cb = CheckBox()
            cb.Margin = Thickness(4, 3, 0, 0)
            cb.IsChecked = entry.key in checked_keys
            Grid.SetRow(cb, r)
            Grid.SetColumn(cb, 0)
            container.Children.Add(cb)
            self._checks[entry.key] = cb

            cell(entry.key, r, 1)
            cell(u'→', r, 2, brush=Brushes.Silver, size=10)
            self._newlabels[entry.key] = cell(u'', r, 3, bold=True)
            cell(entry.text, r, 4, trim=True)
            cell(cat_key or u'', r, 5, brush=Brushes.Gray, size=10)

        for cat in self._model.categories:
            group(u'{}  {}'.format(cat.key, cat.text or u''), len(cat.children))
            for child in cat.children:
                entry_row(child, cat.key)

        if self._model.uncategorised:
            group(_UNCATEGORISED, len(self._model.uncategorised))
            for entry in self._model.uncategorised:
                entry_row(entry, u'')

    def _checked(self):
        """Keys of every ticked row, in display order."""
        ordered = []
        for cat in self._model.categories:
            ordered.extend(e.key for e in cat.children)
        ordered.extend(e.key for e in self._model.uncategorised)
        return [k for k in ordered
                if k in self._checks and self._checks[k].IsChecked]

    # ── Live key preview ──────────────────────────────────────────────────────

    def _recompute(self):
        use_prefix = self._use_prefix()
        self._key_map = self._model.compute_keys(
            use_prefix=use_prefix,
            padding=self._padding(),
            renumber_uncategorised=self._renumber_uncategorised())

        for key, label in self._newlabels.items():
            new = self._key_map.get(key)
            if new is None:
                label.Text = u''
                label.Foreground = Brushes.Silver
            else:
                label.Text = new
                label.Foreground = Brushes.SeaGreen

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

        if problems:
            self._status(u'{} problem(s) must be fixed: {}'.format(
                len(problems), problems[0]), error=True)
        elif not self._key_map:
            self._status(u'No key changes yet. Create a category and assign '
                         u'keynotes to it, or enable flat renumbering.')
        else:
            self._status(u'{} key change(s) pending, affecting {} model '
                         u'reference(s). Nothing is written until you press '
                         u'Update model.'.format(len(self._key_map), affected))

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
                'one item means tags in different views may disagree about which '
                'key describes it.'.format(len(self._dup_groups)))

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

        self._model.add_category(key, name)
        if key_box is not None:
            key_box.Text = ''
        if name_box is not None:
            name_box.Text = ''
        self._refresh_all()

    def _on_del_category(self, sender, e):
        cat = self._selected_category()
        if cat is None:
            self._status('Select a category to delete.', error=True)
            return
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
        if self._model.move_category(cat.key, delta):
            lb = self._win.FindName('CategoryList')
            new_index = self._model.categories.index(
                self._model.find_category(cat.key))
            self._refresh_all()
            if lb is not None:
                lb.SelectedIndex = new_index

    # ── Entry handlers ────────────────────────────────────────────────────────

    def _on_entry_up(self, sender, e):
        self._move_entries(-1)

    def _on_entry_down(self, sender, e):
        self._move_entries(1)

    def _move_entries(self, delta):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to move first.', error=True)
            return
        # Moving down must process from the bottom up (and vice versa) or the
        # items tread on each other and relative order is lost.
        for key in (keys if delta < 0 else list(reversed(keys))):
            self._model.move_entry(key, delta)
        self._build_keynote_grid()
        self._recompute()

    def _on_assign(self, sender, e):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to assign first.', error=True)
            return
        combo = self._win.FindName('AssignCombo')
        if combo is None or combo.SelectedIndex < 0:
            self._status('Create a category first, then select it here.', error=True)
            return
        cat_key = self._assign_keys[combo.SelectedIndex]
        self._model.assign(keys, cat_key)
        self._refresh_all()
        self._status('Assigned {} keynote(s) to "{}".'.format(len(keys), cat_key))

    def _on_unassign(self, sender, e):
        keys = self._checked()
        if not keys:
            self._status('Tick the keynotes you want to remove from their '
                         'category first.', error=True)
            return
        self._model.assign(keys, None)
        self._refresh_all()
        self._status('Removed {} keynote(s) from their category.'.format(len(keys)))

    def _on_sort(self, sender, e):
        cat = self._selected_category()
        self._model.sort_group(cat.key if cat is not None else None, by='text')
        self._build_keynote_grid()
        self._recompute()
        self._status('Sorted {} by keynote text.'.format(
            '"{}"'.format(cat.key) if cat is not None else 'uncategorised'))

    def _on_select_all(self, sender, e):
        for cb in self._checks.values():
            cb.IsChecked = True

    def _on_select_none(self, sender, e):
        for cb in self._checks.values():
            cb.IsChecked = False

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

        self._model.merge(keep, drop)
        self._refresh_all()
        self._status('Merged {} into {}. All references to {} will be '
                     'repointed.'.format(', '.join(drop), keep, ', '.join(drop)))

    def _on_option_changed(self, sender, e):
        self._recompute()

    # ── Preview / Update / Close ──────────────────────────────────────────────

    def _on_preview(self, sender, e):
        if not self._key_map:
            self._status('Nothing to preview — no keys would change.', error=True)
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
            '- **{merges}** merge(s)\n'
            '- References to update: **{t}** tags, **{ty}** types, '
            '**{m}** materials\n'
            '- **{unreferenced}** changed key(s) are not referenced anywhere '
            'in this model'.format(
                key_changes=report['key_changes'],
                merges=report['merges'],
                t=report['totals']['tag'],
                ty=report['totals']['type'],
                m=report['totals']['material'],
                unreferenced=report['unreferenced']))

        rows = [[r['old'], r['new'],
                 'merge' if r['merged'] else '',
                 str(r['tags']), str(r['types']), str(r['materials']),
                 (r['text'] or '')[:70]]
                for r in report['rows']]
        out.print_table(
            table_data=rows,
            columns=['Old', 'New', '', 'Tags', 'Types', 'Materials', 'Keynote text'])

    def _on_update(self, sender, e):
        if not self._key_map:
            self._status('Nothing to update — no keys would change.', error=True)
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
        return self._action, {
            'model':   self._model,
            'meta':    self._meta,
            'path':    self._path,
            'key_map': self._key_map,
            'refs':    self._refs,
        }
