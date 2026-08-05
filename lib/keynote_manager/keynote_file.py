# -*- coding: utf-8 -*-
"""
Keynote .txt parsing and writing.

File format (Autodesk, tab-delimited, no header):

    <key>  TAB  <text>                     top-level entry / category
    <key>  TAB  <text>  TAB  <parent key>  child entry

The parent key in column 3 is the ONLY thing that expresses hierarchy —
indentation and file order are irrelevant to Revit.  Revit's keynote browser
sorts alphabetically by key, which is exactly why renumbering is the only way
to reorder keynotes.

ENCODING — READ THIS BEFORE CHANGING ANYTHING
---------------------------------------------
LB project keynote files are UTF-16 LE with a BOM and CRLF line endings.
Revit accepts ANSI, UTF-16 (with BOM) or UTF-8 (with BOM); UTF-8 *without*
a BOM either loads as mojibake or fails silently.

This module therefore never guesses on write.  read_keynote_file() records the
exact BOM, codec, newline and trailing-newline state it found in a FileMeta,
and write_keynote_file() reproduces them byte-for-byte.  Round-tripping a file
with no edits must produce an identical file — test_roundtrip() asserts this.
"""

import io
import os
import re
import shutil
import datetime


# ── Byte-order marks, longest first so UTF-32 never matches as UTF-16 ─────────
_BOMS = [
    (b'\xff\xfe\x00\x00', 'utf-32-le'),
    (b'\x00\x00\xfe\xff', 'utf-32-be'),
    (b'\xef\xbb\xbf',     'utf-8'),
    (b'\xff\xfe',         'utf-16-le'),
    (b'\xfe\xff',         'utf-16-be'),
]

# Lines pyRevit's own keynote manager injects to embed its text database.
# Revit ignores them because of the leading '#', but they mean the file is
# under pyRevit's management and we must not fight it.
_PYREVIT_DB_RE = re.compile(r'^\s*#.*@(db|table|begin|end)\s*\(', re.IGNORECASE)

BACKUP_DIRNAME = '_LB_keynote_backups'


class FileMeta(object):
    """Everything needed to write a file back exactly as it was found."""

    def __init__(self, bom, codec, newline, trailing_newline, comments):
        self.bom              = bom               # bytes, b'' if none
        self.codec            = codec             # python codec name
        self.newline          = newline           # u'\r\n' or u'\n'
        self.trailing_newline = trailing_newline  # bool
        self.comments         = comments          # [(index, raw_line)] preserved verbatim

    def describe(self):
        enc = self.codec.upper()
        if self.bom:
            enc += ' + BOM'
        return '{}, {} line endings'.format(
            enc, 'CRLF' if self.newline == u'\r\n' else 'LF')


class KeynoteEntry(object):
    """One row of the keynote file."""

    __slots__ = ('key', 'text', 'parent')

    def __init__(self, key, text, parent=u''):
        self.key    = key
        self.text   = text
        self.parent = parent or u''

    @property
    def is_category(self):
        """True when this entry has no parent — Revit renders these as branches."""
        return not self.parent

    def clone(self):
        return KeynoteEntry(self.key, self.text, self.parent)

    def __repr__(self):
        return 'KeynoteEntry({!r}, {!r}, {!r})'.format(
            self.key, self.text, self.parent)


# ── Reading ───────────────────────────────────────────────────────────────────

def _sniff(raw):
    """Return (bom_bytes, codec) for a raw byte string."""
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            return bom, codec

    # No BOM.  Revit's own default for Latin-only files is ANSI, but a
    # BOM-less UTF-8 file is also common when someone has edited in a
    # modern text editor.  Prefer UTF-8 when it decodes cleanly.
    try:
        raw.decode('utf-8')
        return b'', 'utf-8'
    except (UnicodeDecodeError, ValueError):
        return b'', 'cp1252'


