# -*- coding: utf-8 -*-
"""
Pure classification logic for the Sheet Level Codes tool.

Kept free of any UI so the rules can be reasoned about (and unit-tested) on
their own. Two stages:

    classify_view(view)            -> one view's category
    resolve_sheet(infos, code_of)  -> the sheet's single level code, or a flag

Categories
----------
    NUM      a plan tied to one building level  -> contributes that level's code
    ZZ       a view that spans levels (section, elevation, 3D)
    XX       content with no level (schedule, drafting view, detail)
    IGNORE   legend -> never counted
    UNKNOWN  a view type we can't judge -> forces the sheet to be flagged

Sheet resolution (after your revised rules)
-------------------------------------------
    * Legends are ignored entirely.
    * XX content (schedules/drafting/details) is IGNORED whenever the sheet
      also has a real level-bearing view. It only yields "XX" when it is the
      only thing on the sheet.
    * Any level-spanning view, or two+ different numbered levels -> "ZZ".
    * Exactly one numbered level -> that code.
    * Anything unresolvable (unknown view type, a plan whose level has no code
      set, an uncoded level mixed with a known one) -> (None, reason) so the
      sheet is left blank and flagged.
"""

from Autodesk.Revit.DB import ViewType

NUM = 'num'
ZZ = 'zz'
XX = 'xx'
IGNORE = 'ignore'
UNKNOWN = 'unknown'

# Plan views carry a single GenLevel -> a numbered code.
_PLAN_TYPES = (ViewType.FloorPlan, ViewType.CeilingPlan,
               ViewType.EngineeringPlan, ViewType.AreaPlan)
# Views that inherently show more than one level.
_ZZ_TYPES = (ViewType.Section, ViewType.Elevation, ViewType.ThreeD)
# Graphical views with no level association.
_XX_TYPES = (ViewType.Detail, ViewType.DraftingView)


def classify_view(view):
    """Return (category, payload).

    payload is the Revit Level for a plan (NUM), a short description string for
    UNKNOWN, and None otherwise.
    """
    vt = view.ViewType
    if vt in _PLAN_TYPES:
        return (NUM, getattr(view, 'GenLevel', None))
    if vt in _ZZ_TYPES:
        return (ZZ, None)
    if vt in _XX_TYPES:
        return (XX, None)
    if vt == ViewType.Legend:
        return (IGNORE, None)
    if vt == ViewType.Schedule:
        # Schedules normally arrive as ScheduleSheetInstance, but a schedule
        # reached through a viewport lands here too.
        return (XX, None)
    return (UNKNOWN, str(vt))


def resolve_sheet(infos, code_for_level):
    """Collapse a sheet's views into one level code.

    infos           : list of (category, payload) from classify_view (+ any
                      schedule instances added by the caller as (XX, None)).
    code_for_level  : callable(level) -> code string ('' when unset / None).

    Returns (code, None) on success or (None, reason) when the sheet should be
    left blank and flagged.
    """
    numbered = set()     # distinct, non-empty codes seen
    uncoded = set()      # names of levels present but with no code set
    unknown = set()      # reasons a view could not be judged
    has_zz = False
    has_xx = False

    for category, payload in infos:
        if category == IGNORE:
            continue
        elif category == ZZ:
            has_zz = True
        elif category == XX:
            has_xx = True
        elif category == NUM:
            level = payload
            if level is None:
                unknown.add('plan view with no associated level')
            else:
                code = (code_for_level(level) or '').strip()
                if code:
                    numbered.add(code)
                else:
                    uncoded.add(level.Name)
        elif category == UNKNOWN:
            unknown.add('unsupported view type: {}'.format(payload))

    # Unknown view types are never safe to guess past.
    if unknown:
        return (None, '; '.join(sorted(unknown)))

    # A level-spanning view makes the whole sheet multi-level regardless of
    # what else is on it, so decide that before worrying about uncoded plans.
    if has_zz:
        return ('ZZ', None)

    # A plan on a level with no code set can't be resolved (and might be a
    # second, different level) -> flag rather than guess.
    if uncoded:
        return (None, 'no level code set for: ' + ', '.join(sorted(uncoded)))

    if len(numbered) >= 2:
        return ('ZZ', None)
    if len(numbered) == 1:
        return (next(iter(numbered)), None)

    # No level-bearing views at all.
    if has_xx:
        return ('XX', None)

    return (None, 'no level-bearing views (legend-only or empty sheet)')
