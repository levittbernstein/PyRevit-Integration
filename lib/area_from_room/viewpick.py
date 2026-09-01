# -*- coding: utf-8 -*-
"""Group plan views for a selection dialog, matching the Project Browser.

Groups by the custom "View Type" project parameter (how the LB browser is
organised), falling back to the browser folder path, then the view's Type.
"""

from collections import OrderedDict

from Autodesk.Revit.DB import BrowserOrganization


def _view_type_value(view):
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


def _browser_group(doc, view):
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


def _type_group(doc, view):
    try:
        t = doc.GetElement(view.GetTypeId())
        if t is not None and t.Name:
            return t.Name
    except Exception:
        pass
    return 'Views'


def grouped(doc, views):
    """OrderedDict {group label: [views]} for forms.SelectFromList."""
    labelled = [(_view_type_value(v) or _browser_group(doc, v)
                 or _type_group(doc, v), v) for v in views]
    labelled.sort(key=lambda t: (t[0], t[1].Name))
    groups = OrderedDict()
    for label, v in labelled:
        groups.setdefault(label, []).append(v)
    return groups
