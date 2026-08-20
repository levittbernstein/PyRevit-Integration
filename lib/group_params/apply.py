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
    Write the planned values, in TWO transactions.

    Split deliberately. Revit enforces the group restriction at COMMIT, and the
    failure handler has to roll the transaction back — so a single grouped
    element in the batch discards every other write with it, including all the
    valid ungrouped ones. Writing everything in one transaction meant one
    refused room could produce "nothing was written at all".

    So elements that cannot be in a multi-instance group go first, on their own,
    and are safe from anything the risky ones do. The risky ones follow in a
    second transaction whose failure costs nothing already earned.

    This also means the tool no longer depends on predicting correctly which
    writes Revit will allow — a wrong prediction now costs one wasted
    transaction instead of the whole run.
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

    # Split by risk, not by prediction. Anything in a group type with several
    # instances might be refused at commit; everything else will not be.
    safe, risky = [], []
    for item in plan_obj.to_write:
        el = item[0]
        if survey.instance_count_for(el) > 1:
            risky.append(item)
        else:
            safe.append(item)

    # Phase 1 — the writes that cannot be refused for group reasons.
    if safe:
        _write_batch(doc, safe, target, binding, survey, report,
                     id_options=id_options,
                     enable_vary=(plan_obj.needs_vary and enable_vary),
                     restore_vary=restore_vary,
                     label='LB - Set Parameter (ungrouped)')

    # Phase 2 — the ones Revit may refuse. Separate transaction, so a refusal
    # here cannot undo phase 1.
    if risky:
        risky_report = dict(report)
        risky_report['written'] = 0
        risky_report['failed'] = []
        risky_report['error'] = None
        risky_report['committed'] = False

        _write_batch(doc, risky, target, binding, survey, risky_report,
                     id_options=id_options, enable_vary=False,
                     restore_vary=False,
                     label='LB - Set Parameter (in groups)')

        if risky_report['committed']:
            report['written'] += risky_report['written']
            report['failed'].extend(risky_report['failed'])
        else:
            report['skipped'] += len(risky)
            report['blocked_note'] = (
                '{} element(s) inside multi-instance group types were refused '
                'by Revit and rolled back on their own, leaving the {} '
                'ungrouped write(s) intact. {}'.format(
                    len(risky), report['written'],
                    risky_report['error'] or ''))

    report['committed'] = report['written'] > 0 or not plan_obj.to_write
    return report


def _write_batch(doc, items, target, binding, survey, report,
                 id_options=None, enable_vary=False, restore_vary=False,
                 label='LB - Set Parameter'):
    """
    Write one batch in its own transaction, updating *report* in place.

    Kept separate so each batch's success or failure is independent.
    """
    from Autodesk.Revit.DB import Transaction, TransactionStatus

    original_varies = bool(binding.varies)

    # Capture Revit's failures rather than letting them raise a modal dialog.
    # That dialog offers an "Ungroup" button, and a user pressing it to force a
    # value through would ungroup their model — so it must never appear.
    capture = probe.make_failure_capture(rollback_on_error=True)

    t = Transaction(doc, label)
    written_here = 0
    try:
        t.Start()
        probe._install_capture(t, capture)

        if enable_vary and binding.can_enable and binding.definition is not None:
            try:
                binding.definition.SetAllowVaryBetweenGroups(doc, True)
                report['vary_enabled'] = True
            except Exception as exc:
                report['error'] = (
                    'Could not enable vary-by-group on "{}": {}'.format(
                        target, probe._short(exc)))

        for el, value, key in items:
            ok, reason = probe.set_value(el, target, value,
                                        id_options=id_options)
            if ok:
                written_here += 1
            else:
                extra = u''
                if survey.is_grouped(el):
                    extra = ' [in group "{}", {} instance(s)]'.format(
                        survey.type_name.get(survey.group_type_of(el), '?'),
                        survey.instance_count_for(el))
                report['failed'].append(
                    (probe.eid_int(el.Id), key, (reason or 'refused') + extra))

        # Off by default: switching back to "aligned per group type" makes Revit
        # align values across group instances, overwriting per-instance values.
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
        # changes. Trusting it blindly once made the tool report 130 values
        # written when none persisted.
        status = t.Commit()
        if status == TransactionStatus.Committed:
            report['written'] += written_here
            report['committed'] = True
        else:
            detail = capture.messages[0] if capture.messages else (
                'Revit rejected the changes at commit time.')
            report['error'] = (
                'This batch was rejected and rolled back: {} '
                'Parameter.Set() reports success on a grouped element, but the '
                'restriction is enforced at commit.'.format(detail))
            report['vary_enabled'] = False
            report['vary_restored'] = False

    except Exception as exc:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        report['error'] = (
            'This batch failed and was rolled back: {}'.format(
                probe._short(exc)))

    return report
