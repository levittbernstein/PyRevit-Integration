# -*- coding: utf-8 -*-
"""
Revit-side reading: locate the keynote file and snapshot every reference to a
keynote key.

Three separate parameters hold keynote keys, and missing any one of them leaves
the model desynced from the file:

    BuiltInParameter.KEYNOTE_PARAM   on element TYPES  (not instances)
    BuiltInParameter.KEYNOTE_PARAM   on MATERIALS      (same param — there is
                                                        no Material.Keynote)
    BuiltInParameter.KEY_VALUE       on keynote TAG instances (OST_KeynoteTags)

Tags store the key as a plain string with no ElementId or GUID link back to the
keynote table.  Change a key in the .txt and every tag holding the old string
is orphaned; it does not self-heal, not even after KeynoteTable.Reload().  That
is the entire reason this tool exists.
"""


# ── Robust Revit type resolution ──────────────────────────────────────────────
# IronPython sometimes cannot resolve a .NET type via 'from X import Y' when
# the importing module lives inside a Python package.  DataStorage bit us this
# way for several hours.  This helper tries the normal import first, then falls
# back to assembly reflection off a type that is always importable.

def _dbtype(name):
    """Return the Autodesk.Revit.DB type called *name*."""
    try:
        mod = __import__('Autodesk.Revit.DB', fromlist=[name])
        return getattr(mod, name)
    except (ImportError, AttributeError):
        pass

    import clr
    from Autodesk.Revit.DB import Transaction as _anchor
    t = clr.GetClrType(_anchor).Assembly.GetType('Autodesk.Revit.DB.' + name)
    if t is None:
        raise ImportError(
            'Cannot resolve Autodesk.Revit.DB.{} in this Revit version.'.format(name))
    return t


def _eid_int(element_id):
    """Integer of an ElementId — Revit 2024+ uses .Value, earlier .IntegerValue."""
    v = getattr(element_id, 'Value', None)
    if v is None:
        v = getattr(element_id, 'IntegerValue', None)
    return v


# ── Keynote file location ─────────────────────────────────────────────────────

def get_keynote_table(doc):
    """Return the document's KeynoteTable."""
    KeynoteTable = _dbtype('KeynoteTable')
    return KeynoteTable.GetKeynoteTable(doc)


def get_keynote_file_path(doc):
    """
    Resolve the keynote file path.

    Returns (path, kind) where kind is 'local', 'external' or None.
    Two branches are needed: a normal file reference exposes an absolute
    ModelPath, while a cloud/server-hosted file (BIM360/ACC) only exposes an
    in-session display path.
    """
    from Autodesk.Revit.DB import ModelPathUtils

    kt = get_keynote_table(doc)
    if kt is None:
        return None, None

    # Normal file reference — the common case for LB projects.
    try:
        if kt.IsExternalFileReference():
            ref = kt.GetExternalFileReference()
            return ModelPathUtils.ConvertModelPathToUserVisiblePath(
                ref.GetAbsolutePath()), 'local'
    except Exception:
        pass

    # Cloud / external resource server.
    try:
        if kt.RefersToExternalResourceReferences():
            refs = kt.GetExternalResourceReferences()
            try:
                items = dict(refs).items()
            except Exception:
                items = [(kvp.Key, kvp.Value) for kvp in refs]
            for _ref_type, ref in items:
                if ref is not None and ref.HasValidDisplayPath():
                    return ref.InSessionPath, 'external'
    except Exception:
        pass

    return None, None


def read_entries_from_table(doc):
    """
    Read keynote entries straight from the Revit API as {key: (text, parent)}.

    Used only to cross-check the parsed .txt — if the table and the file
    disagree, the file on disk has changed since Revit last loaded it and the
    user must reload before we touch anything.
    """
    kt = get_keynote_table(doc)
    if kt is None:
        return {}

    out = {}
    try:
        for e in kt.GetKeyBasedTreeEntries():
            out[e.Key] = (e.KeynoteText, e.ParentKey or u'')
    except Exception:
        return {}
    return out


# ── Reference snapshot ────────────────────────────────────────────────────────

class KeyRef(object):
    """One parameter on one element that holds a keynote key."""

    __slots__ = ('element_id', 'kind', 'key', 'label')

    def __init__(self, element_id, kind, key, label):
        self.element_id = element_id   # ElementId
        self.kind       = kind         # 'tag' | 'type' | 'material'
        self.key        = key          # current key string
        self.label      = label        # human-readable, for the report


def _param_of(element, kind):
    """Return the keynote parameter for *element*, or None if absent."""
    from Autodesk.Revit.DB import BuiltInParameter

    bip = (BuiltInParameter.KEY_VALUE if kind == 'tag'
           else BuiltInParameter.KEYNOTE_PARAM)
    try:
        return element.get_Parameter(bip)
    except Exception:
        return None


def _safe_name(element):
    try:
        n = element.Name
        return n if n else '<unnamed>'
    except Exception:
        return '<unnamed>'


def snapshot_references(doc):
    """
    Collect every model reference to a keynote key.

    MUST be called before any key is written.  Snapshotting first is what makes
    a permutation safe: if we re-queried mid-update, elements already moved to
    their new key would be picked up again by a later old-key lookup.  Because
    we hold ElementIds up front and rewrite the whole file in one pass, the
    two-phase sentinel dance pyRevit needs is unnecessary here.

    Returns (refs_by_key, stats) where refs_by_key maps key -> [KeyRef].
    """
    from Autodesk.Revit.DB import (
        FilteredElementCollector, BuiltInCategory,
    )
    Material = _dbtype('Material')

    refs_by_key = {}
    stats       = {'tag': 0, 'type': 0, 'material': 0, 'no_param': 0}

    def _add(element, kind):
        p = _param_of(element, kind)
        if p is None:
            # Tags created via IndependentTag.Create() can lack KEY_VALUE
            # entirely — only UI-created keynote tags reliably have it.
            stats['no_param'] += 1
            return
        try:
            key = p.AsString()
        except Exception:
            return
        if not key or not key.strip():
            return
        key = key.strip()

        refs_by_key.setdefault(key, []).append(
            KeyRef(element.Id, kind, key, _safe_name(element)))
        stats[kind] += 1

    # Keynote tag instances.
    for tag in (FilteredElementCollector(doc)
                .OfCategory(BuiltInCategory.OST_KeynoteTags)
                .WhereElementIsNotElementType()
                .ToElements()):
        _add(tag, 'tag')

    # Element types.  Keynote is a TYPE parameter, never an instance one.
    for et in (FilteredElementCollector(doc)
               .WhereElementIsElementType()
               .ToElements()):
        _add(et, 'type')

    # Materials are not ElementTypes, so they need their own collector.
    for mat in (FilteredElementCollector(doc)
                .OfClass(Material)
                .ToElements()):
        _add(mat, 'material')

    return refs_by_key, stats


def find_orphans(refs_by_key, valid_keys):
    """Keys referenced in the model that do not exist in the keynote file."""
    return sorted(k for k in refs_by_key if k not in valid_keys)


def has_linked_models(doc):
    """
    True when the document contains Revit links.

    Tags inside linked models hold their own key strings and cannot be updated
    from this session — the user must be warned and given the audit log.
    """
    from Autodesk.Revit.DB import FilteredElementCollector
    RevitLinkInstance = _dbtype('RevitLinkInstance')
    try:
        return (FilteredElementCollector(doc)
                .OfClass(RevitLinkInstance)
                .GetElementCount()) > 0
    except Exception:
        return False
