# -*- coding: utf-8 -*-
"""
Set up a key schedule and drive a parameter from it.

Why this is the good route
--------------------------
A key-driven parameter's value lives on the KEY element, and key elements are
not inside any group. So changing the value is an edit to the key, never to a
group member, and the "Changes to groups are allowed only in group edit mode"
restriction simply does not apply. No rebuild, no group origins, no risk to
geometry.

The cost is one-off: the key has to be assigned to each element once. That
assignment IS a write to a grouped element and may be refused — but it is paid
once, after which every future value change is a single edit on the key.

What the API does and does not allow
------------------------------------
Available:  ViewSchedule.CreateKeySchedule(), ScheduleDefinition.AddField(),
            collecting key elements with FilteredElementCollector(doc, view.Id),
            renaming a key, and writing the key's own parameter values.
Not available: any documented equivalent of the UI's "Insert Data Row". New keys
            are therefore created by COPYING an existing one, which needs at
            least one row to exist. Where none does and copying fails, the tool
            reports the exact key names to add by hand rather than pretending.

Only INSTANCE parameters can be fields in a key schedule.
"""

from group_params import probe


class SetupReport(object):
    def __init__(self):
        self.schedule_name   = u''
        self.created_schedule = False
        self.field_added     = False
        self.key_param       = u''      # parameter added to the elements
        self.keys_existing   = 0
        self.keys_created    = 0
        self.keys_needed     = []       # names that could not be created
        self.values_set      = 0
        self.assigned        = 0
        self.assign_failed   = []       # (element_id, key_name, reason)
        self.problems        = []
        self.warnings        = []

    @property
    def ok(self):
        return not self.problems


# ── Finding and creating the schedule ─────────────────────────────────────────

def find_key_schedules(doc, category_id):
    """Every key schedule bound to *category_id*."""
    from Autodesk.Revit.DB import FilteredElementCollector
    ViewSchedule = probe._dbtype('ViewSchedule')

    wanted = probe.eid_int(category_id)
    out = []
    for vs in (FilteredElementCollector(doc)
               .OfClass(ViewSchedule)
               .ToElements()):
        try:
            definition = vs.Definition
            if not definition.IsKeySchedule:
                continue
            if probe.eid_int(definition.CategoryId) != wanted:
                continue
        except Exception:
            continue
        out.append(vs)
    return out


def _category_id(doc, category_bic):
    from Autodesk.Revit.DB import Category
    return Category.GetCategory(doc, category_bic).Id


def _element_param_names(doc, category_bic):
    """Parameter names on a sample element, for detecting what a schedule adds."""
    els = probe.collect_elements(doc, category_bic)
    if not els:
        return set()
    return set(probe.all_parameter_names(els[0]))


def _schedulable_field(schedule, doc, param_name):
    """The SchedulableField called *param_name*, or None."""
    try:
        for field in schedule.Definition.GetSchedulableFields():
            try:
                if field.GetName(doc) == param_name:
                    return field
            except Exception:
                continue
    except Exception:
        pass
    return None


def _has_field(schedule, doc, param_name):
    try:
        definition = schedule.Definition
        for i in range(definition.GetFieldCount()):
            if definition.GetField(i).GetName() == param_name:
                return True
    except Exception:
        pass
    return False


# ── Keys ──────────────────────────────────────────────────────────────────────

def keys_in(doc, schedule):
    """{key name: element} for one key schedule."""
    from Autodesk.Revit.DB import FilteredElementCollector
    out = {}
    try:
        for el in FilteredElementCollector(doc, schedule.Id).ToElements():
            try:
                name = el.Name
            except Exception:
                continue
            if name:
                out[name] = el
    except Exception:
        pass
    return out


def _create_key_by_copy(doc, template_key, new_name):
    """
    Make a new key by copying an existing one, then renaming it.

    The API has no documented way to add a key-schedule row, so copying the only
    thing that already is one is the available route.
    """
    from Autodesk.Revit.DB import ElementTransformUtils, XYZ
    from System.Collections.Generic import List
    from Autodesk.Revit.DB import ElementId

    ids = List[ElementId]()
    ids.Add(template_key.Id)
    copied = ElementTransformUtils.CopyElements(doc, ids, XYZ(0, 0, 0))
    for new_id in copied:
        el = doc.GetElement(new_id)
        if el is not None:
            el.Name = new_name
            return el
    return None


# ── Orchestration ─────────────────────────────────────────────────────────────

