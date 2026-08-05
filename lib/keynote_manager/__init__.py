# -*- coding: utf-8 -*-
"""
keynote_manager — LB Keynote Manager tool.

Lets users renumber, reorder and categorise Revit keynotes, then sync those
changes to the external keynote .txt file and every model reference.

Module map
----------
keynote_file.py    Parse and write the keynote .txt, preserving its exact
                   encoding (UTF-16 LE + BOM in LB projects), line endings
                   and trailing-newline state.  Handles backups.
keynote_reader.py  Resolve the keynote file path via the Revit KeynoteTable
                   API and snapshot every model reference to a keynote key.
renumber.py        The category/prefix model and old->new key computation.
sync.py            Pre-flight checks, then the atomic apply: write file,
                   reload the table, rewrite references, verify, audit.
dialog.py          WPF front end (loads dialog.xaml).
"""
