# -*- coding: utf-8 -*-
"""
Rebuild a group type so a non-varying parameter can be changed on its members.

The sequence, which is the workaround Autodesk's own API forum describes:

    1. Ungroup ONE instance of the group type.
    2. Set the wanted values on the now-loose members. They are ordinary
       elements at this point, so nothing blocks the write.
    3. Regroup them, producing a new group type.
    4. Swap every other instance of the old type onto the new type with
       Element.ChangeTypeId, which keeps them associated to one shared
       definition rather than becoming unique groups.
    5. Delete the now-unused old type and take its name, so the model looks
       unchanged apart from the values.

Preserving per-instance data
---------------------------
Swapping an instance onto a new type replaces its members, which would discard
any parameter that legitimately varies between group instances — room numbers
being the obvious one.  So every varying value is snapshotted per instance
before the rebuild and written back afterwards.

There is a neat symmetry that makes this tractable: the parameters that NEED
restoring are exactly the parameters that CAN be restored.  A non-varying
parameter is by definition identical across instances, so the new definition
already carries it; a varying parameter holds real per-instance data, and is
also the only kind Revit permits writing on a group member.

Matching old members to new ones
-------------------------------
Members are recreated, so ElementIds change.  Old and new are matched on
(category, rounded location point) because the group instance does not move,
which makes position a stable key.  Where a member has no location point the
index within the group is used as a fallback.  If matching is incomplete the
whole operation is rolled back rather than restoring values onto the wrong
elements.

Safety
------
Everything happens inside ONE transaction, and it is rolled back unless the
post-rebuild verification is clean. dry_run=True always rolls back, which makes
it possible to find out exactly what this would do to a real model without
risking it.
"""

from group_params import probe


# Built-in parameters that hold genuine per-instance data on group members even
# though they are not project parameters. Attempted on a best-effort basis and
# reported if refused, rather than assumed.
_SPECIAL_RESTORE = [
    'Number',        # Room / space number — unique per room, must survive
    'Name',          # Room name
    'Mark',
    'Comments',
]

_TOL = 4  # decimal places when rounding a location point into a match key


# ── Which parameters vary ─────────────────────────────────────────────────────

def varying_parameter_names(doc):
    """
    Names of project parameters that are set to vary by group instance.

    These are the ones holding per-instance data that a type swap would destroy.
    """
    names = set()
    try:
        it = doc.ParameterBindings.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            definition = it.Key
            if definition is None:
                continue
            try:
                if definition.GetAllowVaryBetweenGroups(doc):
                    names.add(definition.Name)
            except Exception:
                pass
    except Exception:
        pass
    return names


def restore_parameter_names(doc):
    """Everything worth snapshotting per instance."""
    return sorted(varying_parameter_names(doc) | set(_SPECIAL_RESTORE))


# ── Member identity ───────────────────────────────────────────────────────────

def _match_key(doc, element, index):
    """
    A key that survives the member being recreated.

    Position is used because the group instance stays put, so a member ends up in
    the same place. Index is the fallback for members with no location point.
    """
    cat_id = None
    try:
        if element.Category is not None:
            cat_id = probe.eid_int(element.Category.Id)
    except Exception:
        pass

    try:
        loc = element.Location
        pt = getattr(loc, 'Point', None)
        if pt is not None:
            return (cat_id, round(pt.X, _TOL), round(pt.Y, _TOL),
                    round(pt.Z, _TOL))
    except Exception:
        pass

    return (cat_id, 'index', index)


def _members_of(doc, group):
    out = []
    try:
        for idx, mid in enumerate(group.GetMemberIds()):
            el = doc.GetElement(mid)
            if el is not None:
                out.append((idx, el))
    except Exception:
        pass
    return out


def _values_present(wanted, after, skip=None):
    """
    True when every value in *wanted* still exists somewhere in *after*.

    Used to tell a member that was recreated slightly off its old position — so
    its match key changed — from one whose data is genuinely gone. Without this
    distinction a harmless re-key is indistinguishable from data loss, and the
    run gets rolled back for nothing.
    """
    pool = {}
    for vals in after.values():
        for name, value in vals.items():
            pool.setdefault(name, set()).add(value)

    for name, value in wanted.items():
        if name == skip:
            continue
        if value not in pool.get(name, ()):
            return False
    return True


def _member_at(doc, group, key):
    """The member of *group* whose match key is *key*, or None."""
    for idx, el in _members_of(doc, group):
        if _match_key(doc, el, idx) == key:
            return el
    return None


