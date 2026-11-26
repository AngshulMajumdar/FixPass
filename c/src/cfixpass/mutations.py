"""Typed, AST-anchored program edits used by the test-driven search (clang AST via libclang)."""
from __future__ import annotations

from dataclasses import dataclass

import clang.cindex as ci

from . import cfront, lexer
from .text import Edit

K = ci.CursorKind
T = ci.TypeKind

INTEGRAL = {T.BOOL, T.CHAR_U, T.UCHAR, T.CHAR16, T.CHAR32, T.USHORT, T.UINT, T.ULONG, T.ULONGLONG, T.UINT128, T.CHAR_S,
            T.SCHAR, T.WCHAR, T.SHORT, T.INT, T.LONG, T.LONGLONG, T.INT128, T.ENUM}
FLOATING = {T.FLOAT, T.DOUBLE, T.LONGDOUBLE}
REL = ['<', '<=', '>', '>=', '==', '!=']
ARITH = ['+', '-', '*', '/', '%']
FUNC_SWAPS = {'fmin': 'fmax', 'fmax': 'fmin', 'floor': 'ceil', 'ceil': 'floor', 'fminf': 'fmaxf', 'fmaxf': 'fminf',
              'floorf': 'ceilf', 'ceilf': 'floorf'}


@dataclass(frozen=True)
class Cand:
    edit: Edit
    cost: int
    op: str
    anchor: int


def kind(t):
    t = t.get_canonical()
    if t.kind == T.ELABORATED:
        t = t.get_named_type().get_canonical()
    return t.kind


def numeric(t):
    k = kind(t)
    return k in INTEGRAL or k in FLOATING


def integral(t):
    return kind(t) in INTEGRAL


def unwrap(c):
    while c.kind == K.UNEXPOSED_EXPR:
        kids = list(c.get_children())
        if len(kids) != 1:
            break
        c = kids[0]
    return c


