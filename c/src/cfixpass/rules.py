"""Warning-driven static rules. In C many real bugs compile with only a warning: scanf arguments without '&', format
specifiers that do not match their arguments, '=' in a condition, a stray ';' after if/for/while, uninitialized
locals, a missing return value. Each warning yields candidate edits (mostly clang's own fix-its); with tests, an edit is
kept only if no test regresses."""
from __future__ import annotations

import re

from . import cfront
from .compile import function_at, return_values
from .text import Edit, line_of

SILENCE_ONLY = ('place parentheses', 'separate line', 'explicitly cast', 'add explicit braces', 'silence this warning by')
FORMAT_PTR = re.compile(r"format specifies type '(.+) \*' but the argument has type '(.+)'")


class Finding:
    def __init__(self, line, rule, alternatives, safe=False):
        self.line, self.rule, self.alternatives, self.safe = line, rule, alternatives, safe  # alternatives: [Edit]


def findings(unit):
    src = unit.src
    out = []
    for d in unit.warnings:
        alts, rule, safe = [], None, False
        for f in d.fixits:
            alts.append(Edit(f.start, f.end, f.text))
            rule, safe = f'clang fix-it: {d.message}', d.option == '-Wformat'
        for msg, fx in d.notes:
            if not fx or any(s in msg for s in SILENCE_ONLY) and 'initialize the variable' not in msg:
                continue
            if len(fx) == 1:
                alts.append(Edit(fx[0].start, fx[0].end, fx[0].text))
                rule = f'{msg}'
                safe = safe or 'initialize the variable' in msg
        m = FORMAT_PTR.search(d.message)
        if m and m.group(1) == m.group(2).replace('const ', '') and d.ranges:
            a = d.ranges[0][0]
            if src[a:a + 1] != '&':
                alts.append(Edit(a, a, '&'))
                rule, safe = "scanf argument needs '&'", True
        if d.option == '-Wempty-body' and src[d.offset:d.offset + 1] == ';':
            alts.append(Edit(d.offset, d.offset + 1, ''))
            rule = "delete ';' that makes an empty body"
        if d.option == '-Wreturn-type' and src[d.offset:d.offset + 1] == '}':
            fn = function_at(unit, d.offset)
            if fn is not None:
                ind = '    '
                for v in return_values(unit, fn):
                    alts.append(Edit(d.offset, d.offset, f'{ind}return {v};\n'))
                rule = 'missing return value'
        if alts and rule:
            uniq = []
            for e in alts:
                if all((e.start, e.end, e.text) != (x.start, x.end, x.text) for x in uniq):
                    uniq.append(e)
            out.append(Finding(line_of(src, d.offset), rule, uniq, safe))
    out.sort(key=lambda f: -max(e.start for e in f.alternatives))
    return out
