# -*- coding: utf-8 -*-
"""
WPF settings dialog for the LB Issue Register exporter.
Runs under IronPython inside Revit.
"""

import os
import io

import clr
import System
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')

from System.Windows import (
    Window, DependencyObject, LogicalTreeHelper,
    GridLength, Thickness, HorizontalAlignment, VerticalAlignment,
    TextWrapping, TextAlignment,
)
from System.Windows.Controls import (
    TextBox, TextBlock, Grid, ColumnDefinition, RowDefinition,
    ScrollViewer, Button,
)
from System.Windows.Media import Brushes
import System.Windows.Markup as Markup


def _load_xaml(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


class ExportDialog(object):

    def __init__(self, issue_keys, settings, all_packages=None, project_info=None):
        self._issue_keys       = issue_keys
        self._settings         = settings
        self._all_packages     = all_packages or []
        self._project_info     = project_info or {}
        self._confirmed        = False
        self._name_boxes       = []
        self._code_boxes       = {}
        self._selected_row_idx = None

        # Package list controls (set in _setup_packages)
        self._included_lb = None
        self._excluded_lb = None

        # Project info controls (set in _setup_project_info)
        self._reg_title_box = None
        self._reg_date_box  = None
        self._reg_rev_box   = None

        xaml_path = os.path.join(os.path.dirname(__file__), 'dialog.xaml')
        self._win = _load_xaml(xaml_path)

        self._recipients = [dict(r) for r in settings.get('recipients', [])]
        self._setup_project_info()
        self._setup_packages()
        self._build_grid()
        self._wire_buttons()

    # ------------------------------------------------------------------
    # Project information fields
    # ------------------------------------------------------------------

    def _setup_project_info(self):
        self._reg_title_box = self._win.FindName('RegisterTitle')
        self._reg_date_box  = self._win.FindName('RegisterIssueDate')
        self._reg_rev_box   = self._win.FindName('RegisterRevision')

        if self._reg_title_box is not None:
            self._reg_title_box.Text = self._settings.get(
                'register_title',
                self._project_info.get('project_name', '')
            )
        if self._reg_date_box is not None:
            self._reg_date_box.Text = self._settings.get('register_issue_date', '')
        if self._reg_rev_box is not None:
            self._reg_rev_box.Text = self._settings.get('register_revision', '')

    # ------------------------------------------------------------------
    # Drawing packages lists
    # ------------------------------------------------------------------

    def _setup_packages(self):
        self._included_lb = self._win.FindName('IncludedPackages')
        self._excluded_lb = self._win.FindName('ExcludedPackages')
        if self._included_lb is None or self._excluded_lb is None:
            return

        excluded_set = set(self._settings.get('excluded_packages', []))
        for pkg in sorted(self._all_packages):
            if pkg in excluded_set:
                self._excluded_lb.Items.Add(pkg)
            else:
                self._included_lb.Items.Add(pkg)

        self._included_lb.MouseDoubleClick += self._on_move_to_excluded
        self._excluded_lb.MouseDoubleClick += self._on_move_to_included

    def _move_items(self, src_lb, dst_lb):
        selected = list(src_lb.SelectedItems)
        if not selected:
            return
        for item in selected:
            src_lb.Items.Remove(item)
        existing = [dst_lb.Items[i] for i in range(dst_lb.Items.Count)]
        merged = sorted(existing + selected)
        dst_lb.Items.Clear()
        for item in merged:
            dst_lb.Items.Add(item)

    def _on_move_to_excluded(self, sender, e):
        self._move_items(self._included_lb, self._excluded_lb)

    def _on_move_to_included(self, sender, e):
        self._move_items(self._excluded_lb, self._included_lb)

    # ------------------------------------------------------------------
    # Button wiring
    # ------------------------------------------------------------------

    def _wire_buttons(self):
        def _walk(el):
            if not isinstance(el, DependencyObject):
                return None
            if isinstance(el, Button):
                label = str(el.Content) if el.Content is not None else ''
                _buttons[label] = el
            for child in LogicalTreeHelper.GetChildren(el):
                _walk(child)

        _buttons = {}
        _walk(self._win)

        if '+ Add recipient'    in _buttons: _buttons['+ Add recipient'].Click    += self._on_add
        if '− Remove selected'  in _buttons: _buttons['− Remove selected'].Click  += self._on_remove
        if 'Export Register'    in _buttons: _buttons['Export Register'].Click    += self._on_export
        if 'Cancel'             in _buttons: _buttons['Cancel'].Click             += self._on_cancel

        btn = self._win.FindName('MoveToExcluded')
        if btn: btn.Click += self._on_move_to_excluded
        btn = self._win.FindName('MoveToIncluded')
        if btn: btn.Click += self._on_move_to_included

    # ------------------------------------------------------------------
    # Distribution grid
    # ------------------------------------------------------------------

    def _build_grid(self):
        container = self._win.FindName('DistributionContainer')
        if container is None:
            return

        container.Children.Clear()
        container.ColumnDefinitions.Clear()
        container.RowDefinitions.Clear()
        self._name_boxes = []
        self._code_boxes = {}

        n_issues = len(self._issue_keys)

        # ── Column definitions ────────────────────────────────────────
        # Col 0: recipient name (170px)
        cd = ColumnDefinition()
        cd.Width = GridLength(170)
        container.ColumnDefinitions.Add(cd)

        # Cols 1..n: one per issue date (70px each)
        for _ in range(n_issues):
            cd = ColumnDefinition()
            cd.Width = GridLength(70)
            container.ColumnDefinitions.Add(cd)

        # ── Header row ────────────────────────────────────────────────
        rd = RowDefinition()
        rd.Height = GridLength(46)
        container.RowDefinitions.Add(rd)

        self._header_cell(container, 'RECIPIENT', 0, 0)
        for col_idx, (date_str, _issued_by) in enumerate(self._issue_keys):
            label = self._fmt_date(date_str) + '\nP{:02d}'.format(col_idx + 1)
            self._header_cell(container, label, 0, col_idx + 1)

        # ── Recipient rows ────────────────────────────────────────────
        if not self._recipients:
            # Placeholder row
            rd = RowDefinition()
            rd.Height = GridLength(36)
            container.RowDefinitions.Add(rd)

            ph = TextBlock()
            ph.Text = "Click '+ Add recipient' below to add rows."
            ph.Foreground = Brushes.Gray
            ph.FontSize = 11
            ph.VerticalAlignment = VerticalAlignment.Center
            ph.Margin = Thickness(10, 0, 0, 0)
            Grid.SetRow(ph, 1)
            Grid.SetColumn(ph, 0)
            if n_issues > 0:
                Grid.SetColumnSpan(ph, n_issues + 1)
            container.Children.Add(ph)
            return

        saved_issues = self._settings.get('issues', {})

        for row_idx, recipient in enumerate(self._recipients):
            rd = RowDefinition()
            rd.Height = GridLength(28)
            container.RowDefinitions.Add(rd)

            r_name = recipient.get('name', '')
            bg = Brushes.White if row_idx % 2 == 0 else Brushes.WhiteSmoke

            # Name TextBox
            name_box = TextBox()
            name_box.Text                    = r_name
            name_box.Margin                  = Thickness(1)
            name_box.Padding                 = Thickness(6, 2, 6, 2)
            name_box.BorderBrush             = Brushes.LightGray
            name_box.BorderThickness         = Thickness(0, 0, 1, 1)
            name_box.Background              = bg
            name_box.FontSize                = 11
            name_box.VerticalContentAlignment = VerticalAlignment.Center
            name_box.GotFocus += self._make_focus_handler(row_idx)
            Grid.SetRow(name_box, row_idx + 1)
            Grid.SetColumn(name_box, 0)
            container.Children.Add(name_box)
            self._name_boxes.append(name_box)

            # Code cells
            for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
                key        = '{}||{}'.format(date_str, issued_by)
                saved_code = saved_issues.get(key, {}).get(r_name, '')

                cell = TextBox()
                cell.Text                     = saved_code
                cell.Margin                   = Thickness(1)
                cell.Padding                  = Thickness(2, 0, 2, 0)
                cell.BorderBrush              = Brushes.LightGray
                cell.BorderThickness          = Thickness(0, 0, 1, 1)
                cell.Background               = bg
                cell.FontSize                 = 10
                cell.TextAlignment            = TextAlignment.Center
                cell.VerticalContentAlignment = VerticalAlignment.Center
                cell.MaxLength                = 3
                cell.GotFocus                += self._make_focus_handler(row_idx)
                Grid.SetRow(cell, row_idx + 1)
                Grid.SetColumn(cell, col_idx + 1)
                container.Children.Add(cell)
                self._code_boxes[(row_idx, col_idx)] = cell

    def _make_focus_handler(self, row_idx):
        def handler(sender, e):
            self._select_row(row_idx)
        return handler

    def _select_row(self, row_idx):
        self._selected_row_idx = row_idx
        for i, nb in enumerate(self._name_boxes):
            is_sel = (i == row_idx)
            nb.Background = Brushes.LightBlue if is_sel else (
                Brushes.White if i % 2 == 0 else Brushes.WhiteSmoke)
        for (ri, ci), cell in self._code_boxes.items():
            is_sel = (ri == row_idx)
            cell.Background = Brushes.LightBlue if is_sel else (
                Brushes.White if ri % 2 == 0 else Brushes.WhiteSmoke)

    def _header_cell(self, container, text, row, col):
        tb = TextBlock()
        tb.Text              = text
        tb.TextWrapping      = TextWrapping.Wrap
        tb.FontSize          = 9
        tb.TextAlignment     = TextAlignment.Center
        tb.VerticalAlignment = VerticalAlignment.Center
        tb.Background        = Brushes.Black
        tb.Foreground        = Brushes.White
        tb.Padding           = Thickness(4, 3, 4, 3)
        tb.Margin            = Thickness(0, 0, 1, 1)
        Grid.SetRow(tb, row)
        Grid.SetColumn(tb, col)
        container.Children.Add(tb)

    @staticmethod
    def _fmt_date(date_str):
        for fmt in ('%d/%m/%y', '%d/%m/%Y', '%d.%m.%y', '%d.%m.%Y'):
            try:
                from datetime import datetime
                return datetime.strptime(date_str, fmt).strftime('%d/%m/%y')
            except (ValueError, ImportError):
                pass
        return date_str

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_add(self, sender, e):
        self._recipients.append({'name': '', 'row': len(self._recipients) + 4})
        self._build_grid()

    def _on_remove(self, sender, e):
        if not self._recipients:
            return
        idx = self._selected_row_idx
        if idx is None or idx >= len(self._recipients):
            idx = len(self._recipients) - 1
        self._recipients.pop(idx)
        self._selected_row_idx = None
        self._build_grid()

    def _on_export(self, sender, e):
        self._confirmed = True
        self._win.Close()

    def _on_cancel(self, sender, e):
        self._confirmed = False
        self._win.Close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def show(self):
        self._win.ShowDialog()

        if not self._confirmed:
            return False, self._settings

        updated = dict(self._settings)

        # Project information
        if self._reg_title_box is not None:
            updated['register_title'] = self._reg_title_box.Text.strip()
        if self._reg_date_box is not None:
            updated['register_issue_date'] = self._reg_date_box.Text.strip()
        if self._reg_rev_box is not None:
            updated['register_revision'] = self._reg_rev_box.Text.strip()

        # Excluded packages
        if self._excluded_lb is not None:
            updated['excluded_packages'] = [
                self._excluded_lb.Items[i]
                for i in range(self._excluded_lb.Items.Count)
            ]

        # Recipients
        updated_recipients = [
            {'name': nb.Text.strip(), 'row': self._recipients[i].get('row', i + 4)}
            for i, nb in enumerate(self._name_boxes)
        ]
        updated['recipients'] = updated_recipients

        # Distribution codes
        saved_issues = dict(updated.get('issues', {}))
        for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
            key = '{}||{}'.format(date_str, issued_by)
            if key not in saved_issues:
                saved_issues[key] = {}
            for row_idx, recipient in enumerate(updated_recipients):
                cell = self._code_boxes.get((row_idx, col_idx))
                if cell is not None:
                    saved_issues[key][recipient['name']] = cell.Text.strip()
        updated['issues'] = saved_issues

        return True, updated
