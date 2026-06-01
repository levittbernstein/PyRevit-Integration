# -*- coding: utf-8 -*-
"""
LB Hatch Creator — pyRevit push-button script.

Opens a standalone Tkinter drawing tool (in a CPython subprocess) for
creating Revit model hatch patterns and exporting them as .pat files.
The tool runs independently of any open Revit document.
"""

import sys
import os
import glob
import subprocess

from pyrevit import forms

# ── Add tool lib folder to path ───────────────────────────────────────────────
_EXT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib', 'hatch_editor')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

# ── Find CPython ──────────────────────────────────────────────────────────────
def _find_cpython():
    appdata = os.environ.get('APPDATA', '')
    search_roots = ['pyRevit-Master', 'pyRevit']
    engine_name  = 'CPY3123'
    for root in search_roots:
        candidate = os.path.join(
            appdata, root, 'bin', 'cengines', engine_name, 'python.exe')
        if os.path.exists(candidate):
            return candidate
    for root in search_roots:
        pattern = os.path.join(appdata, root, 'bin', 'cengines', 'CPY*', 'python.exe')
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None

_cpython = _find_cpython()
if not _cpython:
    forms.alert(
        'Cannot find pyRevit CPython.\n\n'
        'Expected at:\n'
        '  %APPDATA%\\pyRevit-Master\\bin\\cengines\\CPY3123\\python.exe\n'
        '  %APPDATA%\\pyRevit\\bin\\cengines\\CPY3123\\python.exe\n\n'
        'Check your pyRevit installation.',
        title='CPython not found', warn_icon=True)
    sys.exit(0)

# ── Launch the Hatch Creator as a fully detached subprocess ──────────────────
# DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures the child process is
# fully detached from pyRevit's process tree on Windows — without this the
# child is killed when the IronPython script host exits, causing the window
# to flash and disappear.
_launcher = os.path.join(_EXT_LIB, 'launcher.py')

# Diagnostic: verify paths exist before launching
_missing = [p for p in [_cpython, _launcher] if not os.path.exists(p)]
if _missing:
    forms.alert(
        'Hatch Creator could not start — missing files:\n\n' +
        '\n'.join(_missing),
        title='Hatch Creator', warn_icon=True)
    sys.exit(0)

# Launch with stderr captured to a temp log so startup errors are visible.
import tempfile
_log = os.path.join(tempfile.gettempdir(), 'hatch_creator.log')
with open(_log, 'w') as _lf:
    _proc = subprocess.Popen(
        [_cpython, _launcher],
        stderr=_lf,
        stdout=_lf,
    )

# Wait up to 3 seconds — if it exits immediately, show the log.
import time
for _ in range(30):
    time.sleep(0.1)
    if _proc.poll() is not None:
        break

if _proc.poll() is not None:
    try:
        with open(_log, 'r') as _lf:
            _err = _lf.read().strip()
    except Exception:
        _err = '(could not read log)'
    forms.alert(
        'Hatch Creator crashed on startup:\n\n' + (_err or '(no output)') +
        '\n\nLog: ' + _log,
        title='Hatch Creator error', warn_icon=True)
