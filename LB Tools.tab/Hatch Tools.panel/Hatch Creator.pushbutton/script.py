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

# ── Find a Python with tkinter ────────────────────────────────────────────────
def _has_tkinter(python_exe):
    try:
        r = subprocess.Popen(
            [python_exe, '-c', 'import tkinter'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        r.communicate()
        return r.returncode == 0
    except Exception:
        return False

def _find_python_with_tkinter():
    localappdata = os.environ.get('LOCALAPPDATA', '')
    appdata      = os.environ.get('APPDATA', '')

    # Prefer a full system Python install over pyRevit's minimal embed
    candidates = []

    # Standard user installs
    candidates.append(os.path.join(localappdata, 'Python', 'bin', 'python.exe'))
    for pat in [os.path.join(localappdata, 'Programs', 'Python', 'Python3*', 'python.exe'),
                os.path.join(localappdata, 'Programs', 'Python', 'Python*', 'python.exe')]:
        candidates.extend(glob.glob(pat))

    # Conda
    candidates.append(os.path.join(localappdata, 'miniconda3', 'python.exe'))
    candidates.append(os.path.join(localappdata, 'anaconda3', 'python.exe'))
    candidates.append(os.path.join(os.path.expanduser('~'), 'miniconda3', 'python.exe'))
    candidates.append(os.path.join(os.path.expanduser('~'), 'anaconda3', 'python.exe'))

    # System-wide
    candidates.extend(glob.glob(r'C:\Python3*\python.exe'))
    candidates.extend(glob.glob(r'C:\Program Files\Python3*\python.exe'))

    # pyRevit bundled CPython (last resort — often missing tkinter)
    for root in ['pyRevit-Master', 'pyRevit']:
        candidates.extend(glob.glob(
            os.path.join(appdata, root, 'bin', 'cengines', 'CPY*', 'python.exe')))

    for exe in candidates:
        if os.path.exists(exe) and _has_tkinter(exe):
            return exe
    return None

_cpython = _find_python_with_tkinter()
if not _cpython:
    forms.alert(
        'Cannot find a Python installation with tkinter.\n\n'
        'Please install Python from python.org (make sure to include tcl/tk).',
        title='Python not found', warn_icon=True)
    sys.exit(0)

# ── Launch the Hatch Creator as a fully detached subprocess ──────────────────
# DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP ensures the child process is
# fully detached from pyRevit's process tree on Windows — without this the
# child is killed when the IronPython script host exits, causing the window
# to flash and disappear.
_launcher = os.path.join(_EXT_LIB, 'launcher.py')

# ── Launch as a detached process so it outlives the pyRevit script host ───────
subprocess.Popen([_cpython, _launcher], creationflags=0x00000008 | 0x00000200)
