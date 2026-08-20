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
    TextBlock, TextBox, ComboBox, Grid, ColumnDefinition, RowDefinition,
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
        self._id_options = {} # key name -> ElementId, for key parameters
        self._key_matches = 0
        self._key_missing = []
        self._key_diag = []   # what key_schedules() found, for diagnosis
        self._write_probe = None   # (ok, reason) from a rolled-back real write
        # The real rebuild stays locked until a dry run has come back clean —
        # it rewrites group definitions, so it should never be the first thing
        # a user manages to press.
        self._dry_run_clean = False

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
        hook('MatchKeysBtn',  self._on_match_keys)
        hook('KeySchedBtn',   self._on_key_schedule)
        hook('PreviewBtn',     self._on_preview)
        hook('RebuildTestBtn', self._on_rebuild_dry)
        hook('RebuildBtn',     self._on_rebuild_real)
        hook('ApplyBtn',       self._on_apply)
        hook('CloseBtn',       self._on_close)
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

    def _target_is_elementid(self):
        """True when the target parameter stores an ElementId (e.g. a key)."""
        if not self._elements:
            return False
        p = probe.param_by_name(self._elements[0], self._target())
        if p is None:
            return False
        try:
            return p.StorageType.ToString() == 'ElementId'
        except Exception:
            return False

    def _capture_typed(self):
        """The values currently typed, keyed by row, so a refresh can restore them."""
        self._flush_values()
        return dict((row.key, row.value) for row in self._rows)

    def _ensure_fresh(self):
        """
        Re-read the model if the cached elements have gone stale.

        Cheap guard for the paths that don't force a refresh: a single dead
        element reference is enough to crash anything that reads .Id.
        """
        if not self._elements or self._survey is None:
            return True
        if not self._survey.is_stale(self._elements[0]):
            return True
        return self._refresh_model_state(self._capture_typed())

    def _refresh_model_state(self, keep=None):
        """
        Re-read elements and groups from the document.

        Required after ANY transaction that ungroups or regroups — including one
        that was rolled back. A rollback restores the model but does not revive
        the Element wrappers already held, so every cached element is dead and
        reading .Id raises InvalidObjectException. Re-collecting is the only fix;
        the ElementIds in the survey are fine, the objects are not.

        *keep* maps row key -> typed value, so a refresh does not discard the
        user's input.
        """
        keep = keep or {}
        bic = self._selected_category()
        if bic is None:
            return False

        # Any clean verdict was measured against the previous state of the model,
        # so it can no longer authorise a real rebuild. Requiring a fresh dry run
        # is the point of the gate.
        self._dry_run_clean = False

        try:
            self._elements = probe.collect_elements(self._doc, bic)
            self._survey = probe.survey_groups(self._doc)
        except Exception as exc:
            self._status('Could not re-read the model: {}'.format(exc),
                         error=True)
            return False

        if not self._elements:
            self._status('No placed elements of that category remain.',
                         error=True)
            return False

        self._rebuild_rows()
        for row in self._rows:
            if row.key in keep:
                row.value = keep[row.key]
                self._set_ctrl_value(self._boxes.get(row.key), keep[row.key])
        self._recount()
        return True

    def _rebuild_rows(self):
        group_by = self._group_by()
        target   = self._target()
        if not group_by or not target:
            return

        self._rows = gapply.build_rows(self._elements, group_by, target)

        # Key-schedule keys, for a target with ElementId storage. Setting a key
        # means resolving a name to the key element that carries it.
        self._id_options = {}
        self._key_diag = []
        if self._target_is_elementid():
            try:
                self._key_diag = probe.key_schedules(
                    self._doc, self._selected_category())
                self._id_options = probe.key_options(
                    self._doc, self._selected_category())
            except Exception as exc:
                self._id_options = {}
                self._key_diag = [{'name': 'lookup failed: {}'.format(exc),
                                   'category_matches': False,
                                   'elements': 0, 'keys': {}}]

        # When the target is a key parameter, the value to write IS a key name —
        # and the key names normally match the group-by values, which is the
        # whole point of keying off Name. So prefill them rather than making the
        # user retype what the tool already knows. Nothing is written until
        # Apply, so prefilling is safe.
        self._key_matches = 0
        self._key_missing = []
        if self._id_options:
            self._match_keys_to_rows(announce=False)

        self._binding = probe.find_binding(self._doc, target)
        probe.probe_vary_capability(self._doc, self._binding)
        self._probe_grouped_write()

        self._build_grid()
        self._show_diagnosis()
        self._recount()

    def _probe_grouped_write(self):
        """
        Attempt one real write inside a multi-instance group and roll it back.

        Only relevant when vary-by-group cannot be enabled — otherwise the write
        route is already known to work. Asking Revit beats predicting from
        instance counts, because Revit's behaviour on group members has edge
        cases and is not fully documented.
        """
        self._write_probe = None

        if self._binding is None or self._binding.varies or self._binding.can_enable:
            return

        sample = None
        for el in self._elements:
            if self._survey.instance_count_for(el) > 1:
                sample = el
                break
        if sample is None:
            return

        target = self._target()
        current = probe.read_value(sample, target)

        # Must differ from the current value or the write is a no-op that Revit
        # would accept even where a genuine change is refused.
        if self._id_options:
            # For a key parameter the test value has to be a REAL key name.
            # A numeric placeholder is rejected as "not a valid key", which
            # would report the wrong reason and hide the group restriction.
            test_value = None
            for name in sorted(self._id_options):
                if name != current:
                    test_value = name
                    break
            if test_value is None:
                return
        else:
            test_value = probe.distinct_test_value(current)
        self._write_probe = probe.probe_write(
            self._doc, sample, target, test_value,
            id_options=self._id_options)

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

            if self._id_options:
                # A key parameter accepts only the existing key names, so offer
                # them rather than making the user retype a name that has to
                # match exactly. Blank means "leave this row alone".
                box = ComboBox()
                box.FontSize = 13
                box.Height = 23
                box.Margin = Thickness(3, 2, 4, 2)
                box.Items.Add(u'')
                for name in sorted(self._id_options):
                    box.Items.Add(name)
                box.SelectedItem = row.value if row.value in self._id_options \
                    else u''
                box.SelectionChanged += self._on_value_typed
            else:
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

    @staticmethod
    def _ctrl_value(ctrl):
        """Current value of a row control, which may be a TextBox or ComboBox."""
        if ctrl is None:
            return u''
        if isinstance(ctrl, ComboBox):
            item = ctrl.SelectedItem
            return u'' if item is None else u'{}'.format(item).strip()
        return u'{}'.format(ctrl.Text).strip()

    @staticmethod
    def _set_ctrl_value(ctrl, value):
        if ctrl is None:
            return
        if isinstance(ctrl, ComboBox):
            ctrl.SelectedItem = value or u''
        else:
            ctrl.Text = value or u''

    def _flush_values(self):
        """Copy the chosen values back onto the rows before using them."""
        for row in self._rows:
            row.value = self._ctrl_value(self._boxes.get(row.key))

    def _on_value_typed(self, sender, e):
        self._flush_values()
        self._recount()

    def _match_keys_to_rows(self, announce=True):
        """
        Point each row at the key whose name matches the row's group-by value.

        e.g. every room named 1B2P gets the key called 1B2P. Rows with no
        matching key are collected so the user is told which keys are missing
        rather than left wondering why some rows stayed blank.
        """
        lookup = dict((name.strip().lower(), name)
                      for name in self._id_options)

        self._key_matches = 0
        self._key_missing = []

        for row in self._rows:
            if not row.key:
                continue
            match = lookup.get(row.key.strip().lower())
            if match is None:
                self._key_missing.append(row.key)
                continue
            row.value = match
            self._key_matches += 1
            self._set_ctrl_value(self._boxes.get(row.key), match)

        if announce:
            self._recount()
            msg = 'Matched {} row(s) to a key of the same name.'.format(
                self._key_matches)
            if self._key_missing:
                msg += ' No key exists for: {}.'.format(
                    ', '.join(self._key_missing[:8]))
            self._status(msg, error=bool(self._key_missing))

    def _on_match_keys(self, sender, e):
        if not self._id_options:
            self._status('The selected parameter is not a key parameter — there '
                         'are no keys to match against.', error=True)
            return
        self._flush_values()
        self._match_keys_to_rows(announce=True)

    def _on_fill_blanks(self, sender, e):
        box = self._win.FindName('FillAllBox')
        value = u'{}'.format(box.Text).strip() if box is not None else u''
        if not value:
            self._status('Type the value to fill blank rows with.', error=True)
            return
        n = 0
        for row in self._rows:
            b = self._boxes.get(row.key)
            if b is not None and not self._ctrl_value(b):
                self._set_ctrl_value(b, value)
                n += 1
        self._flush_values()
        self._recount()
        self._status('Filled {} blank row(s) with "{}".'.format(n, value))

    def _on_clear_all(self, sender, e):
        for b in self._boxes.values():
            self._set_ctrl_value(b, u'')
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
            elif self._write_probe and self._write_probe[0]:
                t2.Text = ('Vary by group instance cannot be enabled, BUT a '
                           'real test write inside a multi-instance group '
                           'succeeded (and was rolled back). These will be '
                           'written; values propagate to every instance of '
                           'each group type.')
                t2.Foreground = Brushes.SeaGreen
            else:
                t2.Text = ('Vary by group instance CANNOT be enabled. {}'
                           .format(b.reason))
                if self._write_probe:
                    t2.Text += (' A real test write inside a group was also '
                                'attempted and Revit refused it: {}'
                                .format(self._write_probe[1]))
                t2.Foreground = Brushes.Firebrick

        if self._target_is_elementid() and not self._id_options:
            # An ElementId target with no resolvable keys writes nothing, so say
            # exactly what was found rather than failing later with "0 known".
            t1 = self._win.FindName('DiagParamText')
            if t1 is not None:
                if not self._key_diag:
                    t1.Text += ('  This parameter expects a KEY, but no key '
                                'schedule was found in this model. Use "Set up '
                                'key schedule" first.')
                else:
                    parts = []
                    for info in self._key_diag:
                        parts.append('"{}" ({} row(s), {} usable name(s){})'
                                     .format(info['name'], info['elements'],
                                             len(info['keys']),
                                             '' if info['category_matches']
                                             else ', different category'))
                    t1.Text += ('  This parameter expects a KEY, but none could '
                                'be read. Key schedules found: {}. If a schedule '
                                'has rows but no usable names, its key name is '
                                'held somewhere this tool did not look — tell me '
                                'what these numbers say.'.format('; '.join(parts)))
            self._status('No usable keys found — see the diagnosis above. '
                         'Nothing can be written until keys resolve.', error=True)

        if self._id_options:
            t1 = self._win.FindName('DiagParamText')
            if t1 is not None:
                extra = (
                    '  This is a KEY parameter with {} key(s) available. Rows '
                    'have been matched to the key of the same name '
                    '({} matched'.format(len(self._id_options),
                                         self._key_matches))
                if self._key_missing:
                    extra += ', no key yet for: {}'.format(
                        ', '.join(self._key_missing[:6]))
                extra += (
                    '). Press Apply to assign them. Once assigned, the key '
                    'schedule fields live on the keys — which are not in any '
                    'group — so changing those values later needs no grouped '
                    'write at all.')
                t1.Text += extra

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
        probe_ok = bool(self._write_probe and self._write_probe[0])
        self._plan = gapply.plan(self._doc, self._rows, self._target(),
                                 self._binding, self._survey,
                                 grouped_write_ok=probe_ok)
        p = self._plan

        if not p.to_write:
            self._status('Nothing to write — every row is blank or already '
                         'holds the value shown.')
            return

        msg = '{} element(s) would be written, {} already correct.'.format(
            len(p.to_write) - len(p.blocked), p.unchanged)
        if p.blocked:
            msg += (' {} cannot be written from outside a group — but they span '
                    'only {} group type(s), so Preview gives you a worksheet of '
                    '{} Edit Group visits instead.'.format(
                        len(p.blocked), len(p.blocked_by_type),
                        len(p.blocked_by_type)))
        self._status(msg, error=bool(p.blocked))

    def _status(self, text, error=False):
        tb = self._win.FindName('StatusText')
        if tb is None:
            return
        tb.Text = text
        tb.Foreground = Brushes.Firebrick if error else Brushes.Black

    # ── Preview ───────────────────────────────────────────────────────────────

    def _on_preview(self, sender, e):
        if not self._ensure_fresh():
            return
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
            out.print_md('## Blocked — these cannot be written from outside a group')
            out.print_md(
                'Revit refuses parameter writes on members of a group type that '
                'has more than one instance when the parameter cannot vary by '
                'group instance. This was verified against this model by '
                'attempting a real write and rolling it back. The Revit API has '
                'no Edit Group mode, so no add-in can do this.')

            self._print_worksheet(out, p)

            out.print_md('### Every blocked element')
            brows = [[str(probe.eid_int(el.Id)), key, reason]
                     for el, key, reason in p.blocked[:200]]
            out.print_table(table_data=brows,
                            columns=['Element id', self._group_by(), 'Reason'])
            if len(p.blocked) > 200:
                out.print_md('_{} further blocked element(s) not listed._'
                             .format(len(p.blocked) - 200))

            self._print_data_types(out)

    def _print_worksheet(self, out, p):
        """
        Per-group-type instructions for the manual Edit Group pass.

        Bucketed by group TYPE rather than listed per element, because the value
        propagates within a group type: one visit per type clears every instance
        of it. That is the difference between hundreds of edits and a handful.
        """
        if not p.blocked_by_type:
            return

        out.print_md('### Worksheet — {} group type(s) to visit'.format(
            len(p.blocked_by_type)))
        out.print_md(
            'Edit **one** instance of each group type below and set the values '
            'shown; Revit propagates them to every other instance of that same '
            'type automatically. That is {} Edit Group visits rather than {} '
            'individual element edits.'.format(
                len(p.blocked_by_type), len(p.blocked)))

        rows = []
        for gt_name in sorted(p.blocked_by_type):
            pairs = p.blocked_by_type[gt_name]
            for key in sorted(pairs):
                rows.append([gt_name, key or '(blank)', pairs[key]])
        out.print_table(
            table_data=rows,
            columns=['Group type — edit one instance', self._group_by(),
                     'Set {} to'.format(self._target())])

    def _print_data_types(self, out):
        """
        Which data types in THIS model accept vary-by-group.

        Autodesk publishes no list, so this probes every existing project
        parameter binding in one rolled-back transaction. It answers the only
        question that matters if a replacement parameter is on the table: what
        type would it have to be?
        """
        try:
            allowed, blocked = probe.probe_data_types(self._doc)
        except Exception:
            return

        out.print_md('## Which data types allow vary-by-group in this model')
        out.print_md(
            'Probed empirically against every project parameter in this model, '
            'because Autodesk does not publish the list. A replacement '
            'parameter would need one of the allowed types.')

        rows = []
        for dtype in sorted(allowed):
            rows.append(['ALLOWED', dtype, ', '.join(sorted(allowed[dtype]))[:90]])
        for dtype in sorted(blocked):
            rows.append(['blocked', dtype, ', '.join(sorted(blocked[dtype]))[:90]])
        out.print_table(table_data=rows,
                        columns=['', 'Data type', 'Example parameters'])

    # ── Key schedule ──────────────────────────────────────────────────────────

    def _on_key_schedule(self, sender, e):
        from group_params import keysched

        if not self._ensure_fresh():
            return
        self._flush_values()
        self._recount()

        values = dict((row.key, row.value)
                      for row in self._rows if row.key and row.value)
        if not values:
            self._status('Fill in the NEW column first — those become the values '
                         'carried by each key.', error=True)
            return

        target = self._target()
        report = keysched.setup(
            self._doc, self._selected_category(), self._group_by(), target,
            values, schedule_name='LB {} Key'.format(target))

        self._report_key_schedule(report, target)
        self._refresh_model_state(
            dict((row.key, row.value) for row in self._rows))

    def _report_key_schedule(self, report, target):
        from pyrevit import script
        out = script.get_output()

        out.print_md('# Key schedule setup')
        out.print_md(
            'A key-driven parameter\'s value lives on the **key element**, and '
            'keys are not inside any group — so changing it later needs no '
            'grouped write at all. That is what makes this route immune to the '
            'problem the rebuild ran into.')

        out.print_md(
            '- Schedule: **{}**{}\n'
            '- Field added for "{}": **{}**\n'
            '- Key parameter on the elements: **{}**\n'
            '- Keys already present: **{}**, created: **{}**\n'
            '- Key values set: **{}**\n'
            '- Elements assigned a key: **{}**'.format(
                report.schedule_name,
                ' *(newly created)*' if report.created_schedule else '',
                target, 'yes' if report.field_added else 'already present',
                report.key_param or '(not detected)',
                report.keys_existing, report.keys_created,
                report.values_set, report.assigned))

        if report.keys_needed:
            out.print_md('## Keys you need to add by hand')
            out.print_md(
                'The Revit API has no documented equivalent of the schedule\'s '
                '**Insert Data Row** button, so keys can only be created by '
                'copying an existing one. Open **{}**, press *Insert Data Row* '
                'once for each name below and set the Key Name, then run this '
                'again — everything else will be filled in automatically.'
                .format(report.schedule_name))
            out.print_table(
                table_data=[[n] for n in sorted(report.keys_needed)],
                columns=['Key Name to create'])

        if report.assign_failed:
            out.print_md('## Elements whose key could not be assigned')
            out.print_md(
                'These are the grouped elements. **The key schedule itself is '
                'created and populated** — only the assignment is outstanding, '
                'and it is a one-off: once assigned, every future value change '
                'is a single edit on the key.')
            out.print_table(
                table_data=[[str(eid), name, reason]
                            for eid, name, reason in report.assign_failed[:60]],
                columns=['Element id', 'Key', 'Reason'])
            if len(report.assign_failed) > 60:
                out.print_md('_{} more not listed._'.format(
                    len(report.assign_failed) - 60))

        if report.problems:
            out.print_md('## Problems')
            for msg in report.problems:
                out.print_md('- {}'.format(msg))

        if report.warnings:
            out.print_md('## Notes')
            for msg in report.warnings:
                out.print_md('- {}'.format(msg))

        if report.problems:
            self._status('Key schedule setup hit problems — see the output '
                         'window.', error=True)
        elif report.keys_needed:
            self._status('Key schedule "{}" created. {} key(s) must be added by '
                         'hand first — see the output window.'.format(
                             report.schedule_name, len(report.keys_needed)),
                         error=True)
        else:
            self._status('Key schedule "{}" set up: {} key(s), {} value(s), {} '
                         'element(s) assigned. Change values on the keys from '
                         'now on.'.format(
                             report.schedule_name,
                             report.keys_existing + report.keys_created,
                             report.values_set, report.assigned))

    # ── Rebuild group types ───────────────────────────────────────────────────

    def _on_rebuild_dry(self, sender, e):
        self._run_rebuild(dry_run=True)

    def _on_rebuild_real(self, sender, e):
        if not self._dry_run_clean:
            self._status('Run "Rebuild: dry run" first and check it comes back '
                         'clean — this rewrites group definitions.', error=True)
            return
        self._run_rebuild(dry_run=False)

    def _run_rebuild(self, dry_run):
        from group_params import regroup

        # Re-read the model first. A previous dry run performed real group
        # operations before rolling back, which leaves every cached Element
        # object dead — using them here is what raised InvalidObjectException.
        typed = self._capture_typed()
        if not self._refresh_model_state(typed):
            return

        p = self._plan
        if p is None or not p.to_write:
            self._status('Nothing to rebuild — set some values first.',
                         error=True)
            return

        # Driven by "is this element in a multi-instance group type and does it
        # need a new value", NOT by whether the plan marked it blocked. Those are
        # different questions, and gating on 'blocked' meant that whenever the
        # write path looked available the rebuild route was locked out — which is
        # precisely when the user needed it.
        by_type_id = {}
        for el, value, key in p.to_write:
            if self._survey.instance_count_for(el) <= 1:
                continue
            gt_id = self._survey.group_type_of(el)
            if gt_id is None or not value:
                continue
            by_type_id.setdefault(gt_id, {})[key] = value

        if not by_type_id:
            self._status('Nothing needs rebuilding — none of the pending '
                         'changes are inside a group type with several '
                         'instances, so Apply will handle them.', error=True)
            return

        # Snapshot what we need BEFORE the first rebuild, because each rebuild
        # invalidates the element objects the plan is holding.
        type_ids = list(by_type_id.keys())

        reports = []
        for gt_id in type_ids:
            values = by_type_id[gt_id]
            # The real ElementId from the survey, never ElementId(int) — that
            # round-trip resolved to the wrong element.
            type_eid = self._survey.type_eid.get(gt_id)
            if type_eid is None:
                continue
            reports.append(regroup.rebuild_group_type(
                self._doc, type_eid, values,
                self._target(), self._group_by(), dry_run=dry_run,
                auto_resolve=self._auto_resolve(),
                id_options=self._id_options))

        if not reports:
            self._status('Could not resolve the group types to rebuild. '
                         'Press Analyse again.', error=True)
            return

        self._report_rebuild(reports, dry_run)

        # Finish the job. The rebuild only covers elements inside multi-instance
        # group types; ungrouped elements and those in single-instance groups
        # need an ordinary write, and expecting the user to notice that and press
        # a second button was simply a gap in the tool.
        if not dry_run:
            self._apply_remaining()

    def _apply_remaining(self):
        """Write the elements that did not need a group rebuild."""
        p = self._plan          # refreshed by _report_rebuild
        if p is None or not p.to_write:
            return

        writable = [(el, v, k) for el, v, k in p.to_write
                    if self._survey.instance_count_for(el) <= 1]
        if not writable:
            return

        subset = gapply.Plan()
        subset.to_write = writable
        subset.needs_vary = p.needs_vary

        report = gapply.apply(
            self._doc, subset, self._target(), self._binding, self._survey,
            enable_vary=self._enable_vary(), restore_vary=self._restore_vary(),
            id_options=self._id_options)

        self._refresh_model_state(
            dict((row.key, row.value) for row in self._rows))

        current = self._win.FindName('StatusText')
        prefix = current.Text if current is not None else ''
        if report['written']:
            self._status('{}  Plus {} element(s) written directly (ungrouped or '
                         'in single-instance groups).'.format(
                             prefix, report['written']))
        else:
            self._status('{}  No ungrouped elements were written: {}'.format(
                prefix, report['error'] or 'nothing left to write'), error=True)

    def _report_rebuild(self, reports, dry_run):
        from pyrevit import script
        out = script.get_output()

        clean = all(r.ok for r in reports) and bool(reports)

        out.print_md('# Rebuild group types — {}'.format(
            'DRY RUN (everything rolled back)' if dry_run else 'APPLIED'))

        if dry_run:
            out.print_md('**Nothing was changed.** Each group type was rebuilt '
                         'for real and then rolled back, so this is exactly '
                         'what would happen.')

        rows = []
        for r in reports:
            rows.append([
                r.group_type, str(r.instances), str(r.written),
                '{} / {}'.format(r.members_before, r.members_after),
                str(r.preserved), str(r.restored), str(len(r.lost)),
                str(r.moved),
                '{:.1f}'.format(r.max_move_mm) if r.max_move_mm else '-',
                'origin offset' if r.uniform_offset_mm else (
                    'rotated/mirrored' if r.moved else '-'),
                'CLEAN' if r.ok else 'PROBLEM',
                'rolled back' if r.rolled_back else 'committed',
            ])
        out.print_table(
            table_data=rows,
            columns=['Group type', 'Instances', 'Values set',
                     'Members before / after', 'Data preserved',
                     'Data restored', 'Data LOST',
                     'Members MOVED', 'Worst move (mm)', 'Kind of movement',
                     'Verdict', 'Outcome'])

        out.print_md(
            '**Members MOVED is the column that matters most.** A `GroupType` '
            'stores member positions relative to its origin, `NewGroup` picks a '
            'new origin, and Revit provides no way to set one — so a rebuilt type '
            'can shift every instance by `old origin - new origin`. Where that '
            'happens the rebuild is **refused**, not compensated: moving the '
            'groups back afterwards would not restore hosting, joins or '
            'constraints broken while they were displaced.')

        moved = [r for r in reports if r.moved]
        if moved:
            out.print_md('## Group types that CANNOT be rebuilt safely')
            out.print_md(
                'Rebuilding these would move their instances, so they were '
                'refused and are unchanged. Their geometry is fine — it is purely '
                'that the new group type would carry a different origin. Do these '
                'by hand in Edit Group mode using the worksheet in *Preview*.')
            out.print_table(
                table_data=[[r.group_type or '(unnamed)', str(r.instances),
                             str(r.moved), '{:.1f}'.format(r.max_move_mm),
                             'origin offset' if r.uniform_offset_mm
                             else 'rotated / mirrored']
                            for r in moved],
                columns=['Group type', 'Instances', 'Members displaced',
                         'Worst move (mm)', 'Cause'])

        safe = [r for r in reports if not r.moved and r.ok]
        if safe:
            out.print_md('## Group types that ARE safe to rebuild')
            out.print_md(
                'These come out with every member in exactly its original '
                'position — the new group type happens to take the same origin as '
                'the old one.')
            out.print_table(
                table_data=[[r.group_type or '(unnamed)', str(r.instances),
                             str(r.written), str(r.restored)] for r in safe],
                columns=['Group type', 'Instances', 'Values set',
                         'Per-instance values restored'])

        unver = sum(r.unverifiable for r in reports)
        if unver:
            out.print_md(
                '_{} member(s) have no location point, so their position could '
                'not be verified either way._'.format(unver))

        # Each group type is its own transaction, so a failure in one does not
        # discard the others. That preserves progress, but it means a run can
        # legitimately be part-applied — which is confusing unless said plainly.
        if not dry_run:
            done = [r for r in reports if r.committed]
            failed = [r for r in reports if not r.committed]
            out.print_md('## What was and was not changed')
            out.print_md(
                '**{} of {} group type(s) were committed.** Each group type is '
                'rebuilt in its own transaction, so the ones that succeeded are '
                'saved and the ones that failed changed nothing.'.format(
                    len(done), len(reports)))
            if failed:
                out.print_md('These group types still hold their OLD values and '
                             'need attention:')
                out.print_table(
                    table_data=[[r.group_type or '(unnamed)',
                                 str(r.instances),
                                 '; '.join(r.problems) or 'unknown']
                                for r in failed],
                    columns=['Group type', 'Instances', 'Why it failed'])

        problems = [(r.group_type, msg) for r in reports for msg in r.problems]
        if problems:
            out.print_md('## Problems — these caused a rollback')
            for gt, msg in problems:
                out.print_md('- **{}**: {}'.format(gt or '(unnamed)', msg))
            if any('joined' in msg.lower() for _gt, msg in problems):
                out.print_md(
                    '> **"Can\'t keep elements joined"** means that group '
                    'contains elements joined to geometry outside it, so Revit '
                    'will not regroup them as they are. Resolving it requires '
                    '**unjoining** those elements, which is a real change to '
                    'your model — so it is not done automatically. Tick '
                    '*"Let Revit resolve errors"* under OPTIONS and run the dry '
                    'run again to see what that would do, or handle that group '
                    'manually in Edit Group mode.')

        lost = [(r.group_type, n, v) for r in reports for n, v in r.lost]
        if lost:
            out.print_md('## Per-instance data that would be LOST')
            out.print_md('This is the only category that genuinely matters — '
                         'these values existed before and are gone afterwards.')
            out.print_table(
                table_data=[[gt or '(unnamed)', n, v] for gt, n, v in lost[:100]],
                columns=['Group type', 'Parameter', 'Value that would be lost'])
            if len(lost) > 100:
                out.print_md('_{} more not listed._'.format(len(lost) - 100))

        warnings = [(r.group_type, msg) for r in reports for msg in r.warnings]
        if warnings:
            out.print_md('## Warnings — cosmetic, did NOT cause a rollback')
            for gt, msg in warnings:
                out.print_md('- **{}**: {}'.format(gt or '(unnamed)', msg))

        failed = [(r.group_type, n, why)
                  for r in reports for n, why in r.restore_failed]
        if failed:
            out.print_md('## Per-instance values that could not be restored')
            out.print_md('These held data that varies between group instances '
                         'and Revit refused to write them back. Treat this as a '
                         'blocker — the data would be lost.')
            out.print_table(
                table_data=[[gt, n, why] for gt, n, why in failed[:100]],
                columns=['Group type', 'Parameter', 'Reason'])

        mismatch = [r for r in reports if r.members_before != r.members_after]
        if mismatch:
            out.print_md('## Member count changed')
            out.print_md(
                'These group types ended up with a different number of members '
                'than they started with, which means the rebuild did not '
                'reproduce the group faithfully. Rolled back.')
            out.print_table(
                table_data=[[r.group_type or '(unnamed)',
                             str(r.members_before), str(r.members_after)]
                            for r in mismatch],
                columns=['Group type', 'Members before', 'Members after'])

        # Whether it committed or rolled back, the rebuild recreated elements, so
        # everything cached is now dead. Re-read before the user can touch
        # anything else.
        typed = {}
        try:
            typed = dict((row.key, row.value) for row in self._rows)
        except Exception:
            pass
        self._refresh_model_state(typed)

        # Set AFTER the refresh: _refresh_model_state clears the flag, since a
        # verdict measured against an older state must not authorise a real run.
        # This verdict was measured against the state the refresh just re-read,
        # so it is the one that counts.
        self._dry_run_clean = clean if dry_run else False

        if dry_run:
            if clean:
                self._status('Dry run CLEAN across {} group type(s) — nothing '
                             'was changed. "Rebuild groups" is now enabled. '
                             'Save the model first.'.format(len(reports)))
            else:
                self._status('Dry run found problems — see the output window. '
                             'Nothing was changed, and Rebuild stays disabled.',
                             error=True)
        else:
            committed = sum(1 for r in reports if r.committed)
            self._status('Rebuilt {} of {} group type(s). {}'.format(
                committed, len(reports),
                'Check the output window.' if committed < len(reports) else ''),
                error=committed < len(reports))

    # ── Apply / Close ─────────────────────────────────────────────────────────

    def _on_apply(self, sender, e):
        if not self._ensure_fresh():
            return
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

    def _auto_resolve(self):
        cb = self._win.FindName('AutoResolveCb')
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
