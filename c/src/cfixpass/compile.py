"""Compile-stage repair: general syntax repair, then diagnostic-driven repairs of semantic errors with the same token
search as fallback. Tied choices are kept as holes that the test-driven search can revisit."""
from __future__ import annotations

import functools
import re

import clang.cindex as ci

from . import cfront, gate, lexer
from .syntax import SyntaxRepair
from .text import Edit, Hole, distance, line_of, line_text, shift_all

HEADER_ORDER = ['stdio', 'stdlib', 'string', 'math', 'ctype', 'stdbool', 'limits', 'float', 'stdint', 'inttypes',
                'stddef', 'time', 'assert', 'errno', 'stdarg', 'wchar', 'wctype', 'locale', 'setjmp', 'fenv', 'iso646',
                'complex', 'tgmath', 'stdalign', 'stdnoreturn', 'uchar']


@functools.lru_cache(maxsize=1)
def header_index():
    """name -> header for the supported standard headers (functions, types, macros), most specific header first."""
    idx = ci.Index.create()
    decls = {}
    for h in HEADER_ORDER:
        if h not in gate.HEADERS:
            continue
        src = f'#include <{h}.h>\n'
        tu = idx.parse('hdr.c', args=cfront.args(), unsaved_files=[('hdr.c', src)],
                       options=ci.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD | ci.TranslationUnit.PARSE_INCOMPLETE)
        names = set()
        for c in tu.cursor.get_children():
            if c.kind in (ci.CursorKind.FUNCTION_DECL, ci.CursorKind.VAR_DECL, ci.CursorKind.TYPEDEF_DECL,
                          ci.CursorKind.MACRO_DEFINITION, ci.CursorKind.STRUCT_DECL, ci.CursorKind.ENUM_CONSTANT_DECL):
                if c.spelling and not c.spelling.startswith('_'):
                    names.add(c.spelling)
            if c.kind == ci.CursorKind.ENUM_DECL:
                for e in c.get_children():
                    names.add(e.spelling)
        decls[h] = names
    out = {}
    for h in sorted(decls, key=lambda x: len(decls[x])):
        for n in decls[h]:
            out.setdefault(n, h)
    return out


def includes(src):
    return set(re.findall(r'^\s*#\s*include\s*<\s*(\w+)\.h\s*>', src, re.M))


def include_edit(src, header):
    """Insert '#include <header.h>' after the last #include line (or at the top)."""
    last = None
    for m in re.finditer(r'^\s*#\s*include[^\n]*\n?', src, re.M):
        last = m
    pos = last.end() if last else 0
    text = f'#include <{header}.h>\n'
    if last and not last.group().endswith('\n'):
        text = '\n' + text
    return Edit(pos, pos, text)


class Fix:
    def __init__(self, edit, cost, rule, value=None):
        self.edit, self.cost, self.rule, self.value = edit, cost, rule, value


UNDECLARED = re.compile(r"(?:use of undeclared identifier|unknown type name|call to undeclared (?:library )?function) '([^']+)'")


def scope_names(unit):
    """Identifiers declared in the file: (name, kind) for variables, parameters, functions, types, fields, constants."""
    out = {}
    for c in cfront.walk(unit.cursor):
        if not unit.in_file(c) and c.kind != ci.CursorKind.TRANSLATION_UNIT:
            continue
        if c.kind in (ci.CursorKind.VAR_DECL, ci.CursorKind.PARM_DECL, ci.CursorKind.FUNCTION_DECL,
                      ci.CursorKind.TYPEDEF_DECL, ci.CursorKind.FIELD_DECL, ci.CursorKind.ENUM_CONSTANT_DECL,
                      ci.CursorKind.MACRO_DEFINITION) and c.spelling:
            out.setdefault(c.spelling, c.kind)
    return out


def function_at(unit, offset):
    for f in cfront.functions(unit):
        if f.extent.start.offset <= offset < f.extent.end.offset:
            return f
    return None