def read_keynote_file(path):
    """
    Parse *path*.

    Returns (entries, meta, problems) where entries is a list of KeynoteEntry
    in file order, meta is a FileMeta, and problems is a list of human-readable
    strings describing anything malformed (never raises on bad rows).
    """
    with open(path, 'rb') as fh:
        raw = fh.read()

    bom, codec = _sniff(raw)
    text = raw[len(bom):].decode(codec, 'replace')

    newline          = u'\r\n' if u'\r\n' in text else u'\n'
    trailing_newline = text.endswith(newline)

    problems = []
    entries  = []
    comments = []

    for idx, line in enumerate(text.split(newline)):
        if not line.strip():
            continue

        if line.lstrip().startswith(u'#'):
            comments.append((idx, line))
            continue

        fields = line.split(u'\t')
        key    = fields[0].strip()
        if not key:
            problems.append('Line {}: no key value, skipped.'.format(idx + 1))
            continue

        body   = fields[1].strip() if len(fields) > 1 else u''
        parent = fields[2].strip() if len(fields) > 2 else u''

        if len(fields) > 3:
            problems.append(
                'Line {} ("{}"): {} tab-separated fields, expected at most 3. '
                'Extra fields ignored — check for stray tabs in the keynote '
                'text.'.format(idx + 1, key, len(fields)))

        entries.append(KeynoteEntry(key, body, parent))

    meta = FileMeta(bom, codec, newline, trailing_newline, comments)
    return entries, meta, problems


def is_pyrevit_managed(path):
    """
    True when the file contains pyRevit keynote-manager database lines.

    pyRevit stores its schema on '#'-prefixed lines that Revit ignores.  Both
    tools writing the same file would corrupt that database, so the caller
    should refuse to run rather than try to coexist.
    """
    with open(path, 'rb') as fh:
        raw = fh.read()
    bom, codec = _sniff(raw)
    text = raw[len(bom):].decode(codec, 'replace')
    return any(_PYREVIT_DB_RE.match(ln) for ln in text.splitlines())


# ── Writing ───────────────────────────────────────────────────────────────────

def serialise(entries, meta):
    """Render *entries* to the exact byte string that should hit disk."""
    lines = []

    # Preserve any comment lines that were in the original file, in place.
    for _idx, raw_line in meta.comments:
        lines.append(raw_line)

    for e in entries:
        if e.parent:
            lines.append(u'{}\t{}\t{}'.format(e.key, e.text, e.parent))
        else:
            lines.append(u'{}\t{}'.format(e.key, e.text))

    body = meta.newline.join(lines)
    if meta.trailing_newline:
        body += meta.newline

    return meta.bom + body.encode(meta.codec)


def write_keynote_file(path, entries, meta):
    """
    Write *entries* to *path*, reproducing the original encoding exactly.

    Writes to a temp file in the same directory then replaces the target, so a
    failure mid-write cannot leave a truncated keynote file that Revit would
    refuse to load.
    """
    data = serialise(entries, meta)
    tmp  = path + '.lbtmp'

    with open(tmp, 'wb') as fh:
        fh.write(data)

    # os.replace is atomic but is Python 3 only; IronPython 2.7 needs the
    # remove-then-rename dance.  The window is microseconds and we hold a
    # backup made before this point.
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def backup(path):
    """
    Copy *path* into a timestamped file inside a BACKUP_DIRNAME subfolder.

    The subfolder keeps the keynote directory clean — pyRevit's habit of
    dropping timestamped files beside the original is a long-standing
    complaint (pyRevit issues #1440, #1457).

    Returns the backup path, or None if the backup could not be made.
    """
    try:
        folder = os.path.join(os.path.dirname(path), BACKUP_DIRNAME)
        if not os.path.isdir(folder):
            os.makedirs(folder)

        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        name  = os.path.splitext(os.path.basename(path))[0]
        dest  = os.path.join(folder, '{}_{}.txt'.format(name, stamp))
        shutil.copy2(path, dest)
        return dest
    except Exception:
        return None


def is_writable(path):
    """
    True when *path* can actually be written.

    Checked by opening for append rather than trusting os.access, which lies
    about network shares and read-only ACLs.  Must be verified BEFORE touching
    the model, or we would update parameters against a file that never changed.
    """
    try:
        with open(path, 'ab'):
            pass
        return True
    except Exception:
        return False


# ── Self-check ────────────────────────────────────────────────────────────────

def test_roundtrip(path):
    """
    Read *path* and confirm re-serialising it reproduces the original bytes.

    Run this before any destructive operation on an unfamiliar file: if it
    fails, our parser has lost information and must not be used to rewrite it.
    Returns (ok, detail).
    """
    with open(path, 'rb') as fh:
        original = fh.read()

    entries, meta, _problems = read_keynote_file(path)
    rebuilt = serialise(entries, meta)

    if rebuilt == original:
        return True, 'Round-trip exact ({} bytes, {}).'.format(
            len(original), meta.describe())

    return False, (
        'Round-trip differs: {} bytes in, {} bytes out ({}). '
        'Refusing to rewrite this file.'.format(
            len(original), len(rebuilt), meta.describe()))
