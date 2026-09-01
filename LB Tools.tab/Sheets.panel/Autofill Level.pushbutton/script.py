# -*- coding: utf-8 -*-
"""
LB Sheet Level Codes — pyRevit push-button script.

Fills each sheet's "Level" parameter (the BIM drawing-numbering field) from the
views placed on it:

    00 / 01 / 02 …   single level, above ground
    99 / 98 / 97 …   single level, below ground
    ZZ               spans several levels (section/elevation/3D, or plans on
                     two or more levels)
    XX               no level content at all (schedules / drafting only)

Legends are ignored. Schedules only make a sheet "XX" when nothing else is on
it — a schedule sharing a sheet with a real drawing is ignored. Anything the
rules can't resolve is left blank and the sheet is listed in the summary for a
human to sort out.

Revit doesn't store a two-digit level code, so on each run a dialog lists every
Level with an editable code (best-guessed from the level name). The map is saved
in the model, so it is really only entered once and re-runs are one click.

Runs under IronPython (pyRevit default engine) — no f-strings.

IMPORTANT: never call sys.exit() after a committed Transaction — pyRevit treats
SystemExit as a signal to roll the transaction back.
"""

import os
import sys

from pyrevit import revit, DB, forms, script

# ── Add the extension lib folder to the path ───────────────────────────────────
_EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_EXT_LIB = os.path.join(_EXT_ROOT, 'lib')
if _EXT_LIB not in sys.path:
    sys.path.insert(0, _EXT_LIB)

from level_code import classify, autocode, storage    # noqa: E402
from level_code.dialog import LevelCodeDialog          # noqa: E402


PARAM_NAME = 'Level'

doc = revit.doc
output = script.get_output()


def _sheet_infos(sheet):
    """Every (category, payload) on a sheet: graphical views + schedules."""
    infos = []
    for vp_id in sheet.GetAllViewports():
        vp = doc.GetElement(vp_id)
        if vp is None:
            continue
        view = doc.GetElement(vp.ViewId)
        if view is not None:
            infos.append(classify.classify_view(view))
    # Schedules are placed as ScheduleSheetInstance, not viewports. The
    # titleblock's own revision schedule is not a drawing — skip it.
    for si in (DB.FilteredElementCollector(doc, sheet.Id)
                 .OfClass(DB.ScheduleSheetInstance)):
        try:
            if si.IsTitleblockRevisionSchedule:
                continue
        except Exception:
            pass
        infos.append((classify.XX, None))
    return infos


if doc is None or doc.IsFamilyDocument:
    forms.alert('Please open a Revit project first.',
                title='No project open', warn_icon=True)

else:
    levels = list(DB.FilteredElementCollector(doc)
                    .OfClass(DB.Level).WhereElementIsNotElementType())
    levels.sort(key=lambda lv: lv.Elevation)

    if not levels:
        forms.alert('This model has no levels.', title='Nothing to do',
                    warn_icon=True)
    else:
        # ── Level -> code dialog (seeded from the saved map, else guesses) ──
        saved = storage.load_map(doc)
        rows = []
        for lvl in levels:
            uid = lvl.UniqueId
            code = saved.get(uid)
            if code is None:                       # unseen level -> guess it
                code = autocode.guess_code(lvl.Name)
            rows.append((uid, lvl.Name, code))

        ok, code_map = LevelCodeDialog(rows).show()

        if ok:
            def code_for_level(level):
                if level is None:
                    return ''
                return code_map.get(level.UniqueId, '')

            sheets = (DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewSheet)
                        .WhereElementIsNotElementType().ToElements())

            to_write = []        # (sheet, code)
            unchanged = 0
            flagged = []         # (sheet, reason)
            missing_param = []   # sheet numbers with no "Level" parameter
            not_writable = []    # sheet numbers where "Level" isn't writable text

            for sheet in sheets:
                if sheet.IsPlaceholder:
                    continue

                code, reason = classify.resolve_sheet(
                    _sheet_infos(sheet), code_for_level)
                if code is None:
                    flagged.append((sheet, reason))
                    continue

                p = sheet.LookupParameter(PARAM_NAME)
                if p is None:
                    missing_param.append(sheet.SheetNumber)
                    continue
                if p.IsReadOnly or p.StorageType != DB.StorageType.String:
                    not_writable.append(sheet.SheetNumber)
                    continue

                if (p.AsString() or '') == code:
                    unchanged += 1
                else:
                    to_write.append((sheet, code))

            # ── Write the sheet codes ──────────────────────────────────────
            written = 0
            failed = []
            if to_write:
                with revit.Transaction('Set sheet level codes'):
                    for sheet, code in to_write:
                        p = sheet.LookupParameter(PARAM_NAME)
                        try:
                            if p.Set(code):
                                written += 1
                            else:
                                failed.append(str(sheet.SheetNumber))
                        except Exception as ex:
                            failed.append('{} ({})'.format(sheet.SheetNumber, ex))

            # ── Persist the level map (best-effort, kept separate so a
            #    checkout clash on ProjectInformation can't undo the writes) ──
            map_note = ''
            try:
                with revit.Transaction('Save level code map'):
                    storage.save_map(doc, code_map)
            except Exception as ex:
                map_note = ('Could not save the level-code map to the model '
                            '({}). The codes were still applied this run; '
                            'you may need to re-enter them next time.'.format(ex))

            # ── Summary "textbox" ──────────────────────────────────────────
            out = ['# Autofill Level', '',
                   '- Updated: **{}**'.format(written),
                   '- Already correct: {}'.format(unchanged),
                   '- Flagged (left blank): **{}**'.format(len(flagged))]
            if missing_param:
                out.append('- Missing "{}" parameter: {}'.format(
                    PARAM_NAME, len(missing_param)))
            if not_writable:
                out.append('- "{}" not writable text: {}'.format(
                    PARAM_NAME, len(not_writable)))
            if failed:
                out.append('- **Failed writes: {}** ({})'.format(
                    len(failed), ', '.join(failed)))
            if map_note:
                out += ['', '> ' + map_note]

            if flagged:
                out += ['', '## Flagged sheets — resolve manually', '',
                        '| Sheet | Name | Reason |', '|---|---|---|']
                for sheet, reason in sorted(
                        flagged, key=lambda s: s[0].SheetNumber):
                    out.append('| {} | {} | {} |'.format(
                        sheet.SheetNumber, sheet.Name, reason))

            if missing_param:
                out += ['',
                        'Sheets missing a writable text "{}" parameter: {}.'
                        .format(PARAM_NAME, ', '.join(missing_param)),
                        '',
                        'Add it via Manage > Project Parameters (category '
                        'Sheets, Instance) and run again.']

            output.print_md('\n'.join(out))

# No sys.exit() after a committed transaction — see the module docstring.
