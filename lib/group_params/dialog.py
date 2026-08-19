# -*- coding: utf-8 -*-
"""
WPF front end for Set Parameter in Groups.

Same pattern as the other LB dialogs: dialog.xaml parsed at runtime with
XamlReader, window held on self._win rather than subclassing Window, controls
found with FindName and wired with .NET '+='.

The dialog runs read-only probes (each rolled back) but performs no lasting
write — Apply hands back to script.py, which owns the real transaction.
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
    GridLength, GridUnitType, Thickness, VerticalAlignment,
    FontWeights, TextTrimming,
)
from System.Windows.Controls import (
    TextBlock, TextBox, Grid, ColumnDefinition, RowDefinition,
)
from System.Windows.Media import Brushes

from group_params import apply as gapply
from group_params import probe


def _load_xaml(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


# group-by value, element count, in-groups, current value, new value
_COLS = [None, 70, 150, 150, 110]

_HEADER_H = 26
_ROW_H    = 28


class GroupParamDialog(object):

    def __init__(self, doc, categories):
        self._doc        = doc
        self._categories = categories   # [(label, BuiltInCategory)]

        self._action   = 'close'
        self._elements = []
        self._rows     = []
        self._binding  = None
        self._survey   = None
        self._plan     = None
        self._boxes    = {}   # row key -> TextBox

        self._win = _load_xaml(
            os.path.join(os.path.dirname(__file__), 'dialog.xaml'))
        self._container = self._win.FindName('RowContainer')

        self._fill_categories()
        self._wire()
        self._status('Choose a category, then press Analyse.')

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _fill_categories(self):
        combo = self._win.FindName('CategoryCombo')
        if combo is None:
            return
        for label, _bic in self._categories:
            combo.Items.Add(label)
        if combo.Items.Count:
            combo.SelectedIndex = 0
        combo.SelectionChanged += self._on_category_changed

    def _wire(self):
        w = self._win

        def hook(name, handler, event='Click'):
            ctrl = w.FindName(name)
            if ctrl is None:
                return
            if event == 'Click':
                ctrl.Click += handler
            elif event == 'SelectionChanged':
                ctrl.SelectionChanged += handler

        hook('AnalyseBtn',    self._on_analyse)
        hook('FillBlanksBtn', self._on_fill_blanks)
        hook('ClearAllBtn',   self._on_clear_all)
        hook('PreviewBtn',    self._on_preview)
        hook('ApplyBtn',      self._on_apply)
        hook('CloseBtn',      self._on_close)
        hook('GroupByCombo',  self._on_param_changed, 'SelectionChanged')
        hook('TargetCombo',   self._on_param_changed, 'SelectionChanged')

    # ── Selections ────────────────────────────────────────────────────────────

    def _selected_category(self):
        combo = self._win.FindName('CategoryCombo')
        if combo is None or combo.SelectedIndex < 0:
            return None
        return self._categories[combo.SelectedIndex][1]

    def _combo_text(self, name):
        combo = self._win.FindName(name)
        if combo is None or combo.SelectedIndex < 0:
            return u''
        return u'{}'.format(combo.SelectedItem)

    def _group_by(self):
        return self._combo_text('GroupByCombo')

    def _target(self):
        return self._combo_text('TargetCombo')

    def _on_category_changed(self, sender, e):
        self._elements = []
        self._rows = []
        self._clear_rows()
        self._status('Category changed — press Analyse.')

    def _on_param_changed(self, sender, e):
        # Re-bucket only once both parameters are chosen and elements are loaded.
        if self._elements and self._group_by() and self._target():
            self._rebuild_rows()

    # ── Analyse ───────────────────────────────────────────────────────────────

    def _on_analyse(self, sender, e):
        bic = self._selected_category()
        if bic is None:
            self._status('Choose a category first.', error=True)
            return

        self._elements = probe.collect_elements(self._doc, bic)
        if not self._elements:
            self._status('No placed elements of that category in this model.',
                         error=True)
            self._clear_rows()
            return

        sample = self._elements[0]
        self._survey = probe.survey_groups(self._doc)

        self._populate_param_combos(sample)

        if self._group_by() and self._target():
            self._rebuild_rows()
        else:
            self._status('{} element(s) found. Choose "Group by" and "Set '
                         'parameter".'.format(len(self._elements)))

    def _populate_param_combos(self, sample):
        group_names  = probe.all_parameter_names(sample)
        target_names = probe.writable_parameter_names(sample)

        for combo_name, names, preferred in (
                ('GroupByCombo', group_names,  ['Name', 'Type Name', 'Number']),
                ('TargetCombo',  target_names, [])):
            combo = self._win.FindName(combo_name)
            if combo is None:
                continue
            current = u'{}'.format(combo.SelectedItem) if combo.SelectedIndex >= 0 else u''
            combo.Items.Clear()
            for n in names:
                combo.Items.Add(n)

            # Keep the user's choice across a re-analyse where possible.
            chosen = -1
            if current and current in names:
                chosen = names.index(current)
            else:
                for pref in preferred:
                    if pref in names:
                        chosen = names.index(pref)
                        break
            if chosen < 0 and combo.Items.Count:
                chosen = 0
            if combo.Items.Count:
                combo.SelectedIndex = chosen

    # ── Rows ──────────────────────────────────────────────────────────────────

    def _rebuild_rows(self):
        group_by = self._group_by()
        target   = self._target()
        if not group_by or not target:
            return

        self._rows = gapply.build_rows(self._elements, group_by, target)
        self._binding = probe.find_binding(self._doc, target)
        probe.probe_vary_capability(self._doc, self._binding)

        self._build_grid()
        self._show_diagnosis()
        self._recount()

    def _clear_rows(self):
        if self._container is None:
            return
        self._container.Children.Clear()
        self._container.RowDefinitions.Clear()
        self._container.ColumnDefinitions.Clear()
        self._boxes = {}

    def _build_grid(self):
        container = self._container
        if container is None:
            return

        self._clear_rows()

        for width in _COLS:
            cd = ColumnDefinition()
            cd.Width = (GridLength(1, GridUnitType.Star) if width is None
                        else GridLength(width))
            container.ColumnDefinitions.Add(cd)

        state = {'row': 0}

        def add_row(height):
            rd = RowDefinition()
            rd.Height = GridLength(height)
            container.RowDefinitions.Add(rd)
            r = state['row']
            state['row'] += 1
            return r

        def label(text, r, c, bold=False, brush=None, size=13, trim=False):
            tb = TextBlock()
            tb.Text = text or u''
            tb.FontSize = size
            tb.Margin = Thickness(4, 3, 4, 3)
            tb.VerticalAlignment = VerticalAlignment.Center
            if bold:
                tb.FontWeight = FontWeights.SemiBold
            if brush is not None:
                tb.Foreground = brush
            if trim:
                tb.TextTrimming = TextTrimming.CharacterEllipsis
                tb.ToolTip = text or u''
            Grid.SetRow(tb, r)
            Grid.SetColumn(tb, c)
            container.Children.Add(tb)
            return tb

        hdr = add_row(_HEADER_H)
        for idx, text in enumerate([self._group_by() or u'VALUE', u'COUNT',
                                    u'IN GROUPS', u'CURRENT', u'NEW']):
            label(text.upper(), hdr, idx, bold=True, brush=Brushes.Gray, size=12)

        for row in self._rows:
            r = add_row(_ROW_H)

            label(row.key if row.key else u'(blank)', r, 0, trim=True)
            label(u'{}'.format(row.count), r, 1, brush=Brushes.Gray, size=12)

            grouped = sum(1 for el in row.elements
                          if self._survey.is_grouped(el))
            multi = sum(1 for el in row.elements
                        if self._survey.instance_count_for(el) > 1)
            if grouped:
                note = u'{} of {}'.format(grouped, row.count)
                if multi:
                    note += u'  ({} in repeated)'.format(multi)
                label(note, r, 2, brush=Brushes.DarkOrange, size=12)
            else:
                label(u'no', r, 2, brush=Brushes.Gray, size=12)

            if row.mixed:
                label(u'(mixed)', r, 3, brush=Brushes.Firebrick, size=12)
            else:
                label(row.existing, r, 3, brush=Brushes.Gray, size=12)

            box = TextBox()
            box.Text = row.value or u''
            box.FontSize = 13
            box.Height = 23
            box.Margin = Thickness(3, 2, 4, 2)
            box.VerticalContentAlignment = VerticalAlignment.Center
            box.BorderBrush = Brushes.Gainsboro
            box.TextChanged += self._on_value_typed
            Grid.SetRow(box, r)
            Grid.SetColumn(box, 4)
            container.Children.Add(box)
            self._boxes[row.key] = box

    def _flush_values(self):
        """Copy typed values back onto the rows before using them."""
        for row in self._rows:
            box = self._boxes.get(row.key)
            if box is not None:
                row.value = u'{}'.format(box.Text).strip()

    def _on_value_typed(self, sender, e):
        self._flush_values()
        self._recount()

    def _on_fill_blanks(self, sender, e):
        box = self._win.FindName('FillAllBox')
        value = u'{}'.format(box.Text).strip() if box is not None else u''
        if not value:
            self._status('Type the value to fill blank rows with.', error=True)
            return
        n = 0
        for row in self._rows:
            b = self._boxes.get(row.key)
            if b is not None and not u'{}'.format(b.Text).strip():
                b.Text = value
                n += 1
        self._flush_values()
        self._recount()
        self._status('Filled {} blank row(s) with "{}".'.format(n, value))

    def _on_clear_all(self, sender, e):
        for b in self._boxes.values():
            b.Text = u''
        self._flush_values()
        self._recount()
        self._status('Cleared every row — nothing would be written.')

    # ── Diagnosis ─────────────────────────────────────────────────────────────

    def _show_diagnosis(self):
        b = self._binding
        target = self._target()

        t1 = self._win.FindName('DiagParamText')
        if t1 is not None:
            if b.is_project_parameter:
                kind = ('instance' if b.is_instance
                        else 'TYPE' if b.is_instance is False else 'unknown')
                t1.Text = ('"{}" is a project/shared parameter — data type {}, '
                           'bound as a {} parameter.'.format(
                               target, b.type_name or 'unknown', kind))
            else:
                t1.Text = ('"{}" is a built-in Revit parameter, so it has no '
                           '"vary by group instance" setting at all.'.format(
                               target))

        t2 = self._win.FindName('DiagVaryText')
        if t2 is not None:
            if b.varies:
                t2.Text = ('Vary by group instance is ALREADY ON, so writes '
                           'inside groups will work.')
                t2.Foreground = Brushes.SeaGreen
            elif b.can_enable:
                t2.Text = ('Vary by group instance is off but CAN be enabled — '
                           'verified against this model. Enabling it is what '
                           'removes the "Edit Group mode" restriction.')
                t2.Foreground = Brushes.SeaGreen
            else:
                t2.Text = ('Vary by group instance CANNOT be enabled. {}'
                           .format(b.reason))
                t2.Foreground = Brushes.Firebrick

        t3 = self._win.FindName('DiagGroupText')
        if t3 is not None:
            grouped = sum(1 for el in self._elements
                          if self._survey.is_grouped(el))
            multi = sum(1 for el in self._elements
                        if self._survey.instance_count_for(el) > 1)
            single = grouped - multi
            t3.Text = ('{} of {} element(s) are inside groups: {} in group types '
                       'placed once (writable regardless), {} in group types '
                       'placed more than once.'.format(
                           grouped, len(self._elements), single, multi))

    # ── Status ────────────────────────────────────────────────────────────────

    def _recount(self):
        if not self._rows or self._binding is None:
            return
        self._plan = gapply.plan(self._doc, self._rows, self._target(),
                                 self._binding, self._survey)
        p = self._plan

        if not p.to_write:
            self._status('Nothing to write — every row is blank or already '
                         'holds the value shown.')
            return

        msg = '{} element(s) would be written, {} already correct.'.format(
            len(p.to_write), p.unchanged)
        if p.blocked:
            msg += (' {} CANNOT be written — see Preview for why.'
                    .format(len(p.blocked)))
        self._status(msg, error=bool(p.blocked))

    def _status(self, text, error=False):
        tb = self._win.FindName('StatusText')
        if tb is None:
            return
        tb.Text = text
        tb.Foreground = Brushes.Firebrick if error else Brushes.Black

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_preview(self, sender, e):
        self._flush_values()
        self._recount()
        if self._plan is None or not self._plan.to_write:
            self._status('Nothing to preview.', error=True)
            return
        try:
            self._print_preview()
            self._status('Preview written to the pyRevit output window.')
        except Exception as exc:
            self._status('Could not open the output window: {}'.format(exc),
                         error=True)

    def _print_preview(self):
        from pyrevit import script
        out = script.get_output()
        p = self._plan
        b = self._binding

        out.print_md('# LB Set Parameter in Groups — preview')
        out.print_md('**Nothing has been written.**')

        out.print_md('## Diagnosis')
        out.print_md(
            '- Parameter: **{}** ({}), {}\n'
            '- Vary by group instance: **{}**\n'
            '- {}'.format(
                self._target(), b.type_name or 'unknown type',
                'project/shared parameter' if b.is_project_parameter
                else 'built-in parameter',
                'already on' if b.varies
                else ('off, can be enabled' if b.can_enable else 'off, CANNOT be enabled'),
                b.reason or ''))

        out.print_md('## Values to write')
        rows = []
        for row in self._rows:
            if not row.value:
                continue
            grouped = sum(1 for el in row.elements if self._survey.is_grouped(el))
            rows.append([row.key or '(blank)', str(row.count), str(grouped),
                         '(mixed)' if row.mixed else (row.existing or ''),
                         row.value])
        out.print_table(table_data=rows,
                        columns=[self._group_by(), 'Elements', 'In groups',
                                 'Current', 'New'])

        if p.warnings:
            out.print_md('## Notes')
            for w in p.warnings:
                out.print_md('- {}'.format(w))

        if p.blocked:
            out.print_md('## Blocked — these cannot be written')
            out.print_md('Revit refuses parameter writes on members of a group '
                         'type that has more than one instance when the '
                         'parameter cannot vary by group instance. There is no '
                         'API route around this.')
            brows = [[str(probe.eid_int(el.Id)), key, reason]
                     for el, key, reason in p.blocked[:200]]
            out.print_table(table_data=brows,
                            columns=['Element id', self._group_by(), 'Reason'])
            if len(p.blocked) > 200:
                out.print_md('_{} further blocked element(s) not listed._'
                             .format(len(p.blocked) - 200))

    # ── Apply / Close ─────────────────────────────────────────────────────────

    def _on_apply(self, sender, e):
        self._flush_values()
        self._recount()
        if self._plan is None or not self._plan.to_write:
            self._status('Nothing to apply.', error=True)
            return
        self._action = 'apply'
        self._win.Close()

    def _on_close(self, sender, e):
        self._action = 'close'
        self._win.Close()

    def _enable_vary(self):
        cb = self._win.FindName('EnableVaryCb')
        return bool(cb.IsChecked) if cb is not None else True

    def _restore_vary(self):
        cb = self._win.FindName('RestoreVaryCb')
        return bool(cb.IsChecked) if cb is not None else False

    # ── Public ────────────────────────────────────────────────────────────────

    def show(self):
        self._win.ShowDialog()
        return self._action, {
            'plan':         self._plan,
            'target':       self._target(),
            'binding':      self._binding,
            'survey':       self._survey,
            'enable_vary':  self._enable_vary(),
            'restore_vary': self._restore_vary(),
        }