def candidates(unit, d):
    """Rule candidates for one semantic error."""
    src = unit.src
    out = []
    for f in d.fixits:
        out.append(Fix(Edit(f.start, f.end, f.text), 0, f"clang fix-it '{f.text}'"))
    m = UNDECLARED.search(d.message)
    if m:
        name = m.group(1)
        hidx = header_index()
        have = includes(src)
        if name in hidx and hidx[name] not in have:
            out.append(Fix(include_edit(src, hidx[name]), 0, f'add #include <{hidx[name]}.h>'))
        # a function defined later in the file: add a prototype before the first function definition
        for fn in cfront.functions(unit):
            if fn.spelling == name and fn.extent.start.offset > d.offset:
                body = next((c for c in fn.get_children() if c.kind == ci.CursorKind.COMPOUND_STMT), None)
                if body is not None:
                    proto = src[fn.extent.start.offset:body.extent.start.offset].strip() + ';\n'
                    first = cfront.functions(unit)[0].extent.start.offset
                    first = src.rfind('\n', 0, first) + 1
                    out.append(Fix(Edit(first, first, proto), 0, f"add prototype for '{name}'"))
        # rename to a declared name (or a standard library name) at edit distance <= 2
        start = d.offset
        if src[start:start + len(name)] == name:
            names = scope_names(unit)
            pool = list(names) + [n for n in hidx if hidx[n] in have and abs(len(n) - len(name)) <= 2]
            seen = set()
            for cand in pool:
                if cand == name or cand in seen:
                    continue
                seen.add(cand)
                dist = distance(cand.lower(), name.lower()) if cand.lower() != name.lower() else 0
                if cand.lower() == name.lower() or dist <= 1 or (dist == 2 and len(name) >= 4):
                    out.append(Fix(Edit(start, start + len(name), cand), 1 + dist + (0 if cand in names else 0.5),
                                   f'rename {name} -> {cand}'))
            # an undeclared variable: declare it at the top of the enclosing function
            if 'identifier' in d.message and re.fullmatch(r'[a-z_][A-Za-z0-9_]*', name):
                fn = function_at(unit, d.offset)
                body = None if fn is None else next((c for c in fn.get_children() if c.kind == ci.CursorKind.COMPOUND_STMT), None)
                if body is not None:
                    at = body.extent.start.offset + 1
                    kids = list(body.get_children())
                    ind = '    '
                    if kids:
                        ls = src.rfind('\n', 0, kids[0].extent.start.offset) + 1
                        ind = src[ls:kids[0].extent.start.offset] if not src[ls:kids[0].extent.start.offset].strip() else ind
                    for i, ty in enumerate(('int', 'double', 'char', 'long', 'float')):
                        out.append(Fix(Edit(at, at, f'\n{ind}{ty} {name};'), 4 + 0.01 * i, f'declare {ty} {name}', value=ty))
    if 'should return a value' in d.message or 'should not return a value' in d.message:
        m2 = re.match(r'return\s*;', src[d.offset:d.offset + 20])
        fn = function_at(unit, d.offset)
        if m2 and fn is not None:
            for v in return_values(unit, fn):
                out.append(Fix(Edit(d.offset, d.offset + m2.end(), f'return {v};'), 2, f'value:{v}', value=v))
        if 'should not' in d.message:
            m3 = re.match(r'return\b([^;]*);', src[d.offset:d.offset + 400])
            if m3:
                out.append(Fix(Edit(d.offset, d.offset + m3.end(), 'return;'), 2, 'drop return value'))
    if 'incompatible' in d.message and 'conversion' in d.message and d.ranges:
        a, b = d.ranges[-1]
        expr = src[a:b]
        if expr.startswith('&'):
            out.append(Fix(Edit(a, a + 1, ''), 2, "remove '&'"))
        if expr.startswith('*'):
            out.append(Fix(Edit(a, a + 1, ''), 2, "remove '*'"))
        out.append(Fix(Edit(a, a, '&'), 2, "add '&'"))
        out.append(Fix(Edit(a, a, '*'), 2.5, "add '*'"))
    return out


def return_values(unit, fn):
    """Candidate return values: other returns in the function, locals and parameters of the return type, literals."""
    src = unit.src
    rt = fn.result_type.spelling
    vals = []
    for c in cfront.walk(fn):
        if c.kind == ci.CursorKind.RETURN_STMT:
            kids = list(c.get_children())
            if kids:
                v = src[kids[0].extent.start.offset:kids[0].extent.end.offset]
                if v and len(v) <= 60 and v not in vals:
                    vals.append(v)
    for c in cfront.walk(fn):
        if c.kind in (ci.CursorKind.VAR_DECL, ci.CursorKind.PARM_DECL) and c.type.spelling == rt and c.spelling not in vals:
            vals.append(c.spelling)
    for lit in (['0', '1', '-1'] if rt not in ('double', 'float') else ['0.0', '1.0']):
        if lit not in vals:
            vals.append(lit)
    return vals


