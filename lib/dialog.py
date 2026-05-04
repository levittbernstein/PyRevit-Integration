# -*- coding: utf-8 -*-
"""
WPF settings dialog for the LB Issue Register exporter.
Runs under IronPython inside Revit.
"""

import os

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
    with open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


class ExportDialog(object):

    def __init__(self, issue_keys, settings):
        self._issue_keys = issue_keys
        self._settings   = settings
        self._confirmed  = False
        self._name_boxes = []
        self._code_boxes = {}

        xaml_path = os.path.join(os.path.dirname(__file__), 'dialog.xaml')
        self._win = _load_xaml(xaml_path)

        self._win.FindName('SubjectBox').Text    = settings['title_block'].get('subject',     '')
        self._win.FindName('DrawnByBox').Text    = settings['title_block'].get('drawn_by',    '')
        self._win.FindName('CheckedByBox').Text  = settings['title_block'].get('checked_by',  '')

        self._recipients = [dict(r) for r in settings.get('recipients', [])]
        self._build_grid()
        self._wire_buttons()

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

        if '+ Add recipient'  in _buttons: _buttons['+ Add recipient'].Click  += self._on_add
        if '− Remove last'    in _buttons: _buttons['− Remove last'].Click    += self._on_remove
        if 'Export Register'  in _buttons: _buttons['Export Register'].Click  += self._on_export
        if 'Cancel'           in _buttons: _buttons['Cancel'].Click           += self._on_cancel

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
        for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
            label = self._fmt_date(date_str)
            if issued_by:
                label += '\n' + issued_by
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
                Grid.SetRow(cell, row_idx + 1)
                Grid.SetColumn(cell, col_idx + 1)
                container.Children.Add(cell)
                self._code_boxes[(row_idx, col_idx)] = cell

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
        if self._recipients:
            self._recipients.pop()
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
        updated['title_block'] = {
            'subject':    self._win.FindName('SubjectBox').Text.strip(),
            'drawn_by':   self._win.FindName('DrawnByBox').Text.strip(),
            'checked_by': self._win.FindName('CheckedByBox').Text.strip(),
        }

        updated_recipients = [
            {'name': nb.Text.strip(), 'row': self._recipients[i].get('row', i + 4)}
            for i, nb in enumerate(self._name_boxes)
        ]
        updated['recipients'] = updated_recipients

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
