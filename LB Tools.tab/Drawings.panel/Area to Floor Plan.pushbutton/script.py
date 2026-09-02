# -*- coding: utf-8 -*-
"""
LB Floor Plan From Area — pyRevit push-button script.

The reverse of Area Plan From Rooms: creates a Floor Plan that matches a chosen
Area Plan's view — same graphic settings, crop, scope box and view range — and
gives it a copy of the area plan's view template named "<template>_Floor Plan"
(all settings matching the original template).

It does NOT draw room boundaries, place rooms, or copy the colour scheme — just
a floor plan that looks like the area plan.

Re-runnable: reuses an existing "<name> Floor Plan" and refreshes its settings;
the template is created only when the floor plan is first made.

Runs under IronPython (pyRevit default engine) — no f-strings.
IMPORTANT: never call sys.exit() after a committed Transaction.
"""

import os
import sys

from pyrevit import revit, forms, script
from Autodesk.Revit.DB import (
    Transaction, TransactionGroup, FilteredElementCollector, ViewPlan, View,
    ViewType, ViewFamilyType, ViewFamily,
)

_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from area_from_room import viewsettings, viewtemplate, viewpick   # noqa: E402
from area_from_room.report import ViewReport                       # noqa: E402

doc = revit.doc
output = script.get_output()


def _area_plans():
    return [v for v in FilteredElementCollector(doc).OfClass(ViewPlan)
            if v.ViewType == ViewType.AreaPlan and not v.IsTemplate]


def _floor_plan_type():
    for v in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        try:
            if v.ViewFamily == ViewFamily.FloorPlan:
                return v
        except Exception:
            continue
    return None


def _target_name(area_plan):
    n = area_plan.Name
    return n.replace('Area Plan', 'Floor Plan') if 'Area Plan' in n \
        else n + ' Floor Plan'


def _unique_view_name(base):
    existing = set(v.Name for v in FilteredElementCollector(doc).OfClass(View))
    name = base
    i = 2
    while name in existing:
        name = '{} {}'.format(base, i)
        i += 1
    return name


def _find_floor_plan(name, level):
    for v in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            if (v.ViewType == ViewType.FloorPlan and not v.IsTemplate
                    and v.Name == name and v.GenLevel is not None
                    and v.GenLevel.Id == level.Id):
                return v
        except Exception:
            continue
    return None


if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project first.',
                title='No project open', warn_icon=True)

else:
    plans = _area_plans()
    if not plans:
        forms.alert('No area plans found in this model.',
                    title='Nothing to convert', warn_icon=True)
    else:
        chosen = forms.SelectFromList.show(
            viewpick.grouped(doc, plans),
            title='Choose the area plan(s) to make floor plans from',
            button_name='Create', name_attr='Name', multiselect=True,
            group_selector_title='Group')
        chosen = list(chosen) if chosen else []

        vft = _floor_plan_type() if chosen else None

        if chosen and vft is None:
            forms.alert('No Floor Plan view type exists in this model.',
                        title='No view type', warn_icon=True)
        elif chosen:
            reports = []
            tg = TransactionGroup(doc, 'Floor plans from area plans')
            tg.Start()
            for ap in chosen:
                rep = ViewReport(ap.Name)
                t = Transaction(doc, 'Floor plan from {}'.format(ap.Name))
                t.Start()
                try:
                    level = ap.GenLevel
                    if level is None:
                        rep.warn('Area plan has no associated level — skipped.')
                        t.RollBack()
                        reports.append(rep)
                        continue

                    target = _target_name(ap)
                    fp = _find_floor_plan(target, level)
                    created_new = fp is None
                    if created_new:
                        fp = ViewPlan.Create(doc, vft.Id, level.Id)
                        fp.Name = _unique_view_name(target)
                    else:
                        rep.note('Reused existing floor plan.')
                    rep.area_plan_name = fp.Name

                    viewsettings.copy_view_settings(ap, fp, rep)
                    if created_new:
                        viewtemplate.apply_look(doc, ap, fp, rep,
                                                suffix='_Floor Plan')

                    t.Commit()
                except Exception as ex:
                    if t.HasStarted() and not t.HasEnded():
                        t.RollBack()
                    rep.warn('Aborted: {}'.format(ex))
                reports.append(rep)
            tg.Assimilate()

            md = ['# Area to Floor Plan', '',
                  'Created {} floor plan(s) matching the chosen area '
                  'plan(s).'.format(len(chosen)), '']
            for rep in reports:
                md.append(rep.to_md())
                md.append('')
            output.print_md('\n'.join(md))

# No sys.exit() after a committed transaction.
