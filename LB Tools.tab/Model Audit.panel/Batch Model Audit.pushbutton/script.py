# -*- coding: utf-8 -*-
"""LB - Batch Model Audit: opens multiple Revit files and extracts quality metrics to CSV."""

import os
import sys

# ── Resolve lib path ──────────────────────────────────────────────────────────
_EXT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_LIB = os.path.join(_EXT_ROOT, 'lib', 'model_audit')
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

# ── Revit / pyRevit imports ───────────────────────────────────────────────────
from pyrevit import forms
from Autodesk.Revit.DB import (
    DetachFromCentralOption,
    ModelPathUtils,
    OpenOptions,
    WorksetConfiguration,
    WorksetConfigurationOption,
)

from revit_reader import extract_metrics
from csv_writer import write_csv

# ── Revit application ─────────────────────────────────────────────────────────
app = __revit__.Application  # noqa: F821  (pyRevit injects __revit__)

# ── Select files ──────────────────────────────────────────────────────────────
rvt_files = forms.pick_file(
    file_ext='rvt',
    multi_file=True,
    title='LB - Select Revit Models to Audit',
)
if not rvt_files:
    sys.exit()

# ── Select output path ────────────────────────────────────────────────────────
output_path = forms.save_file(
    file_ext='csv',
    title='LB - Save Audit Results',
    default_name='LB_Model_Audit',
)
if not output_path:
    sys.exit()


# ── Open options factory ──────────────────────────────────────────────────────
def _make_open_options(detach=True):
    opts = OpenOptions()
    if detach:
        opts.DetachFromCentralOption = (
            DetachFromCentralOption.DetachAndPreserveWorksets
        )
        opts.SetOpenWorksetsConfiguration(
            WorksetConfiguration(WorksetConfigurationOption.CloseAllWorksets)
        )
    return opts


# ── Process each model ────────────────────────────────────────────────────────
results = []
errors = []

with forms.ProgressBar(title='LB - Batch Model Audit', cancellable=True) as pb:
    for idx, fpath in enumerate(rvt_files):
        if pb.cancelled:
            break

        pb.update_progress(idx, len(rvt_files))
        pb.title = 'LB - Auditing {} of {}: {}'.format(
            idx + 1, len(rvt_files), os.path.basename(fpath)
        )

        doc = None
        try:
            model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(fpath)

            # Try detached first (workshared models); fall back for non-workshared
            try:
                doc = app.OpenDocumentFile(model_path, _make_open_options(detach=True))
            except Exception:
                doc = app.OpenDocumentFile(model_path, _make_open_options(detach=False))

            metrics = extract_metrics(doc, fpath)
            results.append(metrics)

        except Exception as ex:
            errors.append({'File Name': os.path.basename(fpath), 'error': str(ex)})
        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass

# ── Write CSV ─────────────────────────────────────────────────────────────────
if results:
    write_csv(results, output_path)

# ── Summary dialog ────────────────────────────────────────────────────────────
summary = 'Audit complete.\n{} model(s) processed successfully.'.format(len(results))
if errors:
    summary += '\n\n{} model(s) failed:\n'.format(len(errors))
    summary += '\n'.join(
        '  • {}: {}'.format(e['File Name'], e['error']) for e in errors
    )

forms.alert(summary, title='LB - Batch Model Audit')
