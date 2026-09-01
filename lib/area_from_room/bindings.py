# -*- coding: utf-8 -*-
"""Extend Rooms-only project/shared parameters to the Areas category too.

Most LB shared parameters apply to both Rooms and Areas, but some are bound to
Rooms only — so areas can't hold them and their values (including the columns of
room key schedules) are left behind. This adds Areas to those bindings, exactly
like ticking Areas in Manage > Project Parameters, so every room parameter has
an area home to copy into.

It's a project-wide settings change, run once per conversion (idempotent — a
parameter already bound to Areas is skipped). Built-in parameters aren't in the
binding map and are left alone.
"""

from Autodesk.Revit.DB import (
    Category, BuiltInCategory, CategorySet, InstanceBinding, TypeBinding,
)


def _cid(cat):
    try:
        cid = cat.Id
        return getattr(cid, 'Value', None) or getattr(cid, 'IntegerValue', None)
    except Exception:
        return None


def unexpanded_room_params(doc, room_to_area):
    """Non-built-in room parameters with a value but no area equivalent — the
    ones the user can tick onto Areas in Manage > Project Parameters, then re-run
    to populate. Returns a set of names. Built-ins (which can't be added to
    Areas at all) are excluded."""
    from Autodesk.Revit.DB import BuiltInParameter, StorageType
    out = set()
    if not room_to_area:
        return out
    sample_room, sample_area = next(iter(room_to_area.items()))
    for p in sample_room.Parameters:
        try:
            if p.IsReadOnly or not p.HasValue:
                continue
            if p.StorageType not in (StorageType.String, StorageType.Integer,
                                     StorageType.Double, StorageType.ElementId):
                continue
            if p.Definition.BuiltInParameter != BuiltInParameter.INVALID:
                continue          # built-in — can't be added to Areas manually
            name = p.Definition.Name
        except Exception:
            continue
        if sample_area.LookupParameter(name) is None:
            out.add(name)
    return out


def bind_room_params_to_areas(doc, report):
    bmap = doc.ParameterBindings
    rooms = Category.GetCategory(doc, BuiltInCategory.OST_Rooms)
    areas = Category.GetCategory(doc, BuiltInCategory.OST_Areas)
    if rooms is None or areas is None:
        return 0

    # Collect first (don't mutate the map while iterating it).
    pending = []
    it = bmap.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        definition = it.Key
        binding = it.Current
        try:
            cats = list(binding.Categories)
        except Exception:
            continue
        ids = set(_cid(c) for c in cats)
        if _cid(rooms) in ids and _cid(areas) not in ids:
            pending.append((definition, binding, cats))

    bound = []
    failed = []
    for definition, binding, cats in pending:
        ok = False
        # Attempt 1: rebuild the binding with Areas added.
        try:
            newset = CategorySet()
            for c in cats:
                newset.Insert(c)
            newset.Insert(areas)
            newb = (TypeBinding(newset) if isinstance(binding, TypeBinding)
                    else InstanceBinding(newset))
            ok = bool(bmap.ReInsert(definition, newb))
        except Exception:
            ok = False
        # Attempt 2: add Areas to the existing binding's category set in place.
        if not ok:
            try:
                binding.Categories.Insert(areas)
                ok = bool(bmap.ReInsert(definition, binding))
            except Exception:
                ok = False
        (bound if ok else failed).append(_pname(definition))

    if bound:
        report.note('Extended {} room parameter(s) to Areas: {}.'.format(
            len(bound), _preview(bound)))
    if failed:
        report.note('{} parameter(s) can\'t be rebound to Areas — the API only '
                    'rebinds SHARED parameters, and these are non-shared '
                    'project parameters: {}. They are bridged to "<name>_Areas" '
                    'instead (or tick Areas for them in Manage > Project '
                    'Parameters to keep the same name).'
                    .format(len(failed), _preview(failed)))
    if not pending:
        report.note('No Rooms-only project/shared parameters found to extend '
                    '(the rest already apply to Areas or are built-in).')
    return len(bound)


def _pname(definition):
    try:
        return definition.Name
    except Exception:
        return '?'


def _preview(names):
    names = sorted(names)
    head = ', '.join(names[:10])
    return head if len(names) <= 10 else head + ' (+{} more)'.format(len(names) - 10)
