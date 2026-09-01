# -*- coding: utf-8 -*-
"""Best-guess a two-digit BIM level code from a Revit Level name.

Deliberately just a FIRST PASS: the dialog shows every guess for a human to
check before anything is written, so it is fine to guess boldly. When a name is
genuinely ambiguous it returns '' and the level shows blank for the user to
decide — and whatever they type is then remembered, so each level is only ever
decided once.

Convention: 00 ground, 01/02… up, 99/98/97… below ground.
"""

import re

_ORDINALS = {
    'ground': 0, 'first': 1, 'second': 2, 'third': 3, 'fourth': 4,
    'fifth': 5, 'sixth': 6, 'seventh': 7, 'eighth': 8, 'ninth': 9,
    'tenth': 10, 'eleventh': 11, 'twelfth': 12, 'thirteenth': 13,
    'fourteenth': 14, 'fifteenth': 15, 'sixteenth': 16, 'seventeenth': 17,
    'eighteenth': 18, 'nineteenth': 19, 'twentieth': 20,
}


def guess_code(level_name):
    """Return a two-digit code string, or '' when unsure."""
    if not level_name:
        return ''
    name = level_name.strip().lower()

    # 1. An explicit two-digit code already in the name: "Level 00", "02 - GF".
    m = re.search(r'(?<!\d)(\d{2})(?!\d)', name)
    if m:
        return m.group(1)

    # 2. Below ground. "Basement 1"/"B1"/"Lower Ground" -> 99, "Basement 2" ->
    #    98, plain "Basement" -> 99.
    below = (any(w in name for w in ('basement', 'lower ground', 'lower-ground',
                                     'sub-ground', 'sub ground', 'subground'))
             or re.match(r'^\s*b\s*\d', name) is not None)
    if below:
        n = re.search(r'\d+', name)
        depth = int(n.group()) if n else 1
        code = 100 - depth
        return '{:02d}'.format(code) if 0 <= code <= 99 else ''

    # 3. Ground floor -> 00.
    if 'ground' in name:
        return '00'

    # 4. Ordinal-word floors: "First Floor" -> 01, "Tenth" -> 10.
    for word, num in _ORDINALS.items():
        if re.search(r'\b' + word + r'\b', name):
            return '{:02d}'.format(num)

    # 5. A single explicit number: "Level 3", "L7", "Floor 12" -> 03/07/12.
    m = re.search(r'\d+', name)
    if m:
        num = int(m.group())
        if 0 <= num <= 99:
            return '{:02d}'.format(num)

    # 6. Give up — let the user decide (and be remembered).
    return ''
