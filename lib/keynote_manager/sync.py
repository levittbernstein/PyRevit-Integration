# -*- coding: utf-8 -*-
"""
Pre-flight checks and the atomic apply.

The apply has to keep two stores in step: an external .txt file and a set of
Revit parameters.  Only one of them is transactional, so the ordering is
deliberate:

    1. snapshot every reference          (before anything changes)
    2. back up the .txt
    3. write the new .txt
    4. open ONE transaction:
         a. KeynoteTable.Reload()        so the new keys exist in the table
         b. rewrite every reference      to its new key
       commit
    5. verify, then write an audit log

If step 4 throws, the transaction rolls the model back but the file on disk is
already new — so the file is restored from the backup made in step 2.  Without
that restore a failed run would leave the file renumbered and the model not,
which is the worst possible outcome and exactly what people report from other
keynote tools.

Reload must run inside a transaction or it throws
ModificationOutsideTransactionException.
"""

import datetime
import os
import shutil

from keynote_manager import keynote_file as kfile
from keynote_manager import keynote_reader as kreader


# ── Pre-flight ────────────────────────────────────────────────────────────────

class Preflight(object):
    def __init__(self):
        self.blockers = []   # must be fixed before Update can run
        self.warnings = []   # user should know, but can proceed

    @property
    def ok(self):
        return not self.blockers


def preflight(doc, path, model, key_map):
    """Everything that could make an Update unsafe, checked before we touch anything."""
    pf = Preflight()

    if not path:
        pf.blockers.append(
            'This model has no external keynote file assigned.\n'
            'Set one in Annotate > Keynote > Keynoting Settings first.')
        return pf

    if not os.path.isfile(path):
        pf.blockers.append(
            'The keynote file cannot be found:\n    {}\n'
            'Check the path in Keynoting Settings, or that the network '
            'location is available.'.format(path))
        return pf

    if not kfile.is_writable(path):
        pf.blockers.append(
            'The keynote file is not writable:\n    {}\n'
            'It may be read-only, open in another program, or on a share you '
            'lack permission to write. Nothing has been changed.'.format(path))

    try:
        if kfile.is_pyrevit_managed(path):
            pf.blockers.append(
                "This keynote file is managed by pyRevit's own Keynote Manager "
                '(it contains embedded database lines).\n'
                'Both tools writing the same file would corrupt that database. '
                'Use pyRevit\'s manager for this file, or export a clean copy '
                'first.')
    except Exception as exc:
        pf.warnings.append('Could not check for pyRevit database lines: {}'.format(exc))

    ok, detail = kfile.test_roundtrip(path)
    if not ok:
        pf.blockers.append(
            'Safety check failed — this file does not survive a read/write '
            'round-trip unchanged, so rewriting it could lose data.\n    {}'
            .format(detail))

    # Does the file on disk still match what Revit has loaded?  If someone
    # edited it since the model was opened, our snapshot of "current" keys is
    # wrong and the renumber would be computed against stale data.
    try:
        table = kreader.read_entries_from_table(doc)
        if table:
            file_keys  = set(e.key for e in model.all_entries())
            file_keys |= set(c.key for c in model.categories)
            table_keys = set(table.keys())
            missing = table_keys - file_keys
            added   = file_keys - table_keys
            if missing or added:
                pf.warnings.append(
                    'The keynote file on disk differs from the table Revit has '
                    'loaded ({} only in Revit, {} only in the file).\n'
                    'Reload the keynote file in Revit before updating, or the '
                    'result may be inconsistent.'.format(len(missing), len(added)))
    except Exception:
        pass

    problems = model.validate_new_keys(key_map)
    pf.blockers.extend(problems)

    if doc.IsWorkshared:
        pf.warnings.append(
            'This is a workshared model. Element types and materials must be '
            'editable by you — anything owned by another user will be reported '
            'as skipped rather than silently missed.')

    if kreader.has_linked_models(doc):
        pf.warnings.append(
            'This model contains Revit links. Keynote tags inside linked models '
            'store their own key strings and CANNOT be updated from here.\n'
            'The audit log written alongside the keynote file lists every key '
            'change so the same update can be run in those models.')

    return pf


