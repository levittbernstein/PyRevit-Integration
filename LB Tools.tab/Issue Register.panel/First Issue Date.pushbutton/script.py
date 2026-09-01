# -*- coding: utf-8 -*-
"""
LB First Issue Date — pyRevit push-button script.

Writes each sheet's FIRST revision date into the sheet parameter that the
titleblock's "first issue" label is bound to (here: "Sheet Issue Date").

Why this tool exists
--------------------
Revit's built-in "Current Revision Date" always tracks the LATEST revision on a
sheet, and the rows of the revision schedule aren't addressable as titleblock
labels, so there is no native way to show the FIRST issue date. This tool fills
that gap: it reads each sheet's revision list, takes the earliest revision, and
copies its date string into the shared sheet parameter the label points at.

"First" is decided by revision SEQUENCE, not by parsing the date text —
GetAllRevisionIds() returns a sheet's revisions in sequence order, so index [0]
is always the first revision issued on that sheet. The date itself is stored
verbatim (Revit revision dates are free text), so whatever was typed in the
Sheet Issues/Revisions dialog round-trips exactly.

Re-runnable and idempotent: run it again whenever sheets are added or (rarely) a
first revision changes. It only writes sheets whose value is actually out of
date, so a second run with nothing to do is harmless and reports "all up to
date".

Runs under IronPython (pyRevit default engine) — no f-strings.

IMPORTANT: never call sys.exit() after a committed Transaction — pyRevit treats
SystemExit as a signal to roll the transaction back.
"""

from pyrevit import revit, DB, forms, script


# The sheet parameter the titleblock's first-issue label is bound to.
PARAM_NAME = "Sheet Issue Date"

# What to write when a sheet has no revisions yet (not issued). A dash reads
# cleanly on a printed titleblock, where "null" would look like an error.
# Change to "TBC", "Not issued", "" (blank), etc. to taste.
UNISSUED_TEXT = "-"

doc = revit.doc
output = script.get_output()


def _first_revision_date(sheet):
    """Date string of the sheet's first (lowest-sequence) revision, or None
    when the sheet carries no revisions yet."""
    rev_ids = sheet.GetAllRevisionIds()   # ordered by revision sequence
    if rev_ids is None or rev_ids.Count == 0:
        return None
    first_rev = doc.GetElement(rev_ids[0])
    return first_rev.RevisionDate or ""


if doc is None or doc.IsFamilyDocument:
    forms.alert("Please open a Revit project first.",
                title="No project open", warn_icon=True)

else:
    sheets = (DB.FilteredElementCollector(doc)
                .OfClass(DB.ViewSheet)
                .WhereElementIsNotElementType()
                .ToElements())

    to_write = []        # (sheet, new_value)
    unchanged = 0
    unissued = 0         # sheets with no revisions — get UNISSUED_TEXT
    missing_param = []   # sheet numbers with no such parameter
    not_writable = []    # sheet numbers where the param is read-only / not text

    for sheet in sheets:
        if sheet.IsPlaceholder:
            continue

        date_str = _first_revision_date(sheet)
        if date_str is None:
            date_str = UNISSUED_TEXT      # not issued yet
            unissued += 1

        p = sheet.LookupParameter(PARAM_NAME)
        if p is None:
            missing_param.append(sheet.SheetNumber)
            continue
        if p.IsReadOnly or p.StorageType != DB.StorageType.String:
            not_writable.append(sheet.SheetNumber)
            continue

        if (p.AsString() or "") == date_str:
            unchanged += 1
        else:
            to_write.append((sheet, date_str))

    if not sheets:
        forms.alert("No sheets in this model.", title="Nothing to do")

    elif missing_param and not to_write and unchanged == 0 and not not_writable:
        # The parameter isn't on any sheet — the setup step hasn't been done.
        forms.alert(
            'None of the sheets have a writable text parameter called "{}".\n\n'
            "Add it as a project parameter (Manage > Project Parameters, "
            "category Sheets, Instance) using the same shared parameter the "
            "titleblock label points at, then run this again.".format(PARAM_NAME),
            title="Parameter not found", warn_icon=True)

    else:
        summary = [
            "Sheets to update:   {}".format(len(to_write)),
            "Already correct:    {}".format(unchanged),
            'Not issued (set to "{}"):   {}'.format(UNISSUED_TEXT, unissued),
        ]
        if missing_param:
            summary.append('Missing "{}":  {}'.format(PARAM_NAME, len(missing_param)))
        if not_writable:
            summary.append("Not writable text:  {}".format(len(not_writable)))

        if not to_write:
            summary.append("")
            summary.append("Nothing to write — all sheets are already up to date.")
            forms.alert("\n".join(summary), title="First Issue Date")
        else:
            summary.append("")
            summary.append('Write the first revision date into "{}" on the {} '
                           "sheet(s) above?".format(PARAM_NAME, len(to_write)))

            if forms.alert("\n".join(summary), title="Populate First Issue Date",
                           ok=False, yes=True, no=True):
                written = 0
                failed = []
                with revit.Transaction("Populate First Issue Date"):
                    for sheet, value in to_write:
                        p = sheet.LookupParameter(PARAM_NAME)
                        try:
                            if p.Set(value):
                                written += 1
                            else:
                                failed.append(str(sheet.SheetNumber))
                        except Exception as ex:
                            failed.append("{} ({})".format(sheet.SheetNumber, ex))

                out = ["**First Issue Date — done**", "",
                       "- Updated: **{}**".format(written),
                       "- Already correct: {}".format(unchanged),
                       '- Not issued (set to "{}"): {}'.format(UNISSUED_TEXT, unissued)]
                if missing_param:
                    out.append('- Missing "{}": {} ({})'.format(
                        PARAM_NAME, len(missing_param), ", ".join(missing_param)))
                if not_writable:
                    out.append("- Not writable text: {} ({})".format(
                        len(not_writable), ", ".join(not_writable)))
                if failed:
                    out.append("- **Failed: {}** ({})".format(
                        len(failed), ", ".join(failed)))
                output.print_md("\n".join(out))

# No sys.exit() after a committed transaction — see the module docstring.