def setup(doc, category_bic, group_by, target, values_by_key,
          schedule_name=None, assign=True):
    """
    Create/extend a key schedule, populate its keys, and assign them.

    values_by_key maps a group-by value (e.g. '1B2P') to the value the key should
    carry for *target*.

    Two transactions on purpose: the schedule, fields, keys and key values are
    committed first, so that work survives even if assigning keys to grouped
    elements is then refused. Losing a correctly populated key schedule because
    of the group restriction would be the worst of both worlds.
    """
    from Autodesk.Revit.DB import Transaction, TransactionStatus
    ViewSchedule = probe._dbtype('ViewSchedule')

    report = SetupReport()
    category_id = _category_id(doc, category_bic)
    before_params = _element_param_names(doc, category_bic)

    # ── Transaction 1: schedule, field, keys, key values ─────────────────────
    capture = probe.make_failure_capture(rollback_on_error=True)
    t = Transaction(doc, 'LB - Set up key schedule')
    try:
        t.Start()
        probe._install_capture(t, capture)

        existing = find_key_schedules(doc, category_id)
        schedule = None

        # Prefer a schedule that already carries the target field.
        for vs in existing:
            if _has_field(vs, doc, target):
                schedule = vs
                break

        if schedule is None and existing:
            schedule = existing[0]
            report.warnings.append(
                'Reusing the existing key schedule "{}" rather than creating '
                'another.'.format(schedule.Name))

        if schedule is None:
            schedule = ViewSchedule.CreateKeySchedule(doc, category_id)
            report.created_schedule = True
            if schedule_name:
                try:
                    schedule.Name = schedule_name
                except Exception:
                    pass

        report.schedule_name = schedule.Name

        # Add the target as a field if it is not already there.
        if not _has_field(schedule, doc, target):
            field = _schedulable_field(schedule, doc, target)
            if field is None:
                report.problems.append(
                    '"{}" cannot be a key schedule field. Only INSTANCE '
                    'parameters of the category are eligible.'.format(target))
                raise RuntimeError('not schedulable')
            schedule.Definition.AddField(field)
            report.field_added = True

        doc.Regenerate()

        # One key per distinct group-by value.
        keys = keys_in(doc, schedule)
        report.keys_existing = len(keys)

        wanted_names = [n for n in values_by_key.keys() if n]
        template = None
        for name in sorted(keys):
            template = keys[name]
            break

        for name in wanted_names:
            if name in keys:
                continue
            if template is None:
                report.keys_needed.append(name)
                continue
            try:
                created = _create_key_by_copy(doc, template, name)
                if created is None:
                    report.keys_needed.append(name)
                else:
                    keys[name] = created
                    report.keys_created += 1
            except Exception:
                report.keys_needed.append(name)

        if report.keys_needed:
            report.warnings.append(
                'Could not create {} key(s) automatically — the API has no '
                'documented way to add a key schedule row. Add them by hand in '
                'the schedule with "Insert Data Row" and run this again.'
                .format(len(report.keys_needed)))

        # Put the wanted value on each key. Keys are not in groups, so these
        # writes are never blocked — this is the whole point of the approach.
        doc.Regenerate()
        keys = keys_in(doc, schedule)
        for name, value in values_by_key.items():
            key_el = keys.get(name)
            if key_el is None or not value:
                continue
            ok, reason = probe.set_value(key_el, target, value)
            if ok:
                report.values_set += 1
            else:
                report.problems.append(
                    'Could not set {} on key "{}": {}'.format(
                        target, name, reason))

        status = t.Commit()
        if status != TransactionStatus.Committed:
            report.problems.append(
                'Revit rejected the key schedule setup: {}'.format(
                    capture.messages[0] if capture.messages else 'unknown'))
            return report

    except Exception as exc:
        if not report.problems:
            report.problems.append(probe._short(exc))
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        return report

    # Which parameter did the schedule add to the elements?
    after_params = _element_param_names(doc, category_bic)
    added = sorted(after_params - before_params)
    if added:
        report.key_param = added[0]
    else:
        # Pre-existing schedule, so nothing new appeared. Fall back to the
        # schedule's own name, which is what Revit names the parameter.
        report.key_param = report.schedule_name

    if not assign:
        return report

    # ── Transaction 2: assign keys to the elements ───────────────────────────
    # Separate so a refusal here cannot discard the populated key schedule above.
    report2 = _assign(doc, category_bic, group_by, report.key_param, values_by_key)
    report.assigned = report2['assigned']
    report.assign_failed = report2['failed']
    if report2['error']:
        report.warnings.append(report2['error'])

    return report


def _assign(doc, category_bic, group_by, key_param, values_by_key):
    """Point each element's key parameter at the key matching its group-by value."""
    from Autodesk.Revit.DB import Transaction, TransactionStatus

    out = {'assigned': 0, 'failed': [], 'error': None}

    options = probe.key_options(doc, category_bic)
    if not options:
        out['error'] = 'No keys found to assign.'
        return out

    capture = probe.make_failure_capture(rollback_on_error=True)
    t = Transaction(doc, 'LB - Assign schedule keys')
    try:
        t.Start()
        probe._install_capture(t, capture)

        for el in probe.collect_elements(doc, category_bic):
            name = probe.read_value(el, group_by)
            if name not in values_by_key:
                continue
            if probe.read_value(el, key_param) == name:
                continue
            ok, reason = probe.set_value(el, key_param, name,
                                        id_options=options)
            if ok:
                out['assigned'] += 1
            else:
                out['failed'].append((probe.eid_int(el.Id), name, reason))

        status = t.Commit()
        if status != TransactionStatus.Committed:
            out['assigned'] = 0
            out['error'] = (
                'Assigning the keys was REJECTED and rolled back: {} '
                'The key schedule itself was still created and populated, so '
                'the keys only need assigning — which can be done in Edit Group '
                'mode, once.'.format(
                    capture.messages[0] if capture.messages else ''))
    except Exception as exc:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass
        out['assigned'] = 0
        out['error'] = probe._short(exc)

    return out