# ── Dry run ───────────────────────────────────────────────────────────────────

def plan(model, refs_by_key, key_map):
    """
    Build a human-readable preview of exactly what Update would do.

    Nothing is written.  This is what the dialog shows before committing.
    """
    rows = []
    totals = {'tag': 0, 'type': 0, 'material': 0}

    for old, new in sorted(key_map.items(), key=lambda kv: _sortable(kv[1])):
        refs   = refs_by_key.get(old, [])
        counts = {'tag': 0, 'type': 0, 'material': 0}
        for r in refs:
            counts[r.kind] += 1
            totals[r.kind] += 1

        entry = model.entry_by_key(old)
        merged_into = model.merged.get(old)

        rows.append({
            'old':      old,
            'new':      new,
            'text':     entry.text if entry is not None else u'(merged / removed)',
            'tags':     counts['tag'],
            'types':    counts['type'],
            'materials': counts['material'],
            'merged':   merged_into is not None,
        })

    unreferenced = [r for r in rows
                    if not (r['tags'] or r['types'] or r['materials'])]

    # New keynotes carry no old key, so report them separately under the key
    # they will actually land on.
    added = []
    for provisional in model.added:
        final = key_map.get(provisional, provisional)
        entry = model.entry_by_key(provisional)
        added.append((final, entry.text if entry is not None else u''))

    return {
        'rows':          rows,
        'totals':        totals,
        'key_changes':   len(key_map),
        'unreferenced':  len(unreferenced),
        'merges':        len(model.merged),
        'added':         added,
    }


# ── Apply ─────────────────────────────────────────────────────────────────────

def _reload_table(doc):
    """
    Reload the keynote table from disk.  Must be inside a transaction.

    Signature is Reload(KeyBasedTreeEntriesLoadResults) with the argument
    optional, but IronPython overload resolution is inconsistent across
    versions, so try the documented form then the bare one.
    """
    kt = kreader.get_keynote_table(doc)
    if kt is None:
        raise RuntimeError('Could not obtain the KeynoteTable for this model.')

    try:
        return kt.Reload(None)
    except TypeError:
        return kt.Reload()


def _set_key(element, kind, new_key):
    """Write *new_key*. Returns (ok, reason)."""
    p = kreader._param_of(element, kind)
    if p is None:
        return False, 'keynote parameter not present'
    if p.IsReadOnly:
        return False, 'parameter is read-only'
    try:
        return (True, None) if p.Set(new_key) else (False, 'Set() refused the value')
    except Exception as exc:
        return False, str(exc)


def apply_changes(doc, path, model, key_map, refs_by_key, meta):
    """
    Commit the renumber to both the file and the model.

    Caller must NOT be inside a transaction — this opens its own.
    Returns a report dict; never raises for per-element failures, which are
    collected and reported instead.
    """
    from Autodesk.Revit.DB import Transaction

    report = {
        'backup':      None,
        'audit':       None,
        'updated':     {'tag': 0, 'type': 0, 'material': 0},
        'skipped':     [],
        'orphans':     [],
        'file_written': False,
        'committed':   False,
        'error':       None,
    }

    # Additions alone are enough to justify a write, even with no key changes.
    if not model.has_changes(key_map):
        report['error'] = 'Nothing to update — no keys changed.'
        return report

    # 1. Back up before anything is written.
    report['backup'] = kfile.backup(path)
    if report['backup'] is None:
        report['error'] = (
            'Could not create a backup of the keynote file, so the update was '
            'not attempted. Check write access to the keynote folder.')
        return report

    new_entries = model.to_entries(key_map)

    # 2. Write the new file.
    try:
        kfile.write_keynote_file(path, new_entries, meta)
        report['file_written'] = True
    except Exception as exc:
        report['error'] = 'Failed to write the keynote file: {}'.format(exc)
        _restore(report['backup'], path, report)
        return report

    # 3. One transaction: reload the table, then repoint every reference.
    t = Transaction(doc, 'LB - Renumber Keynotes')
    try:
        t.Start()
        _reload_table(doc)

        for old_key, new_key in key_map.items():
            for ref in refs_by_key.get(old_key, []):
                element = doc.GetElement(ref.element_id)
                if element is None:
                    report['skipped'].append(
                        (old_key, ref.kind, ref.label, 'element no longer exists'))
                    continue
                ok, reason = _set_key(element, ref.kind, new_key)
                if ok:
                    report['updated'][ref.kind] += 1
                else:
                    report['skipped'].append(
                        (old_key, ref.kind, ref.label, reason))

        t.Commit()
        report['committed'] = True

    except Exception as exc:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        report['error'] = (
            'The model update failed and was rolled back: {}\n'
            'The keynote file has been restored from backup so the file and '
            'model remain consistent.'.format(exc))
        _restore(report['backup'], path, report)
        return report

    # 4. Verify — every remaining reference must resolve to a real key.
    try:
        fresh_refs, _stats = kreader.snapshot_references(doc)
        valid = set(e.key for e in new_entries)
        report['orphans'] = kreader.find_orphans(fresh_refs, valid)
    except Exception:
        pass

    # 5. Audit log, so linked/other models can be brought into line.
    report['audit'] = _write_audit(path, key_map, model, report)

    return report


