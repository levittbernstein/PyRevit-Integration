# -*- coding: utf-8 -*-
"""
Remembered numbering preferences for the Keynote Manager.

Stored in pyRevit's own per-user config, keyed by project, NOT in the .rvt.

Why not Extensible Storage
--------------------------
Writing to the model would need a Transaction, and in a workshared project that
means checking out the element that holds the setting and keeping it until the
next sync — so remembering a checkbox would start blocking colleagues from
opening the dialog.  These are UI preferences, not project data, so they do not
belong in the model.

Keyed by project so that a simple project can sit on flat numbering while a
complex one uses prefixes, without the two fighting over one global setting.
The key is ProjectInformation.UniqueId, which is stable across sessions and
identical for every user of a workshared model (locals are copies of central).

Every function is failure-tolerant: a missing or corrupt config must never stop
the tool from opening.
"""

import json

_SECTION  = 'lb_keynote_manager'
_OPTION   = 'per_project'
_MAX_KEYS = 200   # stop the store growing without bound across many projects

# Only benign numbering preferences are remembered.  'renumber_uncategorised'
# is deliberately excluded — it defaults to off because it rewrites tags on
# every uncategorised keynote, and silently restoring it as ON in a later
# session would be a nasty surprise.
DEFAULTS = {
    'use_prefix': True,
    'padding':    2,
}


def project_key(doc):
    """A stable per-project identifier, or None if one cannot be determined."""
    try:
        info = doc.ProjectInformation
        if info is not None and info.UniqueId:
            return str(info.UniqueId)
    except Exception:
        pass
    try:
        return str(doc.PathName) or None
    except Exception:
        return None


def _config():
    from pyrevit import script
    return script.get_config(_SECTION)


def _read_all():
    try:
        raw = _config().get_option(_OPTION, '{}')
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load(key):
    """Preferences for *key*, falling back to DEFAULTS for anything absent."""
    values = dict(DEFAULTS)
    if not key:
        return values

    stored = _read_all().get(key)
    if isinstance(stored, dict):
        for name, default in DEFAULTS.items():
            if name in stored:
                # Coerce to the default's type so a hand-edited config cannot
                # feed a string into the padding arithmetic.
                try:
                    values[name] = type(default)(stored[name])
                except (TypeError, ValueError):
                    pass
    return values


def save(key, values):
    """Persist *values* for *key*. Returns True on success."""
    if not key:
        return False
    try:
        data = _read_all()
        data[key] = dict((name, values[name])
                         for name in DEFAULTS if name in values)

        if len(data) > _MAX_KEYS:
            # Keep the current project plus an arbitrary remainder; these are
            # throwaway preferences, so which ones survive does not matter.
            keep = [key] + [k for k in list(data.keys())
                            if k != key][:_MAX_KEYS - 1]
            data = dict((k, data[k]) for k in keep)

        cfg = _config()
        cfg.set_option(_OPTION, json.dumps(data))

        from pyrevit import script
        script.save_config()
        return True
    except Exception:
        return False