def generate(unit):
    src = unit.src
    out = []
    toks = lexer.lex(src)

    def add(s, e, text, cost, op, anchor):
        if 0 <= s <= e <= len(src) and src[s:e] != text:
            out.append(Cand(Edit(s, e, text), cost, op, anchor))

    def start(c):
        return c.extent.start.offset

    def end(c):
        return c.extent.end.offset

    def text(c):
        return src[start(c):end(c)]

    def op_between(a, b, ops):
        """(position, operator) of the operator token between offsets a and b."""
        i = lexer.index_at(toks, a)
        while i < len(toks) and toks[i].start < b:
            if toks[i].start >= a and toks[i].text in ops:
                return toks[i].start, toks[i].text
            i += 1
        return -1, None

    def plus_minus_one(e):
        e = unwrap(e)
        if e.kind not in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR, K.CALL_EXPR, K.ARRAY_SUBSCRIPT_EXPR):
            return
        if kind(e.type) not in (T.INT, T.LONG, T.SHORT, T.LONGLONG, T.UINT, T.ULONG):
            return
        t = text(e)
        if not t or '\n' in t:
            return
        add(start(e), end(e), f'({t} + 1)', 2, 'expr+1', start(e))
        add(start(e), end(e), f'({t} - 1)', 2, 'expr-1', start(e))

    for fn in cfront.functions(unit):
        globals_ = [c for c in cfront.file_cursors(unit) if c.kind == K.VAR_DECL]
        params = list(fn.get_arguments())
        local_vars = [c for c in cfront.walk(fn) if c.kind == K.VAR_DECL]
        visible = [(v.spelling, v.type.get_canonical().spelling, start(v)) for v in globals_ + params + local_vars
                   if v.spelling]
        char_lits = []
        for c in cfront.walk(fn):
            if c.kind == K.CHARACTER_LITERAL and text(c) not in char_lits:
                char_lits.append(text(c))
        pairs = []
        stack = [(fn, None)]
        while stack:
            cur, par = stack.pop()
            pairs.append((cur, par))
            for ch in cur.get_children():
                stack.append((ch, cur))
        pairs.sort(key=lambda p: start(p[0]))
        for c, parent in pairs:
            if not unit.in_file(c):
                continue
            s, e = start(c), end(c)
            if c.kind == K.BINARY_OPERATOR:
                kids = list(c.get_children())
                if len(kids) != 2:
                    continue
                lhs, rhs = kids
                pos, op = op_between(end(lhs), start(rhs), REL + ARITH + ['&&', '||', '=', '&', '|', '<<', '>>', '^'])
                if op is None:
                    continue
                lt, rt = lhs.type, rhs.type
                group = None
                if op in REL:
                    group = REL if numeric(lt) and numeric(rt) else ['==', '!='] if op in ('==', '!=') else None
                elif op in ARITH and numeric(lt) and numeric(rt):
                    group = ARITH if integral(lt) and integral(rt) else ['+', '-', '*', '/']
                elif op in ('&&', '||'):
                    group = ['&&', '||']
                if group:
                    for g in group:
                        if g != op:
                            add(pos, pos + len(op), g, 1, f'operator {op}->{g}', pos)
                if op == '/' and integral(lt) and integral(rt):
                    add(start(lhs), start(lhs), '(double) ', 2, 'floating division', start(lhs))
                if op in ('+', '-') and numeric(lt) and unwrap(rhs).kind == K.INTEGER_LITERAL:
                    add(s, e, text(lhs), 2, 'drop constant offset', s)
                if op in REL or op in ARITH:
                    plus_minus_one(lhs)
                    plus_minus_one(rhs)
                stmt_parent = parent is not None and parent.kind in (K.COMPOUND_STMT, K.IF_STMT, K.FOR_STMT, K.WHILE_STMT,
                                                                     K.DO_STMT, K.CASE_STMT, K.DEFAULT_STMT)
                if op == '=' and numeric(lt) and stmt_parent:
                    add(pos, pos + 1, '+=', 2, 'assign = -> +=', s)
                    add(pos, pos + 1, '-=', 2, 'assign = -> -=', s)
            elif c.kind == K.COMPOUND_ASSIGNMENT_OPERATOR:
                kids = list(c.get_children())
                if len(kids) != 2:
                    continue
                pos, op = op_between(end(kids[0]), start(kids[1]), ['+=', '-=', '*=', '/=', '%='])
                alts = {'+=': ['-=', '='], '-=': ['+=', '='], '*=': ['/='], '/=': ['*='], '%=': ['/=']}
                for a in alts.get(op, []):
                    add(pos, pos + 2, a, 1, f'assign {op}->{a}', s)
            elif c.kind == K.ARRAY_SUBSCRIPT_EXPR:
                kids = list(c.get_children())
                if len(kids) == 2:
                    plus_minus_one(kids[1])
            elif c.kind == K.INTEGER_LITERAL:
                t = text(c)
                try:
                    x = int(t.rstrip('uUlL'), 0)
                except ValueError:
                    continue
                suffix = t[len(t.rstrip('uUlL')):]
                add(s, e, str(x + 1) + suffix, 1, 'literal+1', s)
                if x > 0:
                    add(s, e, str(x - 1) + suffix, 1, 'literal-1', s)
            elif c.kind == K.CHARACTER_LITERAL:
                t = text(c)
                alts = list(char_lits)
                if len(t) == 3 and t[1].isalpha():
                    alts.append("'" + t[1].swapcase() + "'")
                for a in alts:
                    if a != t:
                        add(s, e, a, 1, 'char literal', s)
            elif c.kind == K.UNARY_OPERATOR:
                t = text(c)
                kids = list(c.get_children())
                if t.startswith('!') and kids:
                    add(s, e, text(kids[0]), 1, 'remove negation', s)
                for a, b in (('++', '--'), ('--', '++')):
                    at = src.find(a, s, e)
                    if at >= 0 and (t.startswith(a) or t.endswith(a)):
                        add(at, at + 2, b, 1, f'{a} -> {b}', s)
                        break
            elif c.kind == K.CONDITIONAL_OPERATOR:
                kids = list(c.get_children())
                if len(kids) == 3:
                    ts, te, fs, fe = start(kids[1]), end(kids[1]), start(kids[2]), end(kids[2])
                    if ts < te <= fs < fe:
                        add(ts, fe, src[fs:fe] + src[te:fs] + src[ts:te], 2, 'swap ternary branches', ts)
            elif c.kind == K.CALL_EXPR:
                args = list(c.get_arguments())
                for i in range(len(args)):
                    for j in range(i + 1, len(args)):
                        ti, tj = args[i].type.get_canonical().spelling, args[j].type.get_canonical().spelling
                        if ti == tj and not ti.startswith('char *') and 'const char' not in ti:
                            si, ei, sj, ej = start(args[i]), end(args[i]), start(args[j]), end(args[j])
                            if si < ei <= sj < ej:
                                add(si, ej, src[sj:ej] + src[ei:sj] + src[si:ei], 2, 'swap arguments', si)
                name = c.spelling
                if name in FUNC_SWAPS:
                    callee = src.find(name, s, e)
                    if callee == s:
                        add(s, s + len(name), FUNC_SWAPS[name], 1, f'{name}->{FUNC_SWAPS[name]}', s)
            elif c.kind == K.DECL_REF_EXPR:
                ref = c.referenced
                if ref is None or ref.kind not in (K.VAR_DECL, K.PARM_DECL) or text(c) != c.spelling:
                    continue
                ty = ref.type.get_canonical().spelling
                for name, vty, decl in visible:
                    if name != c.spelling and vty == ty and decl < s:
                        add(s, e, name, 2, f'variable {c.spelling}->{name}', s)
    uniq = {}
    for c in out:
        key = (c.edit.start, c.edit.end, c.edit.text)
        if key not in uniq or c.cost < uniq[key].cost:
            uniq[key] = c
    return list(uniq.values())