class CompileRepair:
    def __init__(self, syntax_budget=90.0, token_steps=12):
        self.front = cfront.front()
        self.log = []
        self.holes = []
        self.failure = None
        self.lossy = False
        self.candidates_tried = 0
        self.token_steps = token_steps
        self.syntax_budget = syntax_budget
        self.signatures = None

    def run(self, src):
        sr = SyntaxRepair(self.syntax_budget)
        src = sr.run(src)
        self.log += sr.log
        self.candidates_tried += sr.parses
        self.lossy = sr.lossy
        if sr.failure:
            self.failure = sr.failure
            return src
        src = self.type_directed(src, sr.holes)
        self.holes += sr.holes
        self.signatures = cfront.signatures(self.front.analyze(src))
        return self.semantic_phase(src, {src})

    def type_directed(self, src, holes):
        """Settle tied syntax alternatives by type checking: keep those with the fewest compile errors."""
        for h in sorted(list(holes), key=lambda h: -h.start):
            if h not in holes:
                continue
            now = src[h.start:h.end]
            counts = {}
            for alt in h.alternatives:
                ns = Edit(h.start, h.end, alt).apply(src)
                self.candidates_tried += 1
                u = self.front.parse(ns)
                if u.syntax_errors:
                    continue
                counts[alt] = len(u.errors)
            if not counts:
                continue
            best_count = min(counts.values())
            best_alt = now if counts.get(now) == best_count else min((a for a in counts if counts[a] == best_count),
                                                                      key=h.alternatives.index)
            if best_alt != now:
                e = Edit(h.start, h.end, best_alt)
                prev, src = src, e.apply(src)
                self.log.append({'phase': 'syntax (type-directed choice)', 'line': line_of(src, h.start),
                                 'diagnostic': h.reason, 'message': 'tied syntax alternatives ranked by compile errors',
                                 'rule': f"'{now}' -> '{best_alt}'", 'before': line_text(prev, h.start),
                                 'after': line_text(src, h.start)})
                for o in holes:
                    if o is not h:
                        o.shift(e)
                h.end = h.start + len(best_alt)
            h.alternatives = [a for a in h.alternatives if counts.get(a, 10 ** 9) <= best_count]
            if len(h.alternatives) < 2:
                holes.remove(h)
        return src

    def semantic_phase(self, src, seen):
        steps = 0
        for _ in range(80):
            unit = self.front.analyze(src)
            errs = unit.errors
            if not errs:
                return src
            if any(d.severity == 4 for d in errs):
                self.failure = 'fatal: ' + errs[0].message
                return src
            fixed = False
            for d in errs[:5]:
                tried = []
                for f in candidates(unit, d):
                    e = f.edit
                    ns = e.apply(src)
                    if ns in seen:
                        continue
                    self.candidates_tried += 1
                    nu = self.front.parse(ns)
                    if nu.syntax_errors:
                        continue
                    same = any(x.message == d.message and abs(x.offset - _moved(d.offset, e)) <= 1 for x in nu.errors)
                    if not same and len(nu.errors) <= len(errs) + 2:
                        tried.append((f, ns, len(nu.errors)))
                if not tried:
                    continue
                low = min(t[2] for t in tried)
                tied = [t for t in tried if t[2] == low]
                best = min(tied, key=lambda t: t[0].cost)
                seen.add(best[1])
                rec = {'phase': 'types', 'line': d.line, 'diagnostic': d.category, 'message': d.message,
                       'rule': best[0].rule, 'before': line_text(src, best[0].edit.start),
                       'after': line_text(best[1], best[0].edit.start)}
                same_span = [t for t in tied if (t[0].edit.start, t[0].edit.end) == (best[0].edit.start, best[0].edit.end)]
                if len(tied) > 1:
                    rec['tied_alternatives'] = len(tied)
                self.log.append(rec)
                shift_all(self.holes, best[0].edit)
                if len(same_span) > 1:
                    e = best[0].edit
                    alts = []
                    for t in same_span:
                        if t[0].edit.text not in alts:
                            alts.append(t[0].edit.text)
                    self.holes.append(Hole(e.start, e.start + len(e.text), alts, d.message))
                src = best[1]
                fixed = True
                break
            if not fixed:
                if steps >= self.token_steps:
                    break
                steps += 1
                st = SyntaxRepair(self.syntax_budget).type_step(src, errs)
                if st is None or st[0] in seen:
                    break
                ns, m, lossy, e, hole = st
                seen.add(ns)
                self.log.append(m)
                self.lossy |= lossy
                shift_all(self.holes, e)
                if hole is not None:
                    self.holes.append(hole)
                src = ns
        unit = self.front.analyze(src)
        if unit.errors:
            self.failure = 'unresolved compile error: ' + unit.errors[0].brief()
        return src


def _moved(pos, e):
    return pos + len(e.text) - (e.end - e.start) if pos >= e.end else pos
