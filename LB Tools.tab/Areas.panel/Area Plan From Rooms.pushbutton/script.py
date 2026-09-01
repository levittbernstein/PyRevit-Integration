# -*- coding: utf-8 -*-
"""
LB Area Plan From Rooms — pyRevit push-button script.

Converts a room-bearing floor plan into an Area Plan whose areas mirror the
rooms: boundaries, names/numbers, shared-parameter data, view settings,
annotation, colour scheme, view-template look, key schedules and tags.

Workflow
--------
  1. Pick the floor plan(s) to convert.
  2. Confirm the target Area Scheme (defaults to "NIA" if present).
  3. Pick the area tag family/type to use (or skip tagging).
  4. Per floor plan: create "<name> Area Plan", draw area boundaries mirroring
     the rooms, place + name + populate the areas, copy view settings and
     annotation, and best-effort reproduce colour scheme / graphic look / key
     schedules, then tag.
  5. A summary lists what was done and anything needing a manual check.

Runs under IronPython (pyRevit default engine) — no f-strings.
IMPORTANT: never call sys.exit() after a committed Transaction.
"""

import os
import sys

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    Transaction, TransactionGroup, FilteredElementCollector, ViewPlan,
    ViewType, View, AreaScheme, FamilySymbol, BuiltInCategory, ViewSchedule,
    ElementId, Element, BrowserOrganization,
)

# ── lib path ──────────────────────────────────────────────────────────────────
_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from area_from_room import (                         # noqa: E402
    boundaries, areas, viewsettings, annotations, tagging, colorscheme,
    viewtemplate, keyschedule, bindings,
)
from area_from_room.report import ViewReport         # noqa: E402

doc = revit.doc
output = script.get_output()


class _Opt(object):
    """Display wrapper for forms.SelectFromList (name_attr='name')."""
    def __init__(self, name, value):
        self.name = name
        self.value = value


def _sym_label(symbol):
    """Robust 'Family : Type' label for a FamilySymbol (avoids the IronPython
    ambiguity where symbol.Name / symbol.Family.Name can throw)."""
    fam = None
    typ = None
    try:
        fam = symbol.FamilyName
    except Exception:
        pass
    try:
        typ = Element.Name.GetValue(symbol)
    except Exception:
        pass
    if fam and typ:
        return '{} : {}'.format(fam, typ)
    return typ or fam or 'Area Tag {}'.format(symbol.Id)


def _floor_plans():
    plans = [v for v in FilteredElementCollector(doc).OfClass(ViewPlan)
             if v.ViewType == ViewType.FloorPlan and not v.IsTemplate]
    return sorted(plans, key=lambda v: v.Name)


def _browser_group(view):
    """The view's Project Browser folder path (its grouping), or None."""
    try:
        bo = BrowserOrganization.GetCurrentBrowserOrganization(doc)
        names = []
        for item in bo.GetFolderItems(view.Id):
            try:
                if item.Name:
                    names.append(item.Name)
            except Exception:
                pass
        return ' / '.join(names) if names else None
    except Exception:
        return None


def _type_group(view):
    try:
        t = doc.GetElement(view.GetTypeId())
        if t is not None and t.Name:
            return t.Name
    except Exception:
        pass
    return 'Floor Plans'


def _view_type_value(view):
    """Value of the custom 'View Type' project parameter, if the view has one."""
    p = view.LookupParameter('View Type')
    if p is not None and p.HasValue:
        for getter in ('AsString', 'AsValueString'):
            try:
                s = getattr(p, getter)()
                if s:
                    return s
            except Exception:
                pass
    return None


def _grouped_plans(plans):
    """Group floor plans by the custom 'View Type' parameter (the way the
    Project Browser is organised here), falling back to the browser folder path
    then the view's Type."""
    from collections import OrderedDict
    labelled = [(_view_type_value(v) or _browser_group(v) or _type_group(v), v)
                for v in plans]
    labelled.sort(key=lambda t: (t[0], t[1].Name))
    groups = OrderedDict()
    for label, v in labelled:
        groups.setdefault(label, []).append(v)
    return groups


