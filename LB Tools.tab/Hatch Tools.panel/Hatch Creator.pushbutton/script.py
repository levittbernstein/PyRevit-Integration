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

# ── Show diagnostic paths before doing anything ───────────────────────────────
_info = [
    'CPython:  ' + _cpython,
    'exists:   ' + str(os.path.exists(_cpython)),
    '',
    'Launcher: ' + _launcher,
    'exists:   ' + str(os.path.exists(_launcher)),
    '',
    'EXT_ROOT: ' + _EXT_ROOT,
    'EXT_LIB:  ' + _EXT_LIB,
]
forms.alert('\n'.join(_info), title='Hatch Creator — diagnostics')

# ── Run launcher blocking so we capture all output ───────────────────────────
_proc = subprocess.Popen(
    [_cpython, _launcher],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
_out, _err = _proc.communicate()
_out = _out.decode('utf-8', errors='replace').strip()
_err = _err.decode('utf-8', errors='replace').strip()

if _proc.returncode != 0 or _err:
    forms.alert(
        'Exit code: {}\n\nSTDOUT:\n{}\n\nSTDERR:\n{}'.format(
            _proc.returncode, _out or '(none)', _err or '(none)'),
        title='Hatch Creator error', warn_icon=True)
else:
    forms.alert('Process exited cleanly (code 0) — app closed normally.',
                title='Hatch Creator')