def snapshot_instance(doc, group, param_names):
    """{match_key: {param_name: value}} for one group instance."""
    data = {}
    for idx, el in _members_of(doc, group):
        key = _match_key(doc, el, idx)
        vals = {}
        for name in param_names:
            p = probe.param_by_name(el, name)
            if p is None or p.IsReadOnly:
                continue
            value = probe.read_value(el, name)
            if value != u'':
                vals[name] = value
        if vals:
            data[key] = vals
    return data


# ── Failure handling ──────────────────────────────────────────────────────────

def _install_capture(transaction):
    """
    Dismiss the warnings a rebuild legitimately raises; roll back on errors.

    Ungrouping and regrouping produce warnings that would otherwise stall the run
    behind a modal dialog. Errors are different: Revit's group error dialog
    offers an "Ungroup" button, and a user pressing it would ungroup their model
    to force the change through. So errors abort the transaction instead of ever
    reaching the screen.

    Returns the capture object so its messages can be reported.
    """
    capture = probe.make_failure_capture(rollback_on_error=True)
    probe._install_capture(transaction, capture)
    return capture


# ── The rebuild ───────────────────────────────────────────────────────────────

class RebuildReport(object):
    def __init__(self):
        self.group_type      = u''
        self.instances       = 0
        self.written         = 0
        self.restored        = 0
        self.restore_failed  = []   # (param, reason)
        self.members_before  = 0
        self.members_after   = 0
        self.preserved       = 0    # per-instance values that survived untouched
        self.rekeyed         = 0    # value present but on a differently-keyed member
        self.lost            = []   # (param, old_value) genuinely gone
        self.problems        = []   # cause a rollback
        self.warnings        = []   # do NOT cause a rollback
        self.committed       = False
        self.rolled_back     = False

    @property
    def ok(self):
        """
        Whether this rebuild is safe to keep.

        Judged on DATA, not on bookkeeping: real loss of per-instance values, a
        change in member count, or a refused restore. A cosmetic failure such as
        not being able to reinstate the group type's name is a warning — losing
        the values because of it would be a far worse outcome than a group called
        "Group 12".
        """
        return (not self.problems
                and not self.lost
                and not self.restore_failed
                and self.members_before == self.members_after)


