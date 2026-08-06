# -*- coding: utf-8 -*-
"""
The category model and old->new key computation.

Categories are stored as native Revit parent rows: a parentless entry whose key
IS the prefix.  A category keyed 'R' with text 'Railings' and children pointing
at parent 'R' produces keys R01, R02, ... and Revit's own keynote browser shows
a proper tree without needing this tool.

    R    Railings
    R01  Railing Type A ...    parent R
    R02  Railing Type B ...    parent R

Two numbering modes:

    prefix  child key = <category key><zero-padded position>   R01, R02
    flat    child key = <zero-padded position> across the whole
            file, ignoring category                            01, 02

Flat mode still writes the category rows and parent pointers, so the hierarchy
survives; only the numbers are simple.  That is what makes the prefix toggle
non-destructive — switching modes renumbers, it never loses structure.
"""

import re


DEFAULT_PADDING = 2

# A category key must sort and read sensibly as a prefix, and must not collide
# with generated numeric keys.  Letters only keeps 'R' + '01' unambiguous.
_CATEGORY_KEY_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_.\-]{0,7}$')


class Category(object):
    """A parent row plus its ordered children."""

    def __init__(self, key, text):
        self.key      = key
        self.text     = text
        self.children = []   # ordered list of KeynoteEntry

    def __repr__(self):
        return 'Category({!r}, {!r}, {} children)'.format(
            self.key, self.text, len(self.children))


