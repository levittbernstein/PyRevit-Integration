# -*- coding: utf-8 -*-
"""
LB Set Parameter in Groups — pyRevit push-button script.

Sets a parameter across many elements at once, bucketed by another parameter's
value (e.g. every Room named 1B2P gets Habitable Rooms = 2), including elements
that live inside model groups.

Why this tool exists
--------------------
Revit refuses parameter writes on a group member from outside Edit Group mode:
"Changes to groups are allowed only in group edit mode." That applies to
schedule cells, the Properties palette AND the API, which is why editing a
non-itemised schedule row fails as soon as any of its elements are grouped.

The way through is to enable "Values can vary by group instance" on the
parameter — the value then belongs to the element rather than the group
definition, so writing it is no longer a change to the group. That setting is
itself restricted by data type (Length and Yes/No are excluded by design), so
the tool probes the live model to find out rather than guessing, and reports
honestly when a write is impossible.

IMPORTANT — see README "Developer notes": never call sys.exit() after a
committed Transaction. pyRevit treats SystemExit as a signal to roll back
transactions from this script run.
"""

import os
import sys
import traceback

from pyrevit import forms

_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from group_params import apply as gapply          # noqa: E402
from group_params.dialog import GroupParamDialog  # noqa: E402


doc = __revit__.ActiveUIDocument.Document  # noqa: F821

# Categories worth offering. Rooms first — the case this was built for.
def _categories():
    from Autodesk.Revit.DB import BuiltInCategory as B
    wanted = [
        ('Rooms',            'OST_Rooms'),
        ('Areas',            'OST_Areas'),
        ('Doors',            'OST_Doors'),
        ('Windows',          'OST_Windows'),
        ('Walls',            'OST_Walls'),
        ('Floors',           'OST_Floors'),
        ('Ceilings',         'OST_Ceilings'),
        ('Furniture',        'OST_Furniture'),
        ('Casework',         'OST_Casework'),
        ('Generic Models',   'OST_GenericModel'),
        ('Plumbing Fixtures', 'OST_PlumbingFixtures'),
        ('Mechanical Equipment', 'OST_MechanicalEquipment'),
        ('Specialty Equipment', 'OST_SpecialityEquipment'),
        ('Structural Columns', 'OST_StructuralColumns'),
        ('Structural Framing', 'OST_StructuralFraming'),
        ('Railings',         'OST_StairsRailing'),
        ('Stairs',           'OST_Stairs'),
        ('Roofs',            'OST_Roofs'),
    ]
    out = []
    for label, name in wanted:
        bic = getattr(B, name, None)
        if bic is not None:
            out.append((label, bic))
    return out


if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project file first.',
                title='No project open', warn_icon=True)

else:
    try:
        cats = _categories()
    except Exception:
        cats = []
        forms.alert('Could not read Revit categories.\n\n'
                    + traceback.format_exc(),
                    title='Category error', warn_icon=True)

    if not cats:
        forms.alert('No usable categories found.',
                    title='Nothing to do', warn_icon=True)
    else:
        dlg = GroupParamDialog(doc, cats)
        action, state = dlg.show()

        if action == 'apply' and state['plan'] is not None:
            plan = state['plan']

            lines = [
                'About to write "{}" on {} element(s).'.format(
                    state['target'], len(plan.to_write)),
            ]

            if plan.needs_vary and state['enable_vary'] and \
                    state['binding'].can_enable:
                lines += [
                    '',
                    'This requires enabling "Values can vary by group instance" '
                    'on "{}". That is a PROJECT-WIDE setting change and is what '
                    'allows the write inside groups.'.format(state['target']),
                ]
                if state['restore_vary']:
                    lines += [
                        '',
                        'You have asked for it to be switched back off '
                        'afterwards. Revit will then align values across group '
                        'instances, which can overwrite values that other '
                        'elements held per instance.',
                    ]

            if plan.blocked:
                lines += [
                    '',
                    '{} element(s) CANNOT be written and will be skipped — '
                    'they are in group types with several instances and the '
                    'parameter cannot vary by group instance. Revit has no '
                    'route around this.'.format(len(plan.blocked)),
                ]

            lines += ['', 'Proceed?']

            if forms.alert('\n'.join(lines), title='Confirm parameter update',
                           ok=False, yes=True, no=True, warn_icon=True):

                report = gapply.apply(
                    doc, plan, state['target'], state['binding'],
                    state['survey'],
                    enable_vary=state['enable_vary'],
                    restore_vary=state['restore_vary'])

                if report['error'] and not report['committed']:
                    forms.alert('Update failed:\n\n{}'.format(report['error']),
                                title='Update failed', warn_icon=True)
                else:
                    out = [
                        'Parameter update complete.',
                        '',
                        'Written:  {}'.format(report['written']),
                        'Failed:   {}'.format(len(report['failed'])),
                    ]
                    if report['skipped']:
                        out += [
                            'Skipped:  {} (blocked inside multi-instance '
                            'groups)'.format(report['skipped']),
                            '',
                            'Run Preview for the group-type worksheet — those {} '
                            'element(s) span only {} group type(s), so editing '
                            'one instance of each is enough.'.format(
                                report['skipped'], len(plan.blocked_by_type)),
                        ]
                    if report['vary_enabled']:
                        out += [
                            '',
                            '"Values can vary by group instance" was enabled on '
                            '"{}".'.format(state['target']),
                        ]
                        if report['vary_restored']:
                            out.append(
                                'It was switched back off; Revit aligned {} '
                                'element(s).'.format(report['aligned_ids']))
                        else:
                            out.append(
                                'It has been LEFT ON. Future manual edits to '
                                'this parameter will no longer propagate '
                                'between group instances. Untick it in Manage > '
                                'Project Parameters if you want the old '
                                'behaviour back.')
                    if report['failed']:
                        out += ['', 'First few failures:']
                        for eid, key, reason in report['failed'][:8]:
                            out.append('  {} [{}]: {}'.format(eid, key, reason))
                        out.append('')
                        out.append('Run Preview before applying to see all of '
                                   'them with reasons.')

                    forms.alert('\n'.join(out),
                                title='LB Set Parameter in Groups')

# No sys.exit() after this point — see the module docstring.