def _find_area_plan(name, scheme, level):
    """An existing area plan with this exact name on the same scheme+level, so a
    re-run tops up that view instead of making a duplicate."""
    for v in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            if v.ViewType != ViewType.AreaPlan or v.IsTemplate:
                continue
            if v.Name != name:
                continue
            if v.AreaScheme is None or v.AreaScheme.Id != scheme.Id:
                continue
            if v.GenLevel is None or v.GenLevel.Id != level.Id:
                continue
            return v
        except Exception:
            continue
    return None


def _pick_scheme():
    schemes = list(FilteredElementCollector(doc).OfClass(AreaScheme))
    if not schemes:
        return None
    opts = [_Opt(s.Name, s) for s in sorted(schemes, key=lambda s: s.Name)]
    chosen = forms.SelectFromList.show(
        opts, title='Choose the Area Scheme to create the area plans under',
        button_name='Use scheme', name_attr='name', multiselect=False)
    return chosen.value if chosen else None


def _pick_tag_type():
    syms = [s for s in FilteredElementCollector(doc)
            .OfCategory(BuiltInCategory.OST_AreaTags)
            .WhereElementIsElementType()]
    if not syms:
        forms.alert('No area tag families are loaded — areas will not be '
                    'tagged. Load an area tag family first if you need tags.',
                    title='No area tags', warn_icon=True)
        return None
    opts = [_Opt('— Do not tag areas —', None)]
    for s in syms:
        opts.append(_Opt(_sym_label(s), s.Id))
    chosen = forms.SelectFromList.show(
        opts, title='Choose the area tag type', button_name='Use tag',
        name_attr='name', multiselect=False)
    if chosen is None:
        return None
    return chosen.value


def _rooms_on_level(level):
    out = []
    for r in (FilteredElementCollector(doc)
              .OfCategory(BuiltInCategory.OST_Rooms)
              .WhereElementIsNotElementType()):
        try:
            if r.LevelId != level.Id:
                continue
            if r.Area <= 0 or r.Location is None:
                continue
            out.append(r)
        except Exception:
            continue
    return out


def _key_param_names():
    names = set()
    for vs in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            d = vs.Definition
            if d.IsKeySchedule and _eid(d.CategoryId) == \
                    _eid(ElementId(BuiltInCategory.OST_Rooms)):
                names.add(vs.KeyScheduleParameterName)
        except Exception:
            continue
    return names


def _eid(eid):
    return getattr(eid, 'Value', None) or getattr(eid, 'IntegerValue', None)


def _unique_view_name(base):
    existing = set(v.Name for v in
                   FilteredElementCollector(doc).OfClass(View))
    name = base
    n = 2
    while name in existing:
        name = '{} {}'.format(base, n)
        n += 1
    return name


# ── Main ──────────────────────────────────────────────────────────────────────
if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project first.',
                title='No project open', warn_icon=True)

