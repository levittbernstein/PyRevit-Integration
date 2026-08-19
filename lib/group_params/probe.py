# -*- coding: utf-8 -*-
"""
Survey the model and establish, empirically, what writes are actually possible.

Every capability question here is answered by attempting the operation inside a
transaction that is then rolled back, never by inferring from metadata.  Revit's
rules about group members and "vary by group instance" are not fully documented
and differ by version, data type and binding, so guessing produces a tool that
fails halfway through a run.  A probe costs one transaction and is exact.

Nothing in this module modifies the model.
"""


def _dbtype(name):
    """
    Return the Autodesk.Revit.DB type called *name*.

    IronPython sometimes cannot resolve a .NET type via 'from X import Y' when
    the importing module is inside a Python package, so fall back to reflection
    off a type that always imports.
    """
    try:
        mod = __import__('Autodesk.Revit.DB', fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass

    import clr
    from Autodesk.Revit.DB import Transaction as _anchor
    t = clr.GetClrType(_anchor).Assembly.GetType('Autodesk.Revit.DB.' + name)
    if t is None:
        raise ImportError(
            'Cannot resolve Autodesk.Revit.DB.{} in this Revit version.'
            .format(name))
    return t


def eid_int(element_id):
    """Integer of an ElementId — Revit 2024+ uses .Value, earlier .IntegerValue."""
    v = getattr(element_id, 'Value', None)
    if v is None:
        v = getattr(element_id, 'IntegerValue', None)
    return v


# ── Reading parameter values ──────────────────────────────────────────────────

def param_by_name(element, name):
    """The first non-null parameter on *element* called *name*."""
    try:
        for p in element.Parameters:
            if p.Definition is not None and p.Definition.Name == name:
                return p
    except Exception:
        pass
    return None


def read_value(element, name):
    """
    Parameter value as a display string, or '' when absent or empty.

    Used both for grouping elements and for pre-filling the mapping table, so it
    has to be stable: the same underlying value must always render identically
    or rows would split apart.
    """
    p = param_by_name(element, name)
    if p is None:
        return u''
    try:
        if not p.HasValue:
            return u''
        st = p.StorageType.ToString()
        if st == 'String':
            return p.AsString() or u''
        if st == 'Integer':
            return u'{}'.format(p.AsInteger())
        if st == 'Double':
            # AsValueString honours project units; fall back to the raw double.
            return p.AsValueString() or u'{}'.format(p.AsDouble())
        if st == 'ElementId':
            return p.AsValueString() or u'{}'.format(eid_int(p.AsElementId()))
    except Exception:
        pass
    return u''


def writable_parameter_names(element):
    """Names of parameters on *element* that can be written."""
    out = set()
    try:
        for p in element.Parameters:
            if p.Definition is None or p.IsReadOnly:
                continue
            out.add(p.Definition.Name)
    except Exception:
        pass
    return sorted(out)


def all_parameter_names(element):
    """Every parameter name on *element*, writable or not (for grouping by)."""
    out = set()
    try:
        for p in element.Parameters:
            if p.Definition is not None:
                out.add(p.Definition.Name)
    except Exception:
        pass
    return sorted(out)


# ── Project parameter binding ─────────────────────────────────────────────────

class Binding(object):
    """What the project parameter bindings say about one parameter."""

    def __init__(self, name):
        self.name          = name
        self.definition    = None    # InternalDefinition, when it is a project param
        self.is_instance   = None    # True / False / None when unknown
        self.type_name     = u''     # data type, best effort
        self.varies        = None    # current GetAllowVaryBetweenGroups
        self.can_enable    = None    # filled in by probe_vary_capability
        self.reason        = u''     # why it cannot be enabled

    @property
    def is_project_parameter(self):
        return self.definition is not None


def find_binding(doc, name):
    """
    Locate *name* in the document's project parameter bindings.

    Built-in parameters are absent from BindingMap, which is itself the answer to
    "why is the checkbox greyed out" for them: the vary-by-group setting only
    exists for project and shared parameters.
    """
    info = Binding(name)

    try:
        InstanceBinding = _dbtype('InstanceBinding')
    except Exception:
        InstanceBinding = None

    try:
        it = doc.ParameterBindings.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            definition = it.Key
            if definition is None or definition.Name != name:
                continue

            info.definition = definition
            binding = it.Current
            if InstanceBinding is not None:
                info.is_instance = isinstance(binding, InstanceBinding)

            info.type_name = _data_type_name(definition)

            try:
                info.varies = definition.GetAllowVaryBetweenGroups(doc)
            except Exception:
                info.varies = None
            break
    except Exception:
        pass

    return info


def _data_type_name(definition):
    """Human-readable data type of a parameter definition, across API versions."""
    # Revit 2022+ : GetDataType() returns a ForgeTypeId
    try:
        ftid = definition.GetDataType()
        s = ftid.TypeId if hasattr(ftid, 'TypeId') else str(ftid)
        if s:
            # "autodesk.spec.aec:length-2.0.0" -> "length"
            tail = s.split(':')[-1]
            return tail.split('-')[0] or s
    except Exception:
        pass
    # Older API
    try:
        return definition.ParameterType.ToString()
    except Exception:
        return u''


# ── Capability probes (all rolled back) ───────────────────────────────────────

def probe_vary_capability(doc, binding):
    """
    Determine whether "Values can vary by group instance" can be turned on.

    Attempted for real and rolled back, because the whitelist is undocumented
    and version-dependent — SetAllowVaryBetweenGroups throws ArgumentException
    for a type it does not support, and that exception is the only reliable
    answer available.

    Fills binding.can_enable and binding.reason; returns binding.
    """
    from Autodesk.Revit.DB import Transaction

    if binding.definition is None:
        binding.can_enable = False
        binding.reason = (
            'Not a project or shared parameter. Built-in parameters have no '
            '"vary by group instance" setting at all.')
        return binding

    if binding.is_instance is False:
        binding.can_enable = False
        binding.reason = (
            'Bound as a TYPE parameter. Only instance parameters can vary by '
            'group instance, because a type is shared by definition.')
        return binding

    if binding.varies:
        binding.can_enable = True
        binding.reason = u'Already enabled.'
        return binding

    t = Transaction(doc, 'LB - probe vary by group (rolled back)')
    try:
        t.Start()
        binding.definition.SetAllowVaryBetweenGroups(doc, True)
        binding.can_enable = True
        binding.reason = u'Supported for this parameter.'
    except Exception as exc:
        binding.can_enable = False
        binding.reason = (
            'Revit refuses it for this data type ({}). Length and Yes/No are '
            'excluded by design. [{}]'.format(
                binding.type_name or 'unknown', _short(exc)))
    finally:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass

    return binding


def make_failure_capture(rollback_on_error=True, resolve_errors=False):
    """
    A failure preprocessor that records Revit's failures instead of showing them.

    Two reasons this matters.  First, Parameter.Set() returns True on a group
    member; the group restriction is only enforced later through the failure
    system, so failures are the ONLY place the refusal appears.  Second, Revit's
    own dialog for that failure offers an "Ungroup" button — leaving it to
    appear would invite a user to ungroup their model to force a value through.
    Capturing the failure and rolling back removes that trap.
    """
    from Autodesk.Revit.DB import (
        IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
    )

    class _Capture(IFailuresPreprocessor):
        def __init__(self):
            self.messages = []
            self.had_error = False
            self.resolved = []

        def PreprocessFailures(self, accessor):
            errors = []
            for failure in accessor.GetFailureMessages():
                try:
                    text = failure.GetDescriptionText()
                except Exception:
                    text = u''
                try:
                    is_error = failure.GetSeverity() == FailureSeverity.Error
                except Exception:
                    is_error = False

                self.messages.append(text)
                if is_error:
                    self.had_error = True
                    errors.append(failure)
                else:
                    try:
                        accessor.DeleteWarning(failure)
                    except Exception:
                        pass

            if errors and resolve_errors:
                # Applies Revit's own default resolution, which for "Can't keep
                # elements joined" means UNJOINING geometry. A real model change,
                # so this path is opt-in only and never the default.
                for failure in errors:
                    try:
                        accessor.ResolveFailure(failure)
                        self.resolved.append(
                            failure.GetDescriptionText())
                    except Exception:
                        pass
                return FailureProcessingResult.ProceedWithCommit

            if errors and rollback_on_error:
                return FailureProcessingResult.ProceedWithRollBack
            return FailureProcessingResult.Continue

    capture = _Capture()
    capture.resolved = []
    return capture


def _install_capture(transaction, capture):
    try:
        opts = transaction.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(capture)
        opts.SetClearAfterRollback(True)
        transaction.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def probe_write(doc, element, param_name, value, id_options=None):
    """
    Try writing *value* for real, force validation, then roll back.

    Returns (ok, reason).

    Set() alone proves nothing: it returns True for a group member and the
    refusal only appears when Revit validates the change.  So this regenerates
    the document inside the probe transaction and treats any captured error as a
    refusal.  Without the regenerate step this function reports success for
    writes that cannot possibly persist — which is exactly the bug that let the
    tool claim it had written 130 values it had not.

    Errs towards "not writable": if validation cannot be forced, the answer is
    no rather than an optimistic yes.
    """
    from Autodesk.Revit.DB import Transaction

    capture = make_failure_capture(rollback_on_error=True)
    t = Transaction(doc, 'LB - probe parameter write (rolled back)')
    try:
        t.Start()
        _install_capture(t, capture)

        ok, reason = set_value(element, param_name, value, id_options=id_options)
        if not ok:
            return False, reason

        # This is what actually surfaces the group restriction.
        try:
            doc.Regenerate()
        except Exception as exc:
            return False, _short(exc)

        if capture.had_error:
            return False, (capture.messages[0] if capture.messages
                           else 'Revit rejected the change')

        return True, None

    except Exception as exc:
        return False, _short(exc)
    finally:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass


def distinct_test_value(current, storage_hint=u''):
    """
    A value guaranteed to differ from *current*, for probing a real write.

    Probing with the value already held would be a no-op that Revit accepts even
    where a genuine change would be refused, which would report success falsely.
    """
    cur = (current or u'').strip()
    try:
        return u'{}'.format(int(round(float(cur))) + 1)
    except (ValueError, TypeError):
        pass
    return u'0' if cur == u'1' else u'1'


def probe_data_types(doc):
    """
    Which of the model's existing project parameters can vary by group, by type.

    Answers "what data type would a replacement parameter need to be?" from the
    live model rather than from documentation Autodesk does not publish.  Every
    binding is attempted in ONE transaction which is then rolled back, so this
    is both fast and non-destructive.

    Returns (allowed, blocked) — each a dict of data type name -> [param names].
    """
    from Autodesk.Revit.DB import Transaction

    try:
        InstanceBinding = _dbtype('InstanceBinding')
    except Exception:
        InstanceBinding = None

    entries = []
    try:
        it = doc.ParameterBindings.ForwardIterator()
        it.Reset()
        while it.MoveNext():
            definition = it.Key
            if definition is None:
                continue
            is_instance = True
            if InstanceBinding is not None:
                is_instance = isinstance(it.Current, InstanceBinding)
            entries.append((definition, is_instance))
    except Exception:
        pass

    allowed = {}
    blocked = {}

    t = Transaction(doc, 'LB - probe vary-by-group data types (rolled back)')
    try:
        t.Start()
        for definition, is_instance in entries:
            name = definition.Name
            dtype = _data_type_name(definition) or u'unknown'

            if not is_instance:
                blocked.setdefault(dtype + ' (type-bound)', []).append(name)
                continue

            try:
                already = definition.GetAllowVaryBetweenGroups(doc)
            except Exception:
                already = False

            if already:
                allowed.setdefault(dtype, []).append(name)
                continue

            try:
                definition.SetAllowVaryBetweenGroups(doc, True)
                allowed.setdefault(dtype, []).append(name)
            except Exception:
                blocked.setdefault(dtype, []).append(name)
    except Exception:
        pass
    finally:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except Exception:
            pass

    return allowed, blocked


def _short(exc):
    """First line of an exception message, trimmed."""
    msg = str(exc).strip().split('\n')[0]
    return msg[:220]


# ── Writing ───────────────────────────────────────────────────────────────────

def key_options(doc, category_bic=None):
    """
    {key name: ElementId} for every key-schedule key in the document.

    Key schedule entries are elements that live inside the key schedule view, so
    they are collected by passing the view id to FilteredElementCollector.

    Needed because a key parameter has ElementId storage: setting it means
    resolving a name like '1B2P' to the key element that carries it.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    ViewSchedule = _dbtype('ViewSchedule')

    wanted = None
    if category_bic is not None:
        try:
            wanted = int(category_bic)
        except (TypeError, ValueError):
            wanted = None

    out = {}
    for vs in (FilteredElementCollector(doc)
               .OfClass(ViewSchedule)
               .ToElements()):
        try:
            definition = vs.Definition
            if not definition.IsKeySchedule:
                continue
            if wanted is not None:
                if eid_int(definition.CategoryId) != wanted:
                    continue
        except Exception:
            continue

        try:
            for key_el in FilteredElementCollector(doc, vs.Id).ToElements():
                try:
                    name = key_el.Name
                except Exception:
                    continue
                if name:
                    out[name] = key_el.Id
        except Exception:
            continue

    return out


def set_value(element, param_name, value, id_options=None):
    """
    Write *value* (a string) to *param_name*, coercing to the storage type.

    *id_options* maps a display name to an ElementId, for parameters with
    ElementId storage such as a key-schedule key. Without it an ElementId
    parameter cannot be written at all.

    Returns (ok, reason). Never raises for an ordinary refusal — the caller
    collects reasons and reports them per element.
    """
    p = param_by_name(element, param_name)
    if p is None:
        return False, 'parameter not present on this element'
    if p.IsReadOnly:
        return False, 'parameter is read-only'

    text = u'' if value is None else u'{}'.format(value).strip()

    try:
        st = p.StorageType.ToString()

        if st == 'String':
            return (True, None) if p.Set(text) else (False, 'Set() refused the value')

        if st == 'Integer':
            if text == u'':
                return False, 'no value given'
            low = text.lower()
            if low in ('yes', 'true'):
                iv = 1
            elif low in ('no', 'false'):
                iv = 0
            else:
                try:
                    iv = int(round(float(text)))
                except ValueError:
                    return False, '"{}" is not a whole number'.format(text)
            return (True, None) if p.Set(iv) else (False, 'Set() refused the value')

        if st == 'Double':
            if text == u'':
                return False, 'no value given'
            # SetValueString applies project units, which is what the user typed.
            try:
                if p.SetValueString(text):
                    return True, None
            except Exception:
                pass
            try:
                return ((True, None) if p.Set(float(text))
                        else (False, 'Set() refused the value'))
            except ValueError:
                return False, '"{}" is not a number'.format(text)

        if st == 'ElementId':
            if text == u'':
                return False, 'no value given'
            options = id_options or {}
            eid = options.get(text)
            if eid is None:
                # Case-insensitive second attempt — key names typed by hand
                # rarely match capitalisation exactly.
                for name, candidate in options.items():
                    if name.strip().lower() == text.lower():
                        eid = candidate
                        break
            if eid is None:
                return False, (
                    '"{}" is not one of the available keys ({} known)'.format(
                        text, len(options)))
            return (True, None) if p.Set(eid) else (False, 'Set() refused the key')

        return False, 'unsupported storage type {}'.format(st)

    except Exception as exc:
        return False, _short(exc)


# ── Group survey ──────────────────────────────────────────────────────────────

class GroupSurvey(object):
    def __init__(self):
        self.instances_per_type = {}   # group type id int -> instance count
        self.type_name = {}            # group type id int -> name
        self.member_group = {}         # element id int -> group type id int
        # The real ElementId, kept because reconstructing one from an int via
        # ElementId(int) is not reliable across Revit versions and silently
        # resolved to the wrong element.
        self.type_eid = {}             # group type id int -> ElementId

    def group_type_of(self, element):
        # Tolerates a stale element. Element wrappers are invalidated by any
        # transaction that recreates them — INCLUDING one that was rolled back —
        # and reading .Id then throws InvalidObjectException. The caller should
        # be re-reading the model rather than relying on this, but a dead
        # reference must not take the whole dialog down.
        try:
            return self.member_group.get(eid_int(element.Id))
        except Exception:
            return None

    def is_grouped(self, element):
        return self.group_type_of(element) is not None

    def instance_count_for(self, element):
        gt = self.group_type_of(element)
        return self.instances_per_type.get(gt, 0) if gt is not None else 0

    def is_stale(self, element):
        """True when *element* can no longer be read."""
        try:
            eid_int(element.Id)
            return False
        except Exception:
            return True


def survey_groups(doc):
    """
    Map every group member to its group type, and count instances per type.

    The instance count matters: a group type with exactly one instance accepts
    parameter writes on its members even when the parameter cannot vary by
    group, because Revit has nothing to propagate the change to.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    Group = _dbtype('Group')

    survey = GroupSurvey()

    for grp in (FilteredElementCollector(doc)
                .OfClass(Group)
                .WhereElementIsNotElementType()
                .ToElements()):
        try:
            gt_id = eid_int(grp.GetTypeId())
        except Exception:
            continue

        survey.instances_per_type[gt_id] = survey.instances_per_type.get(gt_id, 0) + 1

        if gt_id not in survey.type_name:
            survey.type_eid[gt_id] = grp.GetTypeId()
            name = None
            try:
                gt = doc.GetElement(grp.GetTypeId())
                if gt is not None:
                    name = gt.Name
            except Exception:
                name = None
            if not name:
                # GroupType.Name is empty in some cases; the instance's own name
                # carries the group name and is a usable stand-in.
                try:
                    name = grp.Name
                except Exception:
                    name = None
            survey.type_name[gt_id] = name or '<unnamed group>'

        try:
            for mid in grp.GetMemberIds():
                survey.member_group[eid_int(mid)] = gt_id
        except Exception:
            pass

    return survey


def collect_elements(doc, category_bic):
    """Placed elements of one built-in category, excluding unplaced rooms."""
    from Autodesk.Revit.DB import FilteredElementCollector

    out = []
    for el in (FilteredElementCollector(doc)
               .OfCategory(category_bic)
               .WhereElementIsNotElementType()
               .ToElements()):
        # An unplaced or redundant room has no area and cannot hold useful data;
        # including them would create phantom rows in the mapping table.
        try:
            if hasattr(el, 'Area') and el.Area == 0 and hasattr(el, 'Location'):
                if el.Location is None:
                    continue
        except Exception:
            pass
        out.append(el)
    return out