def rebuild_group_type(doc, group_type_id, values_by_key, target, group_by,
                       dry_run=True):
    """
    Rebuild one group type with new *target* values.

    values_by_key maps a group-by value (e.g. '1B2P') to the value to write.
    Returns a RebuildReport. Rolls back on dry_run or on any verification
    problem, so a failed attempt leaves the model untouched.
    """
    from Autodesk.Revit.DB import Transaction, TransactionStatus, ElementId
    from System.Collections.Generic import List

    Group = probe._dbtype('Group')

    report = RebuildReport()
    param_names = restore_parameter_names(doc)

    t = Transaction(doc, 'LB - Rebuild group type')
    capture = None
    try:
        t.Start()
        capture = _install_capture(t)

        old_type = doc.GetElement(group_type_id)
        if old_type is None:
            report.problems.append('Group type no longer exists.')
            raise RuntimeError('missing group type')

        try:
            report.group_type = old_type.Name
        except Exception:
            report.group_type = u'<group>'
        old_name = report.group_type

        from Autodesk.Revit.DB import FilteredElementCollector
        instances = [g for g in FilteredElementCollector(doc)
                     .OfClass(Group)
                     .WhereElementIsNotElementType()
                     .ToElements()
                     if probe.eid_int(g.GetTypeId()) == probe.eid_int(group_type_id)]

        report.instances = len(instances)
        if not instances:
            report.problems.append('No instances of this group type.')
            raise RuntimeError('no instances')

        # 1. Snapshot per-instance data for every instance BEFORE anything moves.
        snapshots = {}
        for g in instances:
            snapshots[probe.eid_int(g.Id)] = snapshot_instance(doc, g, param_names)

        victim = instances[0]
        victim_id = probe.eid_int(victim.Id)
        others = instances[1:]
        other_ids = [g.Id for g in others]

        # 2. Ungroup one instance.
        member_ids = victim.UngroupMembers()
        if not member_ids:
            report.problems.append('Ungrouping returned no members.')
            raise RuntimeError('ungroup failed')

        # 3. Write the wanted values on the loose members.
        for mid in member_ids:
            el = doc.GetElement(mid)
            if el is None:
                continue
            key = probe.read_value(el, group_by)
            if key not in values_by_key:
                continue
            wanted = values_by_key[key]
            if not wanted or probe.read_value(el, target) == wanted:
                continue
            ok, reason = probe.set_value(el, target, wanted)
            if ok:
                report.written += 1
            else:
                report.problems.append(
                    'Could not set {} on a member: {}'.format(target, reason))

        # 4. Regroup.
        ids = List[ElementId]()
        for mid in member_ids:
            ids.Add(mid)
        new_group = doc.Create.NewGroup(ids)
        if new_group is None:
            report.problems.append('Regrouping failed.')
            raise RuntimeError('regroup failed')

        new_type_id = new_group.GetTypeId()

        # 5. Swap the other instances onto the new type — this is what keeps
        #    them associated instead of becoming unique groups.
        for gid in other_ids:
            g = doc.GetElement(gid)
            if g is None:
                report.problems.append('An instance vanished during the rebuild.')
                continue
            try:
                g.ChangeTypeId(new_type_id)
            except Exception as exc:
                report.problems.append(
                    'Could not swap an instance onto the new type: {}'.format(
                        probe._short(exc)))

        doc.Regenerate()

        # 6. Compare per-instance data against the snapshot and repair only what
        #    actually changed.
        #
        #    Written as a comparison rather than "assume the swap destroyed
        #    everything and restore blindly": ChangeTypeId may well preserve
        #    members, in which case there is nothing to repair, and a restore
        #    driven by key-matching alone reports catastrophic loss that never
        #    happened. Measure first.
        #
        #    new_group carries a NEW ElementId, so its snapshot has to be looked
        #    up under the id of the instance that was ungrouped, not its own.
        pairs = [(new_group, victim_id)]
        for gid in other_ids:
            g = doc.GetElement(gid)
            if g is not None:
                pairs.append((g, probe.eid_int(gid)))

        for g, snapshot_key in pairs:
            before = snapshots.get(snapshot_key) or {}
            after = snapshot_instance(doc, g, param_names)

            report.members_before += len(before)
            report.members_after += len(after)

            for key, wanted in before.items():
                current = after.get(key)

                if current is None:
                    # No member at that key. The data may simply have re-keyed
                    # (member recreated a hair off), so look for it anywhere in
                    # this instance before calling it lost.
                    if _values_present(wanted, after, skip=target):
                        report.rekeyed += 1
                    else:
                        for name, value in wanted.items():
                            if name != target:
                                report.lost.append((name, value))
                    continue

                for name, value in wanted.items():
                    if name == target:
                        continue                   # deliberately changed
                    if current.get(name) == value:
                        report.preserved += 1
                        continue
                    el = _member_at(doc, g, key)
                    if el is None:
                        report.lost.append((name, value))
                        continue
                    ok, reason = probe.set_value(el, name, value)
                    if ok:
                        report.restored += 1
                    else:
                        report.restore_failed.append((name, reason))

        # 7. Reclaim the original name so the model reads as before.
        #    Cosmetic only — a failure here must NOT discard a good rebuild.
        try:
            doc.Delete(group_type_id)
        except Exception as exc:
            report.warnings.append(
                'Could not delete the superseded group type: {}'.format(
                    probe._short(exc)))
        try:
            new_type = doc.GetElement(new_type_id)
            if new_type is not None and old_name:
                new_type.Name = old_name
        except Exception as exc:
            report.warnings.append(
                'Group type kept its generated name instead of "{}" ({}). '
                'Cosmetic only.'.format(old_name, probe._short(exc)))

        # Force validation now so a refusal is caught here rather than silently
        # at commit — Set() reports success on group members regardless.
        try:
            doc.Regenerate()
        except Exception as exc:
            report.problems.append(
                'Validation failed after the rebuild: {}'.format(
                    probe._short(exc)))

        if capture is not None and capture.had_error:
            report.problems.append(
                'Revit raised an error during the rebuild: {}'.format(
                    capture.messages[0] if capture.messages else 'unknown'))

        # 8. Commit only if this is a real run AND nothing went wrong.
        if dry_run or not report.ok:
            report.rolled_back = True
            t.RollBack()
        else:
            # Commit() returns a status rather than raising when Revit rejects
            # the transaction, so it has to be checked.
            status = t.Commit()
            report.committed = (status == TransactionStatus.Committed)
            if not report.committed:
                report.rolled_back = True
                report.problems.append(
                    'Revit rejected the rebuild at commit and rolled it back: '
                    '{}'.format(capture.messages[0]
                                if (capture and capture.messages) else 'unknown'))

    except Exception as exc:
        if not report.problems:
            report.problems.append(probe._short(exc))
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
                report.rolled_back = True
        except Exception:
            pass

    return report
