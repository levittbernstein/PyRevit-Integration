# -*- coding: utf-8 -*-
"""
group_params — set parameter values on elements that live inside Revit groups.

The problem
-----------
Revit refuses parameter writes on a group member from outside Edit Group mode:

    "Changes to groups are allowed only in group edit mode."

This applies to the API exactly as it does to the Properties palette and to
schedule cells, which is why editing a non-itemised schedule row fails when any
of its elements are grouped.  The Revit API has no Edit Group mode, so the
restriction cannot be side-stepped directly.

What actually works
-------------------
Writes to a group member succeed when either:

  1. The parameter has "Values can vary by group instance" enabled.  The value
     is then per-instance rather than part of the group definition, so writing
     it is no longer a change to the group.
  2. The member's group type has exactly ONE instance in the model.  Revit
     permits the write because there is nothing to propagate to.

Enabling (1) is itself restricted: it needs an *instance* binding, and Revit
whitelists it by data type.  Length and Yes/No are excluded by design;
Text, Integer, Number, Area, Volume, Currency, URL and Material are commonly
allowed.  InternalDefinition.SetAllowVaryBetweenGroups() enforces the same
whitelist as the UI and throws ArgumentException rather than obliging.

Because none of this can be reliably predicted from parameter metadata alone,
probe.py determines it empirically against the live model inside a transaction
that is always rolled back, so a diagnostic never changes anything.

Module map
----------
probe.py    Read-only survey plus zero-risk rolled-back capability probes.
apply.py    Strategy selection and the actual write, with per-element reporting.
dialog.py   WPF front end (loads dialog.xaml).
"""
