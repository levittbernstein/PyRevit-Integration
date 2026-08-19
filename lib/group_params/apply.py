# -*- coding: utf-8 -*-
"""
Plan and apply parameter writes to elements inside groups.

Strategy, in order:

  1. If nothing is grouped, write directly. No special handling needed.
  2. If elements are grouped and the parameter cannot currently vary by group
     instance, try to enable that setting — it removes the write restriction
     entirely, because the value stops being part of the group definition.
  3. Whatever remains grouped and non-varying can only succeed where its group
     type has a single instance. Those are attempted; the rest are reported as
     blocked, with the reason, rather than failing the whole run.

The write happens in ONE transaction so a mid-run failure cannot leave the model
half-applied.
"""

from group_params import probe


class Row(object):
    """One distinct value of the group-by parameter, and what to set for it."""

    def __init__(self, key, elements):
        self.key       = key        # e.g. '1B2P'
        self.elements  = elements   # the elements sharing that key
        self.value     = u''        # what to write
        self.existing  = u''        # current value if consistent, else ''
        self.mixed     = False      # current values disagree
        # Current target value per element, positionally matching self.elements.
        # Cached at build time because plan() runs on every keystroke in the
        # value box; re-reading parameters from Revit there would make typing
        # lag badly on a model with thousands of rooms.
        self.current   = []

    @property
    def count(self):
        return len(self.elements)


def build_rows(elements, group_by, target):
    """
    Bucket *elements* by their *group_by* value.

    Pre-fills each row's value from the existing target value when every element
    in the row already agrees, so an unchanged row can be left alone rather than
    rewritten. Rows whose current values disagree are flagged, because those are
    exactly the ones worth the user's attention.
    """
    buckets = {}
    for el in elements:
        key = probe.read_value(el, group_by)
        buckets.setdefault(key, []).append(el)

    rows = []
    for key in sorted(buckets.keys(), key=_natural):
        row = Row(key, buckets[key])
        row.current = [probe.read_value(el, target) for el in row.elements]
        distinct = set(row.current)
        if len(distinct) == 1:
            row.existing = list(distinct)[0]
            row.value    = row.existing
        else:
            row.mixed    = True
            row.existing = u''
            row.value    = u''
        rows.append(row)
    return rows


def _natural(s):
    """Sort '1B2P' before '2B3P' and '9' before '10'."""
    import re
    return [int(p) if p.isdigit() else p.lower()
            for p in re.split(r'(\d+)', s or u'')]


# ── Planning ──────────────────────────────────────────────────────────────────

class Plan(object):
    def __init__(self):
        self.to_write       = []    # (element, value, row_key)
        self.unchanged      = 0
        self.grouped        = 0
        self.single_inst    = 0     # grouped, but group type has one instance
        self.multi_inst     = 0     # grouped, group type has several instances
        self.needs_vary     = False
        self.blocked        = []    # (element, row_key, reason)
        self.warnings       = []
        # Blocked work bucketed by group type, because the value propagates
        # within a group type: one Edit Group visit per TYPE clears every
        # instance of it. This turns "565 rooms" into "however many group types",
        # which is the number that actually reflects the work involved.
        self.blocked_by_type = {}   # group type name -> {row_key: value}


def plan(doc, rows, target, binding, survey, grouped_write_ok=None):
    """
    Work out what would be written and what cannot be.

    Nothing is modified. The predictions here are what the dialog shows, so they
    have to be honest about the blocked cases rather than optimistic.
    """
    p = Plan()

    for row in rows:
        if row.value == u'':
            continue                     # blank row is left untouched
        for idx, el in enumerate(row.elements):
            # Uses the cached current value, not a fresh Revit read — see Row.
            current = row.current[idx] if idx < len(row.current) else None
            if current == row.value:
                p.unchanged += 1
                continue
            p.to_write.append((el, row.value, row.key))

    varies = bool(binding.varies)

    for el, _value, key in p.to_write:
        if not survey.is_grouped(el):
            continue
        p.grouped += 1
        if survey.instance_count_for(el) <= 1:
            p.single_inst += 1
        else:
            p.multi_inst += 1

    if p.grouped and not varies:
        p.needs_vary = True

        if binding.can_enable:
            p.warnings.append(
                'Enabling "Values can vary by group instance" on "{}" is needed '
                'to write inside groups. This is a project-wide setting change '
                'and is what removes the Edit Group restriction.'.format(target))

        elif grouped_write_ok:
            # A real write was attempted and Revit allowed it, so the rule below
            # would have been wrong. Trust the probe over the rule.
            p.warnings.append(
                'Vary-by-group cannot be enabled, but a test write inside a '
                'multi-instance group SUCCEEDED, so these will be attempted. '
                'Values will propagate to every instance of each group type.')

        else:
            # Only single-instance group types can still be written.
            for el, value, key in p.to_write:
                if survey.is_grouped(el) and survey.instance_count_for(el) > 1:
                    gt_name = survey.type_name.get(
                        survey.group_type_of(el), '?')
                    p.blocked.append((
                        el, key,
                        'in group "{}" which has {} instances, and the '
                        'parameter cannot vary by group instance'.format(
                            gt_name, survey.instance_count_for(el))))
                    p.blocked_by_type.setdefault(gt_name, {})[key] = value

            if p.single_inst:
                p.warnings.append(
                    '{} element(s) are in single-instance groups and can still '
                    'be written; Revit allows those because there is nothing to '
                    'propagate to.'.format(p.single_inst))
            if binding.reason:
                p.warnings.append(
                    'Cannot enable vary-by-group: {}'.format(binding.reason))
            if p.blocked_by_type:
                p.warnings.append(
                    'The {} blocked element(s) span only {} group type(s). '
                    'Because the value propagates within a group type, editing '
                    'ONE instance of each type is enough — see the worksheet in '
                    'Preview.'.format(len(p.blocked), len(p.blocked_by_type)))

    return p


