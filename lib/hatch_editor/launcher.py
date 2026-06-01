"""
CPython entry point for the Hatch Creator tool.

Launched as a subprocess by the pyRevit pushbutton script so that tkinter
runs in standard CPython rather than inside Revit's IronPython engine.
"""
import sys
import os
import traceback

_LOG = os.path.join(os.environ.get('TEMP', os.path.expanduser('~')), 'hatch_creator_error.log')

# Ensure the lib/ directory is on the path so hatch_editor is importable as a package
_lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _lib not in sys.path:
    sys.path.insert(0, _lib)

try:
    from hatch_editor.main import run
    run()
except Exception:
    err = traceback.format_exc()
    with open(_LOG, 'w') as f:
        f.write('Python: ' + sys.executable + '\n')
        f.write('sys.path: ' + str(sys.path) + '\n\n')
        f.write(err)
    sys.exit(1)
