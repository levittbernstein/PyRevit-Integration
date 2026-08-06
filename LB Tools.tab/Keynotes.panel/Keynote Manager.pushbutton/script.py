# -*- coding: utf-8 -*-
"""
LB Keynote Manager — pyRevit push-button script.

Renumber, reorder and categorise keynotes, then sync the changes to the
external keynote .txt file and every reference in the model.

Runs entirely under IronPython — no CPython subprocess needed, since all the
work is Revit API plus plain text file handling.

IMPORTANT — see README "Developer notes": never call sys.exit() after a
committed Transaction. pyRevit treats SystemExit as a signal to roll back
transactions from this script run, which would silently undo the keynote
update. All post-update paths below fall through to the end of the script.
"""

import os
import sys
import traceback

from pyrevit import forms

# ── Make the extension lib folder importable ──────────────────────────────────
# pyRevit normally adds lib/ automatically; doing it explicitly costs nothing
# and protects against path differences on a freshly added panel.
_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from keynote_manager import keynote_file as kfile      # noqa: E402
from keynote_manager import keynote_reader as kreader  # noqa: E402
from keynote_manager import settings as ksettings      # noqa: E402
from keynote_manager import sync as ksync              # noqa: E402
from keynote_manager.renumber import KeynoteModel      # noqa: E402
from keynote_manager.dialog import KeynoteDialog       # noqa: E402


# ── Document ──────────────────────────────────────────────────────────────────
doc = __revit__.ActiveUIDocument.Document  # noqa: F821

if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project file first.',
                title='No project open', warn_icon=True)