else:
    plans = _floor_plans()
    if not plans:
        forms.alert('No floor plans found in this model.',
                    title='Nothing to convert', warn_icon=True)
    else:
        chosen = forms.SelectFromList.show(
            _grouped_plans(plans),
            title='Choose the floor plan(s) to convert to area plans',
            button_name='Convert', name_attr='Name', multiselect=True,
            group_selector_title='Group')
        chosen = list(chosen) if chosen else []

        scheme = _pick_scheme() if chosen else None

        if chosen and scheme is None:
            forms.alert('No Area Scheme available/selected — cannot create area '
                        'plans. Create an Area Scheme (e.g. "NIA") first.',
                        title='No area scheme', warn_icon=True)
        elif chosen:
            tag_type_id = _pick_tag_type()
            key_params = _key_param_names()
            key_cache = {}
            bridge_cache = {}
            manual_params = set()
            reports = []

            tg = TransactionGroup(doc, 'Area plans from rooms')
            tg.Start()
            for plan in chosen:
                rep = ViewReport(plan.Name)
                t = Transaction(doc, 'Area plan from {}'.format(plan.Name))
                t.Start()
                try:
                    level = plan.GenLevel
                    if level is None:
                        rep.warn('Floor plan has no associated level — skipped.')
                        t.RollBack()
                        reports.append(rep)
                        continue

                    rooms = _rooms_on_level(level)
                    if not rooms:
                        rep.warn('No placed rooms on this level — skipped.')
                        t.RollBack()
                        reports.append(rep)
                        continue

                    # Extend any Rooms-only parameters to Areas so all their
                    # values (and key-schedule columns) have somewhere to copy.
                    bindings.bind_room_params_to_areas(doc, rep)

                    target_name = plan.Name + ' Area Plan'
                    area_plan = _find_area_plan(target_name, scheme, level)
                    created_new = area_plan is None
                    if created_new:
                        area_plan = ViewPlan.CreateAreaPlan(
                            doc, scheme.Id, level.Id)
                        area_plan.Name = _unique_view_name(target_name)
                    else:
                        rep.note('Reused existing area plan — only missing '
                                 'boundaries/areas were added.')
                    rep.area_plan_name = area_plan.Name

                    existing_keys = boundaries.existing_boundary_keys(
                        doc, area_plan)
                    boundaries.create_area_boundaries(
                        doc, rooms, area_plan, level, rep,
                        existing_keys=existing_keys)
                    doc.Regenerate()

                    existing_nums = areas.existing_area_numbers(doc, area_plan)
                    room_to_area = areas.create_areas(
                        doc, rooms, area_plan, key_params, rep,
                        existing_numbers=existing_nums)

                    # Collect project params that need manual Areas expansion
                    # (built-in Occupancy-style ones are handled by the key
                    # schedule; these are the ones the user ticks in the UI).
                    manual_params.update(
                        bindings.unexpanded_room_params(doc, room_to_area))

                    # One-time graphic setup only when the view is first made,
                    # so a re-run never duplicates annotation or re-does it.
                    if created_new:
                        viewsettings.copy_view_settings(plan, area_plan, rep)
                        annotations.copy_annotations(doc, plan, area_plan, rep)
                        viewtemplate.apply_look(doc, plan, area_plan, rep)

                    # Colour scheme is idempotent (apply + write colours), so it
                    # runs on every pass — a re-run applies it once you've made
                    # the by-name area colour scheme.
                    colorscheme.replicate_color_scheme(
                        doc, plan, area_plan, scheme, rep)

                    keyschedule.replicate_key_schedules(
                        doc, rooms, room_to_area, key_cache, rep, bridge_cache)
                    if tag_type_id is not None:
                        tagging.tag_areas(doc, plan, area_plan, room_to_area,
                                          tag_type_id, rep)

                    t.Commit()
                except Exception as ex:
                    if t.HasStarted() and not t.HasEnded():
                        t.RollBack()
                    rep.warn('Aborted: {}'.format(ex))
                reports.append(rep)
            tg.Assimilate()

            # ── Summary ────────────────────────────────────────────────────
            md = ['# Area Plan From Rooms', '',
                  'Converted {} floor plan(s) to area scheme "{}".'
                  .format(len(chosen), scheme.Name), '']
            for rep in reports:
                md.append(rep.to_md())
                md.append('')

            colour_steps = None
            for rep in reports:
                if rep.colour_steps:
                    colour_steps = rep.colour_steps
                    break

            if colour_steps or manual_params:
                md.append('---')
                md.append('# ⚠ A couple of things to finish by hand')
                md.append('')
                step_no = 1
                if colour_steps:
                    md.append('### {}. Set up the colour scheme (one time only)'
                              .format(step_no))
                    md.append('Revit won\'t let a tool create a colour scheme '
                              'from scratch, so make it once and the tool will '
                              'colour every area plan after that:')
                    md.append('')
                    for i, s in enumerate(colour_steps, 1):
                        md.append('{}. {}'.format(i, s))
                    md.append('')
                    step_no += 1
                if manual_params:
                    md.append('### {}. Let these parameters apply to areas'
                              .format(step_no))
                    md.append('You created these parameters for Rooms only, and '
                              'Revit only lets a tool copy *shared* parameters '
                              'across. To carry their values onto the areas:')
                    md.append('')
                    md.append('1. Go to **Manage → Project Parameters**.')
                    md.append('2. Edit each parameter below and tick the '
                              '**Areas** box.')
                    md.append('3. Run this tool again — the values will fill in.')
                    md.append('')
                    md.append('**Parameters:**')
                    for p in sorted(manual_params):
                        md.append('- {}'.format(p))
                    md.append('')

            output.print_md('\n'.join(md))

# No sys.exit() after a committed transaction.
