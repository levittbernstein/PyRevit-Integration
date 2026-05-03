# -*- coding: utf-8 -*-
"""
WPF settings dialog for the LB Issue Register exporter.

Shows:
  • Title block fields (subject, drawn by, checked by)
  • Distribution matrix — one column per issue date, one row per recipient,
    cells contain format codes E / U / T / X (editable TextBox)

The dialog returns a (confirmed, settings_dict) tuple.
"""

import os
import sys

import clr  # noqa: F401 — pyRevit provides clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('System.Xaml')

from System.Windows import Window  # noqa: E402
from System.Windows.Controls import (  # noqa: E402
    TextBox, TextBlock, StackPanel, ScrollViewer, Border,
    ItemsControl, Grid, GridLength, ColumnDefinition, RowDefinition,
)
from System.Windows import (  # noqa: E402
    Thickness, HorizontalAlignment, VerticalAlignment
)
from System.Windows.Media import Brushes  # noqa: E402
import System.Windows.Markup as Markup  # noqa: E402


def _load_xaml(path):
    with open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


class ExportDialog(object):
    """
    Wrapper around the XAML-defined WPF window.

    Parameters
    ----------
    issue_keys  : list of (date_str, issued_by) tuples — the unique issue columns
    settings    : dict loaded from extensible storage (or defaults)
    """

    def __init__(self, issue_keys, settings):
        self._issue_keys = issue_keys  # [(date_str, issued_by), ...]
        self._settings   = settings
        self._confirmed  = False

        xaml_path = os.path.join(os.path.dirname(__file__), 'dialog.xaml')
        self._win = _load_xaml(xaml_path)

        # Wire up code-behind handlers
        self._win.FindName('SubjectBox').Text    = settings['title_block'].get('subject', '')
        self._win.FindName('DrawnByBox').Text    = settings['title_block'].get('drawn_by', '')
        self._win.FindName('CheckedByBox').Text  = settings['title_block'].get('checked_by', '')

        self._recipients = [dict(r) for r in settings.get('recipients', [])]
        self._build_distribution_grid()

        # Event handlers
        self._win.FindName('OnAddRecipient')    # resolved at runtime via Click attribute
        self._win.AddHandler(
            Window.LoadedEvent,
            System.EventHandler(self._on_loaded)
        )

        # Button events wired via x:Name look-up because XAML Click= refers to
        # code-behind methods — we re-wire them here programmatically.
        self._win.FindName('OnExport')   # placeholder — actual wiring below:
        self._rewire_buttons()

    def _rewire_buttons(self):
        import System  # noqa: F401
        from System.Windows import RoutedEventHandler  # noqa: F401

        # Find buttons by searching the visual tree
        export_btn  = self._find_button('Export Register')
        cancel_btn  = self._find_button('Cancel')
        add_btn     = self._find_button('+ Add recipient')
        remove_btn  = self._find_button('− Remove last')

        if export_btn:
            export_btn.Click  += self._on_export
        if cancel_btn:
            cancel_btn.Click  += self._on_cancel
        if add_btn:
            add_btn.Click     += self._on_add_recipient
        if remove_btn:
            remove_btn.Click  += self._on_remove_recipient

    def _find_button(self, content_text):
        """Walk the logical tree to find a Button with matching Content."""
        from System.Windows.Controls import Button  # noqa: F401
        from System.Windows import LogicalTreeHelper  # noqa: F401

        for child in LogicalTreeHelper.GetChildren(self._win):
            result = self._find_button_recursive(child, content_text)
            if result:
                return result
        return None

    def _find_button_recursive(self, element, content_text):
        from System.Windows.Controls import Button  # noqa: F401
        from System.Windows import LogicalTreeHelper  # noqa: F401

        if isinstance(element, Button) and str(element.Content) == content_text:
            return element
        for child in LogicalTreeHelper.GetChildren(element):
            result = self._find_button_recursive(child, content_text)
            if result:
                return result
        return None

    def _on_loaded(self, sender, e):
        pass

    # ------------------------------------------------------------------
    # Distribution grid
    # ------------------------------------------------------------------

    def _build_distribution_grid(self):
        """
        Build the distribution matrix UI dynamically:
          • Left panel  → recipient name TextBoxes  (RecipientLabels ItemsControl)
          • Right panel → scrollable grid of code TextBoxes

        The _code_boxes dict maps (row_idx, col_idx) → TextBox so we can
        read values back when the user clicks Export.
        """
        from System.Windows.Controls import (  # noqa: F401
            Button, Label
        )
        from System.Windows import GridLength, HorizontalAlignment  # noqa: F401

        self._code_boxes = {}   # (row_idx, col_idx) → TextBox

        labels_ic = self._win.FindName('RecipientLabels')
        dist_ic   = self._win.FindName('DistributionGrid')

        if labels_ic is None or dist_ic is None:
            return

        labels_ic.Items.Clear()
        dist_ic.Items.Clear()

        # Build a WPF Grid for the distribution matrix
        grid = Grid()

        # Column definitions: one per issue
        for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
            cd = ColumnDefinition()
            cd.Width = GridLength(68)
            grid.ColumnDefinitions.Add(cd)

        # Row 0: date header
        rd_header = RowDefinition()
        rd_header.Height = GridLength(44)
        grid.RowDefinitions.Add(rd_header)

        # Rows 1…N: recipient rows
        for _ in self._recipients:
            rd = RowDefinition()
            rd.Height = GridLength(27)
            grid.RowDefinitions.Add(rd)

        # Header cells
        for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
            display = self._format_date_for_header(date_str)
            if issued_by:
                display += '\n' + issued_by

            tb = TextBlock()
            tb.Text              = display
            tb.TextWrapping      = System.Windows.TextWrapping.Wrap
            tb.FontSize          = 9
            tb.TextAlignment     = System.Windows.TextAlignment.Center
            tb.VerticalAlignment = VerticalAlignment.Center
            tb.Margin            = Thickness(1)
            tb.Background        = Brushes.Black
            tb.Foreground        = Brushes.White
            tb.Padding           = Thickness(2)

            Grid.SetRow(tb, 0)
            Grid.SetColumn(tb, col_idx)
            grid.Children.Add(tb)

        # Code cells
        saved_issues = self._settings.get('issues', {})

        for row_idx, recipient in enumerate(self._recipients):
            r_name = recipient.get('name', '')

            # Left-panel label TextBox
            name_box = TextBox()
            name_box.Text = r_name
            name_box.Height = 26
            name_box.Margin = Thickness(0, 1, 2, 1)
            name_box.Padding = Thickness(4, 2, 4, 2)
            name_box.BorderBrush = Brushes.LightGray
            name_box.FontSize = 11
            name_box.Tag = row_idx   # store index for retrieval
            labels_ic.Items.Add(name_box)

            for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
                key = '{}||{}'.format(date_str, issued_by)
                saved_code = saved_issues.get(key, {}).get(r_name, '')

                cell = TextBox()
                cell.Text                     = saved_code
                cell.Height                   = 25
                cell.Margin                   = Thickness(1)
                cell.Padding                  = Thickness(2, 0, 2, 0)
                cell.BorderBrush              = Brushes.LightGray
                cell.FontSize                 = 10
                cell.TextAlignment            = System.Windows.TextAlignment.Center
                cell.VerticalContentAlignment = VerticalAlignment.Center
                cell.MaxLength                = 3

                Grid.SetRow(cell, row_idx + 1)
                Grid.SetColumn(cell, col_idx)
                grid.Children.Add(cell)

                self._code_boxes[(row_idx, col_idx)] = cell

        dist_ic.Items.Add(grid)

    @staticmethod
    def _format_date_for_header(date_str):
        """Format a Revit date string for the column header."""
        for fmt in ('%d/%m/%y', '%d/%m/%Y', '%d.%m.%y', '%d.%m.%Y'):
            try:
                from datetime import datetime  # noqa: F401
                dt = datetime.strptime(date_str, fmt)
                return dt.strftime('%d/%m/%y')
            except (ValueError, ImportError):
                pass
        return date_str

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_add_recipient(self, sender, e):
        self._recipients.append({'name': 'New recipient', 'row': len(self._recipients) + 4})
        self._build_distribution_grid()

    def _on_remove_recipient(self, sender, e):
        if len(self._recipients) > 1:
            self._recipients.pop()
            self._build_distribution_grid()

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
        """
        Display the dialog modally.

        Returns
        -------
        (confirmed: bool, settings: dict)
            confirmed is True only when the user clicked Export.
        """
        self._win.ShowDialog()

        if not self._confirmed:
            return False, self._settings

        # Collect current values back into settings
        updated = dict(self._settings)
        updated['title_block'] = {
            'subject':     self._win.FindName('SubjectBox').Text.strip(),
            'drawn_by':    self._win.FindName('DrawnByBox').Text.strip(),
            'checked_by':  self._win.FindName('CheckedByBox').Text.strip(),
        }

        # Read recipient names from left-panel label boxes
        labels_ic = self._win.FindName('RecipientLabels')
        updated_recipients = []
        for i, item in enumerate(labels_ic.Items):
            if isinstance(item, TextBox):
                updated_recipients.append({
                    'name': item.Text.strip(),
                    'row':  self._recipients[i].get('row', i + 4),
                })
        updated['recipients'] = updated_recipients

        # Read code cells
        saved_issues = dict(updated.get('issues', {}))
        for col_idx, (date_str, issued_by) in enumerate(self._issue_keys):
            key = '{}||{}'.format(date_str, issued_by)
            if key not in saved_issues:
                saved_issues[key] = {}
            for row_idx, recipient in enumerate(updated_recipients):
                r_name = recipient['name']
                cell = self._code_boxes.get((row_idx, col_idx))
                if cell is not None:
                    saved_issues[key][r_name] = cell.Text.strip()
        updated['issues'] = saved_issues

        return True, updated