else:
    # ── Locate the keynote file ───────────────────────────────────────────────
    try:
        path, kind = kreader.get_keynote_file_path(doc)
    except Exception:
        path, kind = None, None
        forms.alert('Could not read the keynote table from this model.\n\n'
                    + traceback.format_exc(),
                    title='Keynote table error', warn_icon=True)

    if not path:
        forms.alert(
            'This model has no external keynote file assigned.\n\n'
            'Assign one first:  Annotate tab → Keynote → Keynoting Settings.',
            title='No keynote file', warn_icon=True)

    elif not os.path.isfile(path):
        forms.alert(
            'The keynote file assigned to this model cannot be found:\n\n'
            '    {}\n\n'
            'Check Keynoting Settings, or that the network location is '
            'available.'.format(path),
            title='Keynote file not found', warn_icon=True)

    else:
        # ── Refuse to fight pyRevit's own keynote database ────────────────────
        managed = False
        try:
            managed = kfile.is_pyrevit_managed(path)
        except Exception:
            pass

        if managed:
            forms.alert(
                "This keynote file is managed by pyRevit's own Keynote Manager "
                '— it contains embedded database lines on #-prefixed rows.\n\n'
                'Two tools writing that file would corrupt the database, so LB '
                'Keynote Manager will not open it.\n\n'
                'Use pyRevit\'s Keynotes tool for this file, or export a clean '
                'tab-delimited copy and point the model at that.',
                title='File managed by pyRevit', warn_icon=True)

        else:
            # ── Safety: must survive a read/write round-trip ──────────────────
            ok, detail = kfile.test_roundtrip(path)
            if not ok:
                forms.alert(
                    'Safety check failed — this keynote file does not survive '
                    'a read/write round-trip unchanged, so rewriting it could '
                    'lose data.\n\n{}\n\nNothing has been changed.'.format(detail),
                    title='Unsafe to edit', warn_icon=True)

            else:
                # ── Read file + snapshot the model ───────────────────────────
                entries, meta, problems = kfile.read_keynote_file(path)

                if not entries:
                    forms.alert(
                        'The keynote file contains no keynote entries:\n\n'
                        '    {}'.format(path),
                        title='Empty keynote file', warn_icon=True)

                else:
                    with forms.ProgressBar(title='Scanning model for keynote '
                                                 'references...') as pb:
                        pb.update_progress(1, 3)
                        refs_by_key, ref_stats = kreader.snapshot_references(doc)
                        pb.update_progress(3, 3)

                    model = KeynoteModel(entries)

                    if problems:
                        forms.alert(
                            'The keynote file parsed with warnings:\n\n'
                            + '\n'.join(problems[:10]),
                            title='Keynote file warnings', warn_icon=True)

                    # Orphaned references are worth surfacing up front — they
                    # are pre-existing breakage, not something we caused.
                    valid = set(e.key for e in entries)
                    orphans = kreader.find_orphans(refs_by_key, valid)
                    if orphans:
                        forms.alert(
                            '{} keynote key(s) are used in this model but do '
                            'not exist in the keynote file:\n\n    {}\n\n'
                            'These are already broken and this tool cannot '
                            'repair them automatically — they are listed so '
                            'you know they exist.'.format(
                                len(orphans), ', '.join(orphans[:25])),
                            title='Orphaned keynote references', warn_icon=True)

                    # ── Dialog ───────────────────────────────────────────────
                    dlg = KeynoteDialog(model, meta, path, refs_by_key,
                                        ref_stats,
                                        settings_key=ksettings.project_key(doc))
                    action, state = dlg.show()

                    if action == 'update' and state['key_map']:
                        pf = ksync.preflight(doc, path, state['model'],
                                             state['key_map'])

                        if not pf.ok:
                            forms.alert(
                                'The keynote update cannot proceed:\n\n'
                                + '\n\n'.join(pf.blockers)
                                + '\n\nNothing has been changed.',
                                title='Cannot update', warn_icon=True)

                        else:
                            n_changes = len(state['key_map'])
                            n_refs = sum(len(refs_by_key.get(k, []))
                                         for k in state['key_map'])

                            msg = ('About to renumber {} keynote key(s) and '
                                   'update {} reference(s) in this model.\n\n'
                                   'The keynote file will be backed up to the '
                                   '{} folder first.'.format(
                                       n_changes, n_refs, kfile.BACKUP_DIRNAME))
                            if pf.warnings:
                                msg += '\n\n' + '\n\n'.join(pf.warnings)

                            if forms.alert(msg, title='Confirm keynote update',
                                           ok=False, yes=True, no=True,
                                           warn_icon=True):

                                report = ksync.apply_changes(
                                    doc, path, state['model'],
                                    state['key_map'], refs_by_key,
                                    state['meta'])

                                # ── Result ───────────────────────────────────
                                if report['error']:
                                    forms.alert(
                                        'Keynote update failed:\n\n{}'.format(
                                            report['error']),
                                        title='Update failed', warn_icon=True)
                                else:
                                    lines = [
                                        'Keynote update complete.',
                                        '',
                                        'Keys changed:   {}'.format(n_changes),
                                        'Tags updated:      {}'.format(
                                            report['updated']['tag']),
                                        'Types updated:     {}'.format(
                                            report['updated']['type']),
                                        'Materials updated: {}'.format(
                                            report['updated']['material']),
                                    ]
                                    if report['skipped']:
                                        lines += [
                                            '',
                                            '{} reference(s) could not be '
                                            'updated — see the audit log.'
                                            .format(len(report['skipped'])),
                                        ]
                                    if report['orphans']:
                                        lines += [
                                            '',
                                            'WARNING: {} reference(s) still '
                                            'point at keys that do not exist: '
                                            '{}'.format(
                                                len(report['orphans']),
                                                ', '.join(report['orphans'][:15])),
                                        ]
                                    if report['backup']:
                                        lines += ['', 'Backup: {}'.format(
                                            report['backup'])]
                                    if report['audit']:
                                        lines += ['Audit log: {}'.format(
                                            report['audit'])]

                                    lines += [
                                        '',
                                        'If the Keynote browser still shows old '
                                        'numbers, close and reopen it — Revit '
                                        'caches the keynote table.',
                                    ]

                                    forms.alert('\n'.join(lines),
                                                title='LB Keynote Manager')

# No sys.exit() anywhere after this point — see the module docstring.
