# -*- coding: utf-8 -*-
"""Per-view result collector, rendered into a clean, plain-language summary."""


class ViewReport(object):
    """Collects what happened converting one floor plan."""

    def __init__(self, source_name):
        self.source_name = source_name
        self.area_plan_name = None
        self.counts = {}          # label -> int
        self.notes = []           # short plain-language lines
        self.warnings = []        # problems worth surfacing
        self.colour_steps = None  # set when the colour scheme needs manual setup

    def count(self, label, n):
        self.counts[label] = self.counts.get(label, 0) + n

    def note(self, msg):
        self.notes.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def colour_action(self, steps):
        self.colour_steps = steps

    def _summary_line(self):
        c = self.counts
        bits = []
        if 'areas' in c:
            bits.append('{} areas'.format(c['areas']))
        if c.get('areas tagged'):
            bits.append('{} tagged'.format(c['areas tagged']))
        if 'boundary lines' in c:
            bits.append('{} boundaries'.format(c['boundary lines']))
        if c.get('keys created'):
            bits.append('{} key(s) copied'.format(c['keys created']))
        return ('**' + '  ·  '.join(bits) + '**') if bits else ''

    def to_md(self):
        lines = ['## {}'.format(self.area_plan_name or self.source_name)]
        if self.area_plan_name and self.source_name:
            lines.append('_from {}_'.format(self.source_name))
        summ = self._summary_line()
        if summ:
            lines.append(summ)
        for n in self.notes:
            lines.append('- {}'.format(n))
        for w in self.warnings:
            lines.append('- ⚠ {}'.format(w))
        return '\n'.join(lines)
