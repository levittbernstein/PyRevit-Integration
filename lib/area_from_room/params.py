# -*- coding: utf-8 -*-
"""Copy parameter values from a Room to its mirrored Area.

Matched by shared-parameter GUID where possible (robust to renames), else by
name. Skipped:
  * read-only / computed params (Area, Perimeter, Level, …);
  * params with mismatched storage type;
  * ElementId params — a value pointing at a Room-side element (e.g. a key row)
    does not translate to the Area. Key values are handled by keyschedule.py.
"""

from Autodesk.Revit.DB import StorageType


def _shared_guid(param):
    try:
        if param.IsShared:
            return param.GUID.ToString()
    except Exception:
        pass
    return None


def _index(element):
    """{('g', guid): p} and {('n', name): p} for an element's parameters."""
    out = {}
    for p in element.Parameters:
        g = _shared_guid(p)
        if g is not None:
            out[('g', g)] = p
        try:
            out.setdefault(('n', p.Definition.Name), p)
        except Exception:
            pass
    return out


def copy_parameters(src, dst, skip_names=None):
    """Copy every writable, matching, value-typed parameter src -> dst.

    Returns (copied_count, skipped_name_list).
    """
    skip = set(skip_names or [])
    dst_index = _index(dst)
    copied = 0
    skipped = []

    for p in src.Parameters:
        if p is None:
            continue
        try:
            name = p.Definition.Name
        except Exception:
            continue
        if name in skip or not p.HasValue:
            continue

        target = None
        g = _shared_guid(p)
        if g is not None:
            target = dst_index.get(('g', g))
        if target is None:
            target = dst_index.get(('n', name))
        if target is None or target.IsReadOnly:
            continue
        if target.StorageType != p.StorageType:
            continue

        try:
            st = p.StorageType
            if st == StorageType.Double:
                target.Set(p.AsDouble())
            elif st == StorageType.Integer:
                target.Set(p.AsInteger())
            elif st == StorageType.String:
                target.Set(p.AsString() or '')
            else:
                # ElementId / None -> not portable, skip (see module docstring).
                skipped.append(name)
                continue
            copied += 1
        except Exception:
            skipped.append(name)

    return copied, skipped
