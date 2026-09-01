# -*- coding: utf-8 -*-
"""
WPF level -> code editor.

Same runtime-XAML pattern as the other LB dialogs: dialog.xaml parsed with
XamlReader, the window held on self._win (not subclassed), rows built in code
and added to the RowContainer grid, controls wired with .NET '+='.

Purely an editor — it performs no Revit writes. show() hands the edited codes
back to script.py, which owns the transactions.
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
    GridLength, GridUnitType, Thickness, VerticalAlignment, FontWeights,
)
from System.Windows.Controls import (
    TextBlock, TextBox, Grid, ColumnDefinition, RowDefinition,
)


def _load_xaml(path):
    with io.open(path, 'r', encoding='utf-8') as fh:
        return Markup.XamlReader.Parse(fh.read())


class LevelCodeDialog(object):
    """levels: list of (uniqueid, name, initial_code).

    show() returns (ok, {uniqueid: code}) where ok is False on Cancel/close.
    """

    def __init__(self, levels):
        self._levels = levels
        self._boxes = {}     # uniqueid -> TextBox
        self._ok = False

        self._win = _load_xaml(
            os.path.join(os.path.dirname(__file__), 'dialog.xaml'))
        self._container = self._win.FindName('RowContainer')
        self._build_rows()
        self._wire()
        self._status('{} level(s). Edit the codes, then press Run.'
                     .format(len(levels)))

    # ── Build ─────────────────────────────────────────────────────────────
    def _build_rows(self):
        c = self._container
        c.Children.Clear()
        c.RowDefinitions.Clear()
        c.ColumnDefinitions.Clear()

        col_name = ColumnDefinition()
        col_name.Width = GridLength(1, GridUnitType.Star)
        col_code = ColumnDefinition()
        col_code.Width = GridLength(120)
        c.ColumnDefinitions.Add(col_name)
        c.ColumnDefinitions.Add(col_code)

        def add_row():
            rd = RowDefinition()
            rd.Height = GridLength.Auto
            c.RowDefinitions.Add(rd)
            return c.RowDefinitions.Count - 1

        r = add_row()
        c.Children.Add(self._cell('Level', r, 0, bold=True))
        c.Children.Add(self._cell('Code', r, 1, bold=True))

        for uid, name, code in self._levels:
            r = add_row()
            c.Children.Add(self._cell(name, r, 0))

            box = TextBox()
            box.Text = code or ''
            box.Height = 26
            box.MaxLength = 4
            box.Margin = Thickness(0, 3, 4, 3)
            box.VerticalContentAlignment = VerticalAlignment.Center
            Grid.SetRow(box, r)
            Grid.SetColumn(box, 1)
            c.Children.Add(box)
            self._boxes[uid] = box

    def _cell(self, text, r, col, bold=False):
        tb = TextBlock()
        tb.Text = text or ''
        tb.Margin = Thickness(4, 5, 8, 5)
        tb.VerticalAlignment = VerticalAlignment.Center
        if bold:
            tb.FontWeight = FontWeights.SemiBold
        Grid.SetRow(tb, r)
        Grid.SetColumn(tb, col)
        return tb

    # ── Wiring ────────────────────────────────────────────────────────────
    def _wire(self):
        run = self._win.FindName('RunBtn')
        cancel = self._win.FindName('CancelBtn')
        if run is not None:
            run.Click += self._on_run
        if cancel is not None:
            cancel.Click += self._on_cancel

    def _status(self, text):
        lbl = self._win.FindName('StatusText')
        if lbl is not None:
            lbl.Text = text or ''

    def _on_run(self, sender, e):
        self._ok = True
        self._win.Close()

    def _on_cancel(self, sender, e):
        self._ok = False
        self._win.Close()

    # ── Public ────────────────────────────────────────────────────────────
    def show(self):
        self._win.ShowDialog()
        if not self._ok:
            return (False, {})
        result = {}
        for uid, box in self._boxes.items():
            result[uid] = (box.Text or '').strip()
        return (True, result)