# ── Applying ──────────────────────────────────────────────────────────────────

def apply(doc, plan_obj, target, binding, survey, enable_vary=True,
          restore_vary=False, id_options=None):
    """
    Write the planned values in a single transaction.

    Returns a report dict. Per-element refusals are collected rather than raised,
    so one awkward element cannot abort the whole run.
    """
    from Autodesk.Revit.DB import Transaction, TransactionStatus

    report = {
        'written':        0,
        'failed':         [],     # (element_id, row_key, reason)
        'skipped':        0,      # known-blocked, not attempted
        'vary_enabled':   False,
        'vary_restored':  False,
        'aligned_ids':    0,
        'committed':      False,
        'error':          None,
    }

    if not plan_obj.to_write:
        report['error'] = 'Nothing to write.'
        return report

    original_varies = bool(binding.varies)

    # Capture Revit's failures rather than letting them raise a modal dialog.
    # That dialog offers an "Ungroup" button, and a user pressing it to force a
    # value through would ungroup their model — so it must never appear.
    capture = probe.make_failure_capture(rollback_on_error=True)

    t = Transaction(doc, 'LB - Set Parameter in Groups')
    try:
        t.Start()
        probe._install_capture(t, capture)

        # Step 2: lift the group restriction if we can and need to.
        if plan_obj.needs_vary and enable_vary and binding.can_enable:
            try:
                binding.definition.SetAllowVaryBetweenGroups(doc, True)
                report['vary_enabled'] = True
            except Exception as exc:
                report['error'] = (
                    'Could not enable vary-by-group on "{}": {}'.format(
                        target, probe._short(exc)))

        # Already known to be refused, verified by probe. Attempting them anyway
        # would bury the real failures under a hundred identical ones.
        blocked_ids = set(probe.eid_int(el.Id) for el, _k, _r in plan_obj.blocked)

        for el, value, key in plan_obj.to_write:
            if probe.eid_int(el.Id) in blocked_ids:
                report['skipped'] += 1
                continue

            ok, reason = probe.set_value(el, target, value,
                                        id_options=id_options)
            if ok:
                report['written'] += 1
            else:
                extra = u''
                if survey.is_grouped(el):
                    extra = ' [in group "{}", {} instance(s)]'.format(
                        survey.type_name.get(survey.group_type_of(el), '?'),
                        survey.instance_count_for(el))
                report['failed'].append(
                    (probe.eid_int(el.Id), key, (reason or 'refused') + extra))

        # Optionally put the project setting back. Off by default: switching it
        # back to "aligned per group type" makes Revit align values across group
        # instances, which can overwrite per-instance values that other elements
        # legitimately held.
        if report['vary_enabled'] and restore_vary and not original_varies:
            try:
                changed = binding.definition.SetAllowVaryBetweenGroups(doc, False)
                report['vary_restored'] = True
                try:
                    report['aligned_ids'] = len(changed) if changed else 0
                except Exception:
                    pass
            except Exception:
                pass

        # Commit() returns a status; it does not raise when Revit rejects the
        # changes. Trusting it blindly is what made the tool report 130 values
        # written when none of them persisted.
        status = t.Commit()
        report['committed'] = (status == TransactionStatus.Committed)

        if not report['committed']:
            detail = capture.messages[0] if capture.messages else (
                'Revit rejected the changes at commit time.')
            report['error'] = (
                'NOTHING WAS SAVED. Revit rejected the changes and rolled them '
                'back: {}\n\n'
                'Parameter.Set() reports success on a grouped element, but the '
                'restriction is enforced at commit. Use "Rebuild: dry run" '
                'instead — it rewrites the group definitions properly.'
                .format(detail))
            report['written'] = 0        # nothing persisted, so claim nothing
            report['vary_enabled'] = False
            report['vary_restored'] = False

    except Exception as exc:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        report['error'] = (
            'The update failed and was rolled back, so the model is unchanged: '
            '{}'.format(probe._short(exc)))

    return report