def _restore(backup_path, path, report):
    """Put the original file back after a failed apply."""
    if not backup_path or not os.path.isfile(backup_path):
        report['error'] = (report.get('error') or '') + (
            '\n\nWARNING: the keynote file could NOT be restored '
            'automatically. Restore it manually from the {} folder.'
            .format(kfile.BACKUP_DIRNAME))
        return
    try:
        shutil.copy2(backup_path, path)
        report['file_written'] = False
    except Exception as exc:
        report['error'] = (report.get('error') or '') + (
            '\n\nWARNING: restoring the keynote file also failed ({}). '
            'Restore it manually from:\n    {}'.format(exc, backup_path))


def _write_audit(path, key_map, model, report):
    """
    Write the old->new key map beside the backups.

    Tags in linked or other models referencing this keynote file cannot be
    fixed from this session; this log is what makes fixing them possible later.
    """
    try:
        folder = os.path.join(os.path.dirname(path), kfile.BACKUP_DIRNAME)
        if not os.path.isdir(folder):
            os.makedirs(folder)

        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        name  = os.path.splitext(os.path.basename(path))[0]
        dest  = os.path.join(folder, '{}_keymap_{}.txt'.format(name, stamp))

        lines = [
            u'LB Keynote Manager - key change audit',
            u'Generated: {}'.format(
                datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            u'Keynote file: {}'.format(path),
            u'',
            u'References updated in this model: {} tags, {} types, {} materials'
            .format(report['updated']['tag'], report['updated']['type'],
                    report['updated']['material']),
            u'',
            u'Tags in LINKED or OTHER models are not updated automatically.',
            u'Apply the mapping below to those models.',
            u'',
            u'OLD KEY\tNEW KEY\tNOTE',
        ]

        added = set(model.added)
        for old, new in sorted(key_map.items(), key=lambda kv: _sortable(kv[1])):
            note = u''
            if old in model.merged:
                note = u'merged into {}'.format(model.merged[old])
            elif old in added:
                note = u'new keynote'
            lines.append(u'{}\t{}\t{}'.format(old, new, note))

        # New keynotes that were never renumbered have no key_map row, so list
        # them separately or they would be missing from the audit entirely.
        new_only = [k for k in model.added if k not in key_map]
        if new_only:
            lines.extend([u'', u'NEW KEYNOTES ADDED:'])
            for key in new_only:
                entry = model.entry_by_key(key)
                lines.append(u'{}\t{}'.format(
                    key, entry.text if entry is not None else u''))

        if report['skipped']:
            lines.extend([u'', u'SKIPPED IN THIS MODEL:'])
            for old, kind, label, reason in report['skipped']:
                lines.append(u'{}\t{}\t{}\t{}'.format(old, kind, label, reason))

        with open(dest, 'wb') as fh:
            fh.write(u'\r\n'.join(lines).encode('utf-8-sig'))
        return dest
    except Exception:
        return None


def _sortable(key):
    """Natural-ish sort so R02 follows R01 and 10 follows 9."""
    import re
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r'(\d+)', key or u'')]