class KeynoteModel(object):
    """
    Mutable ordered view of a keynote file that the dialog edits.

    Holds categories (each with ordered children) plus a list of uncategorised
    entries.  Nothing here touches Revit or the filesystem — it is pure data so
    it can be unit-tested and so a dry run costs nothing.
    """

    def __init__(self, entries):
        self.categories    = []
        self.uncategorised = []
        self._merged       = {}   # dropped_key -> surviving_key
        self._added        = []   # keys of entries created in this session
        self._load(entries)

    # ── Construction ──────────────────────────────────────────────────────────

    def _load(self, entries):
        by_key   = dict((e.key, e) for e in entries)
        children = {}

        # Every key that has ever existed in this file, so a new keynote never
        # reuses one that has been vacated by a merge or a renumber.  Reusing a
        # freed key is actively dangerous: a tag in a LINKED model still holding
        # the old string would silently resolve to unrelated text, and wrong
        # keynote text is worse than a visibly missing one.  Gaps are harmless.
        self._historic = set(by_key.keys())

        for e in entries:
            if e.parent and e.parent in by_key:
                children.setdefault(e.parent, []).append(e)

        # A parentless entry is a category only if something points at it.
        # In a flat file (LB's current state) nothing does, so every entry
        # starts life uncategorised — which is the correct starting point.
        for e in entries:
            if e.parent:
                continue
            if e.key in children:
                cat = Category(e.key, e.text)
                cat.children = list(children[e.key])
                self.categories.append(cat)
            else:
                self.uncategorised.append(e)

        # Entries whose declared parent does not exist would vanish from the
        # tree, so surface them as uncategorised rather than silently dropping.
        for e in entries:
            if e.parent and e.parent not in by_key:
                e.parent = u''
                self.uncategorised.append(e)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def all_entries(self):
        """Every surviving keynote entry (excludes category rows)."""
        out = []
        for cat in self.categories:
            out.extend(cat.children)
        out.extend(self.uncategorised)
        return out

    def find_category(self, key):
        for cat in self.categories:
            if cat.key == key:
                return cat
        return None

    def _owner_of(self, entry):
        """Return the list currently holding *entry*."""
        for cat in self.categories:
            if entry in cat.children:
                return cat.children
        if entry in self.uncategorised:
            return self.uncategorised
        return None

    def entry_by_key(self, key):
        for e in self.all_entries():
            if e.key == key:
                return e
        return None

    # ── Category operations ───────────────────────────────────────────────────

    def validate_category_key(self, key):
        """Return an error string, or None if *key* is usable as a prefix."""
        key = (key or u'').strip()
        if not key:
            return 'Category key cannot be empty.'
        if not _CATEGORY_KEY_RE.match(key):
            return ('Category key "{}" is invalid. Use 1-8 characters starting '
                    'with a letter (e.g. R, W, DR).'.format(key))
        if self.find_category(key):
            return 'A category with key "{}" already exists.'.format(key)
        if self.entry_by_key(key):
            return ('Key "{}" is already used by a keynote. Categories and '
                    'keynotes cannot share a key.'.format(key))
        return None

    def add_category(self, key, text):
        err = self.validate_category_key(key)
        if err:
            raise ValueError(err)
        cat = Category(key.strip(), (text or u'').strip())
        self.categories.append(cat)
        return cat

    def remove_category(self, key):
        """Delete a category, returning its children to uncategorised."""
        cat = self.find_category(key)
        if cat is None:
            return
        for child in cat.children:
            child.parent = u''
            self.uncategorised.append(child)
        self.categories.remove(cat)

    def rename_category(self, key, new_text):
        cat = self.find_category(key)
        if cat is not None:
            cat.text = (new_text or u'').strip()

    def move_category(self, key, delta):
        """Reorder a category among its peers. Affects file order and, in flat
        mode, the numbering sequence."""
        cat = self.find_category(key)
        if cat is None:
            return False
        i = self.categories.index(cat)
        j = i + delta
        if j < 0 or j >= len(self.categories):
            return False
        self.categories[i], self.categories[j] = self.categories[j], self.categories[i]
        return True

    # ── Creating entries ──────────────────────────────────────────────────────

    def next_free_key(self, category_key=None, padding=DEFAULT_PADDING):
        """
        Lowest unused key in the relevant namespace.

        Inside a category the key is prefixed with the category key, so a new
        railing lands on R07 rather than colliding with the flat sequence.
        Keys retired earlier in the session are excluded, so gaps left by
        merges and renumbers are never reused — see _load() for why.
        """
        used = set(e.key for e in self.all_entries())
        used |= set(c.key for c in self.categories)
        used |= self._historic   # never resurrect a retired key — see _load()

        cat = self.find_category(category_key) if category_key else None
        prefix = cat.key if cat is not None else u''

        n = 1
        while True:
            candidate = u'{}{}'.format(prefix, str(n).zfill(padding))
            if candidate not in used:
                return candidate
            n += 1

    def add_entry(self, text, category_key=None, padding=DEFAULT_PADDING,
                  key=None):
        """
        Create a brand-new keynote.

        The key is real and collision-free from the moment of creation, not a
        placeholder.  That matters because an uncategorised addition with
        renumbering off is never passed through compute_keys(), so whatever key
        it is given here is the key that reaches the file.
        """
        text = (text or u'').strip()
        if not text:
            raise ValueError('Keynote text cannot be empty.')
        if u'\t' in text:
            raise ValueError('Keynote text cannot contain a tab character.')

        if key is None:
            key = self.next_free_key(category_key, padding)
        else:
            key = key.strip()
            err = self.validate_entry_key(key)
            if err:
                raise ValueError(err)

        cat = self.find_category(category_key) if category_key else None
        if category_key and cat is None:
            raise ValueError('No such category: {}'.format(category_key))

        from keynote_manager.keynote_file import KeynoteEntry
        entry = KeynoteEntry(key, text, cat.key if cat is not None else u'')

        if cat is not None:
            cat.children.append(entry)
        else:
            self.uncategorised.append(entry)

        self._added.append(key)
        self._historic.add(key)
        return entry

    def validate_entry_key(self, key):
        """Return an error string, or None if *key* is free and well-formed."""
        key = (key or u'').strip()
        if not key:
            return 'Keynote key cannot be empty.'
        if u'\t' in key:
            return 'Keynote key cannot contain a tab character.'
        if self.entry_by_key(key):
            return 'Key "{}" is already used by another keynote.'.format(key)
        if self.find_category(key):
            return 'Key "{}" is already used by a category.'.format(key)
        return None

    @property
    def added(self):
        """Keys of entries created this session, in creation order."""
        return list(self._added)

    def has_changes(self, key_map):
        """
        True when there is anything to write.

        New keynotes produce no old->new mapping, so a key_map-only test would
        report 'nothing to do' and silently drop them.
        """
        return bool(key_map) or bool(self._added)

    # ── Entry operations ──────────────────────────────────────────────────────

    def assign(self, entry_keys, category_key):
        """
        Move entries into *category_key*, or to uncategorised when it is None.

        Appends in the order given so a multi-select assignment lands
        predictably rather than in collector order.
        """
        target = None
        if category_key is not None:
            target = self.find_category(category_key)
            if target is None:
                raise ValueError('No such category: {}'.format(category_key))

        for key in entry_keys:
            entry = self.entry_by_key(key)
            if entry is None:
                continue
            owner = self._owner_of(entry)
            if owner is not None:
                owner.remove(entry)
            if target is None:
                entry.parent = u''
                self.uncategorised.append(entry)
            else:
                entry.parent = target.key
                target.children.append(entry)

    def move_to(self, entry_key, category_key, index=None):
        """
        Move one entry into *category_key* (None = uncategorised) at *index*.

        This is what drag-and-drop calls: dropping onto another keynote inserts
        at that keynote's position, dropping onto a category header appends to
        that category.  Doing the list surgery here rather than in the dialog
        keeps the ordering rules in one testable place.
        """
        entry = self.entry_by_key(entry_key)
        if entry is None:
            return False

        target = (self.uncategorised if category_key is None
                  else self.find_category(category_key))
        if category_key is not None and target is None:
            return False
        dest = self.uncategorised if category_key is None else target.children

        owner = self._owner_of(entry)
        if owner is None:
            return False

        # Semantics: the entry ends up AT *index* in the resulting list.
        # Because the entry is removed first, this gives the conventional
        # nearest-gap behaviour without any special-casing — dragging down onto
        # a target lands after it, dragging up onto a target lands before it.
        # Deliberately no correction for the removal shift: adding one makes
        # downward drags land before the target, which reads as the item not
        # having moved far enough.
        owner.remove(entry)
        if index is None:
            index = len(dest)
        index = max(0, min(index, len(dest)))
        entry.parent = u'' if category_key is None else category_key
        dest.insert(index, entry)
        return True

    def move_entry(self, key, delta):
        """Reorder an entry within its own group."""
        entry = self.entry_by_key(key)
        if entry is None:
            return False
        owner = self._owner_of(entry)
        if owner is None:
            return False
        i = owner.index(entry)
        j = i + delta
        if j < 0 or j >= len(owner):
            return False
        owner[i], owner[j] = owner[j], owner[i]
        return True

    def sort_group(self, category_key, by='text'):
        """Alphabetise one group — handy before an auto-renumber."""
        owner = (self.uncategorised if category_key is None
                 else (self.find_category(category_key) or Category('', '')).children)
        owner.sort(key=lambda e: (e.text or u'').lower() if by == 'text'
                   else _natural_key(e.key))

    # ── Duplicates ────────────────────────────────────────────────────────────

    def find_duplicate_text(self):
        """
        Group surviving entries by normalised text.

        Returns [(text, [entries])] for every text used by more than one key.
        LB's 4076 file has 'Railing Type F' as both 39 and 45 — two keys for one
        material means tags in different views may disagree about which key
        describes it.
        """
        groups = {}
        for e in self.all_entries():
            norm = u' '.join((e.text or u'').split()).lower()
            if norm:
                groups.setdefault(norm, []).append(e)

        return [(entries[0].text, entries)
                for _norm, entries in sorted(groups.items())
                if len(entries) > 1]

    def merge(self, keep_key, drop_keys):
        """
        Consolidate duplicates onto *keep_key*.

        The dropped entries leave the file, and compute_keys() maps their old
        keys onto the surviving entry's new key so every model reference is
        repointed rather than orphaned.
        """
        keeper = self.entry_by_key(keep_key)
        if keeper is None:
            raise ValueError('No such keynote: {}'.format(keep_key))

        for key in drop_keys:
            if key == keep_key:
                continue
            entry = self.entry_by_key(key)
            if entry is None:
                continue
            owner = self._owner_of(entry)
            if owner is not None:
                owner.remove(entry)
            self._merged[key] = keep_key

    @property
    def merged(self):
        return dict(self._merged)

    # ── Key computation ───────────────────────────────────────────────────────

    def compute_keys(self, use_prefix=True, padding=DEFAULT_PADDING,
                     renumber_uncategorised=False):
        """
        Compute the old->new key map.

        Only genuinely changed keys appear in the map, so an unchanged file
        produces an empty map and the Update button has nothing to do.
        Merged keys map onto their survivor's new key.

        renumber_uncategorised defaults to False deliberately.  Pulling six
        railings into an 'R' category would otherwise cascade a renumber
        through every remaining keynote to close the vacated gaps, rewriting
        tags on twenty unrelated entries for no benefit.  Gaps in the
        uncategorised sequence are harmless — Revit does not care.

        In flat mode there is no prefix to separate the namespaces, so a full
        sequential renumber is the only coherent option and the flag is forced.
        """
        if not use_prefix:
            renumber_uncategorised = True

        new_of  = {}
        counter = 0

        for cat in self.categories:
            if use_prefix:
                n = 0
                for child in cat.children:
                    n += 1
                    new_of[child.key] = u'{}{}'.format(
                        cat.key, str(n).zfill(padding))
            else:
                for child in cat.children:
                    counter += 1
                    new_of[child.key] = _padded(counter, padding)

        if renumber_uncategorised:
            for entry in self.uncategorised:
                counter += 1
                new_of[entry.key] = _padded(counter, padding)

        # Merged keys inherit their survivor's new key.  A dropped duplicate
        # whose survivor is not being renumbered still has to repoint onto the
        # survivor's existing key, so fall back to the survivor itself.
        for dropped, survivor in self._merged.items():
            new_of[dropped] = new_of.get(survivor, survivor)

        # Drop no-ops so the report only lists real changes.
        return dict((old, new) for old, new in new_of.items() if old != new)

    def validate_new_keys(self, key_map):
        """
        Check a computed map before anything is written.

        Returns a list of blocking problems.  Catches the case where a generated
        key collides with a category key, or where two entries would end up
        sharing a key.
        """
        problems = []

        final = {}
        for entry in self.all_entries():
            final[entry.key] = key_map.get(entry.key, entry.key)

        seen = {}
        for old, new in sorted(final.items()):
            if new in seen:
                problems.append(
                    'Key collision: "{}" and "{}" would both become "{}".'
                    .format(seen[new], old, new))
            seen[new] = old

        cat_keys = set(c.key for c in self.categories)
        for old, new in sorted(final.items()):
            if new in cat_keys:
                problems.append(
                    'Keynote "{}" would become "{}", which is a category key.'
                    .format(old, new))

        for new in sorted(seen):
            if not new or u'\t' in new:
                problems.append('Invalid generated key: {!r}'.format(new))

        return problems

    # ── Output ────────────────────────────────────────────────────────────────

    def to_entries(self, key_map):
        """
        Flatten to the ordered KeynoteEntry list to write to disk.

        Category rows are emitted immediately before their children.  Revit
        ignores file order entirely, but a human opening the .txt should be able
        to read it.
        """
        out = []
        for cat in self.categories:
            out.append(_mk(cat.key, cat.text, u''))
            for child in cat.children:
                out.append(_mk(key_map.get(child.key, child.key),
                               child.text, cat.key))
        for entry in self.uncategorised:
            out.append(_mk(key_map.get(entry.key, entry.key), entry.text, u''))
        return out


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mk(key, text, parent):
    from keynote_manager.keynote_file import KeynoteEntry
    return KeynoteEntry(key, text, parent)


def _padded(number, padding):
    return u'{}'.format(str(number).zfill(padding))


def _natural_key(s):
    """Sort key that orders '9' before '10' instead of after it."""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r'(\d+)', s or u'')]


def suggest_padding(count):
    """Width wide enough for *count* entries, never narrower than 2."""
    return max(2, len(str(max(1, count))))
