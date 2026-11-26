"""General syntax repair: least-cost token search with clang as the oracle (after Burke-Fisher / least-cost recovery).

At the first parse error, candidate token edits near the error are tried in cost order. An edit is accepted if the file
then parses, or the first parse error moves at least K tokens further on. Candidates are ranked by edit cost plus an
estimate of the work still ahead (remaining parse errors, or semantic errors once the file parses). Levels escalate from
lexical repairs to priority tokens, the full vocabulary, pairs of edits and finally reported lossy excision, so every
input ends parseable.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import cfront, lexer
from .text import Edit, Hole, code_mask, distance, line_of, line_start, line_text, shift_all

TYPE_PENALTY = 0.7
PRIORITY = [';', ')', '(', '}', '{', ',', ']', '[', '.', '=']
NO_SPACE_BEFORE = {';', ',', ')', ']', '.', '++', '--', '[', '(', '->'}
KEYWORDS = ['return', 'else', 'if', 'for', 'while', 'int', 'long', 'double', 'float', 'char', 'void', 'static', 'const',
            'unsigned', 'break', 'continue', 'case', 'default', 'do', 'struct', 'sizeof', 'switch']
CONFUSABLE = {
    '“': '"', '”': '"', '„': '"', '″': '"', '‘': "'", '’': "'", '′': "'",
    '−': '-', '–': '-', '—': '-', '×': '*', '÷': '/', '≤': '<=', '≥': '>=',
    '≠': '!=', ' ': ' ', '​': '', '﻿': '', '；': ';', '（': '(', '）': ')',
    '，': ',', '　': ' ', '…': '...', '«': '"', '»': '"', '＝': '=', '→': '->'}


def is_word(c):
    return c.isalnum() or c == '_'


@dataclass(frozen=True)
class Score:
    clean: bool     # no parse errors
    after: int      # tokens after the first parse error
    errors: int     # parse errors
    types: int      # semantic errors (+ empty-body warnings) once clean
    offsets: tuple = ()  # offsets of all errors (syntax and semantic)

    def before(self, cutoff: int) -> int:
        return sum(1 for o in self.offsets if o < cutoff)


@dataclass
class Cand:
    edit: Edit
    cost: float
    rule: str
    site: int
    group: str
    token: str | None = None


@dataclass
class Found:
    c: Cand
    src: str
    s: Score
    ties: list = field(default_factory=list)
    chain: list = field(default_factory=list)


def effective(c: Cand, s: Score) -> float:
    return c.cost + (TYPE_PENALTY * min(s.types, 5) if s.clean else 1.0 * min(s.errors, 8))


def accept(base: Score, s: Score, k: int) -> bool:
    if s.clean:
        return True
    if s.after <= base.after - k:
        return True
    return base.after <= k and (s.after < base.after or (s.after <= base.after and s.errors < base.errors))


def better(a: Found | None, c: Cand, s: Score, dist: int, dist_a: int) -> bool:
    if a is None:
        return True
    ec, ea = effective(c, s), effective(a.c, a.s)
    if abs(ec - ea) > 1e-9:
        return ec < ea
    if s.clean != a.s.clean:
        return s.clean
    if s.after != a.s.after:
        return s.after < a.s.after
    return dist < dist_a


def normalize_confusables(src: str):
    mask = code_mask(src)
    out, changed = [], 0
    for i, ch in enumerate(src):
        r = CONFUSABLE.get(ch)
        if r is not None and (mask[i] or ch in '“”'):
            out.append(r)
            changed += 1
        else:
            out.append(ch)
    return ''.join(out), changed


def local(old, e: Edit, ns: str) -> bool:
    """Token edits must not re-lex code outside their own window (no comment or string opener swallowing the file)."""
    nt = lexer.lex(ns)
    b = lexer.index_at(old, e.end)
    while b < len(old) and old[b].start < e.end:
        b += 1
    m = len(old) - b
    if len(nt) < m or abs(len(nt) - len(old)) > 4:
        return False
    for i in range(1, m + 1):
        a, c = old[-i], nt[-i]
        if a.text != c.text or a.kind != c.kind:
            return False
    a = lexer.index_at(old, e.start)
    for i in range(min(a, len(nt))):
        if old[i].text != nt[i].text:
            return False
    for t in nt:
        if t.unterminated and e.start - 1 <= t.start <= e.start + len(e.text):
            return False
    return True


def insert_text(src: str, pos: int, tok: str) -> str:
    word = is_word(tok[0]) or tok.startswith('"')
    before = src[pos - 1] if pos > 0 else ' '
    after = src[pos] if pos < len(src) else ' '
    lead = '' if (tok in NO_SPACE_BEFORE and not word) else ('' if before.isspace() or before in '([' else ' ')
    trail = ' ' if ((word or tok not in NO_SPACE_BEFORE) and not after.isspace() and after not in ');,]') else ''
    return lead + tok + trail


def empty_stmt(toks, before, after, tok) -> float:
    """A ';' right after ';', '{' or '}' (or before ';') makes an empty statement: rarely intended."""
    if tok != ';':
        return 0.0
    p = toks[before].text if 0 <= before < len(toks) else ''
    n = toks[after].text if 0 <= after < len(toks) else ''
    if p in ('int', 'char', 'float', 'double', 'long', 'short', 'unsigned', 'signed', 'void', 'const', 'struct', 'static'):
        return 0.6
    return 0.6 if p in (';', '{', '}') or n == ';' or p == ')' and _control_paren(toks, before) else 0.0


def _control_paren(toks, close_idx) -> bool:
    """True if toks[close_idx] is the ')' closing an if/for/while condition (a ';' there makes an empty body)."""
    depth = 0
    for j in range(close_idx, -1, -1):
        t = toks[j].text
        if t == ')':
            depth += 1
        elif t == '(':
            depth -= 1
            if depth == 0:
                return j > 0 and toks[j - 1].text in ('if', 'for', 'while', 'switch')
    return False


def insert(src, toks, gap, tok, cost, rule):
    cost += empty_stmt(toks, gap - 1, gap, tok)
    pos = (toks[0].start if toks else 0) if gap == 0 else toks[gap - 1].end
    return Cand(Edit(pos, pos, insert_text(src, pos, tok)), cost, f"{rule} '{tok}'", gap, f'ins@{gap}', tok)


def replace(src, toks, i, tok, cost):
    cost += empty_stmt(toks, i - 1, i + 1, tok)
    t = toks[i]
    if t.text in ('{', '}'):
        cost += 0.5
    if t.kind == lexer.PP:
        cost += 50
    text = tok
    if is_word(tok[0]):
        if t.start > 0 and is_word(src[t.start - 1]):
            text = ' ' + text
        if t.end < len(src) and is_word(src[t.end]):
            text = text + ' '
    return Cand(Edit(t.start, t.end, text), cost, f"replace '{t.text}' with '{tok}'", i, f'rep@{i}', tok)


def delete(src, toks, i, cost):
    t = toks[i]
    if '\n' in t.text or t.kind == lexer.PP:
        cost += 50
    if t.text in ('{', '}'):
        cost += 0.5
    if t.kind in (lexer.IDENT, lexer.NUMBER, lexer.STRING, lexer.CHAR):
        cost += 0.5
    elif t.kind == lexer.KEYWORD:
        cost += 0.2
    s, e = t.start, t.end
    if s > 0 and src[s - 1] == ' ' and (e >= len(src) or src[e].isspace()):
        s -= 1
    return Cand(Edit(s, e, ''), cost, f"delete '{t.text}'", i, f'del@{i}', None)


QUOTED = re.compile(r"'([^']+)'")


def expected(d) -> list:
    out = []
    if 'expected' in d.message:
        for m in QUOTED.finditer(d.message.split(' after ')[0] if "expected '" in d.message else d.message):
            out.append(m.group(1))
        if 'expected identifier' in d.message:
            out.append('x')
    for f in d.fixits:
        if f.start == f.end and f.text.strip() and f.text.strip() not in out:
            out.append(f.text.strip())
    return out


def token_edits(src, toks, d, e0, window, full, ahead=1):
    out = []
    n = len(toks)
    lo, hi = max(0, e0 - window), min(n, e0 + ahead)
    exp = expected(d)
    vocab = list(PRIORITY)
    if full:
        seen = set(vocab)
        for o in lexer.OPS + KEYWORDS:
            if o not in seen and o not in ('#', '##'):
                vocab.append(o)
                seen.add(o)
        for i in range(max(0, e0 - 40), min(n, e0 + 40)):
            if toks[i].kind == lexer.IDENT and toks[i].text not in seen:
                vocab.append(toks[i].text)
                seen.add(toks[i].text)
        for x in ('0', '1', '""'):
            if x not in seen:
                vocab.append(x)
    for g in range(lo, hi + 1):
        if g < n and toks[g].kind == lexer.PP and g > 0 and toks[g - 1].kind == lexer.PP:
            continue
        near = 0.001 * abs(e0 - g)
        for x in exp:
            if x != 'x':
                out.append(insert(src, toks, g, x, 0.5 + near, 'insert expected'))
        for x in vocab:
            if full and x in PRIORITY:
                continue
            c = (1.0 if x in PRIORITY else 1.5 if x in lexer.KEYWORDS else 1.6 if (x[0].isalpha() or x[0] == '_')
                 else 1.7 if re.match(r'[0-9"]', x) else 1.3)
            out.append(insert(src, toks, g, x, c + near, 'insert'))
    for i in range(lo, min(n, hi)):
        near = 0.001 * abs(e0 - i)
        if toks[i].kind == lexer.PP:
            continue
        if not full:
            out.append(delete(src, toks, i, 1.0 + near))
            if i + 1 < n and toks[i + 1].kind != lexer.PP:
                a, b = toks[i], toks[i + 1]
                out.append(Cand(Edit(a.start, b.end, b.text + src[a.end:b.start] + a.text), 1.0 + near,
                                f"swap '{a.text}' '{b.text}'", i, f'swap@{i}'))
        for x in (vocab if full else PRIORITY):
            if x == toks[i].text or (full and x in PRIORITY):
                continue
            op_to_op = toks[i].kind == lexer.OP and not (x[0].isalpha() or x[0] == '_') and not re.match(r'[0-9"]', x)
            c = (1.2 if x in PRIORITY else 1.8 if x in lexer.KEYWORDS else 1.8 if (x[0].isalpha() or x[0] == '_')
                 else 1.3 if op_to_op else 1.5)
            out.append(replace(src, toks, i, x, c + near))
        for x in exp:
            if x != 'x' and x != toks[i].text:
                out.append(replace(src, toks, i, x, 1.1 + near))
    known = set(lexer.KEYWORDS) | {t.text for t in toks if t.kind == lexer.IDENT}
    for i in range(max(0, e0 - 12), min(n, hi)):
        t = toks[i]
        near = 0.001 * abs(e0 - i)
        if t.kind in (lexer.IDENT, lexer.KEYWORD) and len(t.text) >= 3:
            w = t.text
            for p in range(1, len(w)):
                a, b = w[:p], w[p:]
                if a in known and b in known:
                    out.append(Cand(Edit(t.start, t.end, a), 0.95 + near, f"un-glue '{w}' -> '{a}'", i, f'unglue@{i}', a))
                    if b != a:
                        out.append(Cand(Edit(t.start, t.end, b), 0.95 + near, f"un-glue '{w}' -> '{b}'", i, f'unglue@{i}', b))
                if a in known or b in known:
                    for sep in (' ', '(', ',', ';'):
                        out.append(Cand(Edit(t.start + p, t.start + p, sep), 1.0 + near, f"split '{w}' with '{sep.strip()}'",
                                        i, f'split@{i}:{p}'))
            for kw in lexer.KEYWORDS:
                at = w.find(kw)
                if 0 < at and at + len(kw) < len(w):
                    for sep in ('(', ' ', ''):
                        out.append(Cand(Edit(t.start + at, t.start + at + len(kw), sep), 1.0 + near,
                                        f"replace glued '{kw}' in '{w}' with '{sep}'", i, f'glue@{i}'))
        if i + 1 < n:
            b = toks[i + 1]
            joined = t.text + b.text
            if b.start > t.end and joined in known and is_word(t.text[0]):
                out.append(Cand(Edit(t.end, b.start, ''), 0.8 + near, f"join '{t.text}' '{b.text}'", i, f'join@{i}'))
    if not full:
        for i in range(lo, min(n, hi) - 1):
            a, b = toks[i], toks[i + 1]
            if a.kind == lexer.PP or b.kind == lexer.PP:
                continue
            out.append(Cand(Edit(a.start, b.end, ''), 1.9 + 0.001 * abs(e0 - i), f"delete '{a.text} {b.text}'", i, f'del2@{i}'))
    return out


def indent(line: str) -> int:
    n = 0
    for ch in line:
        if ch == ' ':
            n += 1
        elif ch == '\t':
            n += 4
        else:
            break
    return n


def brace_candidates(src: str):
    """Indentation-guided brace repair: insert a missing '}' where the indentation says a block ended, or delete an
    extra one. Returns [(Edit, rule)]."""
    out = []
    code = code_mask(src)
    lines = src.split('\n')
    starts = [0] * len(lines)
    for i in range(1, len(lines)):
        starts[i] = starts[i - 1] + len(lines[i - 1]) + 1
    unit = 0
    for l in lines:
        ind = indent(l)
        if l.strip() and ind > 0 and (unit == 0 or ind < unit):
            unit = ind
    unit = unit or 4
    depth = 0
    prev = ''
    for i, l in enumerate(lines):
        t = l.strip()
        comment = t.startswith('//') or t.startswith('*') or t.startswith('/*') or t.startswith('#')
        if t and not comment:
            ind = indent(l)
            closes = t.startswith('}')
            continuation = not (prev == '' or prev.endswith(';') or prev.endswith('{') or prev.endswith('}')
                                or prev.endswith(':') or prev.startswith('#'))
            if not continuation and ind % unit == 0:
                exp_depth = ind // unit + (1 if closes else 0)
                if depth > exp_depth:
                    ins = ''.join(' ' * (k * unit) + '}\n' for k in range(depth - 1, exp_depth - 1, -1))
                    out.append((Edit(starts[i], starts[i], ins), "insert missing '}' (indentation)"))
                    return out
            for c in range(starts[i], starts[i] + len(l)):
                if code[c]:
                    if src[c] == '{':
                        depth += 1
                    elif src[c] == '}':
                        depth -= 1
            if depth < 0 and t == '}':
                end = starts[i] + len(l) + (1 if i + 1 < len(lines) else 0)
                out.append((Edit(starts[i], end, ''), "delete extra '}'"))
                return out
            prev = t
    if depth > 0:
        ins = ('' if src.endswith('\n') else '\n') + ''.join(' ' * (k * unit) + '}\n' for k in range(depth - 1, -1, -1))
        out.append((Edit(len(src), len(src), ins), "insert missing '}' at end of file"))
    return out


def lexical(src, toks, d, e0):
    """Diagnostic-specific lexical repairs plus indentation-guided brace repairs."""
    out = []
    pos = max(0, min(d.offset, len(src) - 1)) if src else 0
    msg = d.message
    ls = line_start(src, pos)
    le = src.find('\n', pos)
    le = len(src) if le < 0 else le
    mask = code_mask(src)
    conf = []
    for i in range(ls, le):
        r = CONFUSABLE.get(src[i])
        if r is not None and (mask[i] or src[i] in '“”'):
            conf.append(Edit(i, i + 1, r))
    if len(conf) > 1:
        line = list(src[ls:le])
        for e in reversed(conf):
            line[e.start - ls:e.end - ls] = list(e.text)
        out.append(Cand(Edit(ls, le, ''.join(line)), 0.25, f'replace {len(conf)} confusable characters on the line', e0,
                        'lex-conf-line'))
    for e in conf:
        out.append(Cand(e, 0.3, f"replace confusable U+{ord(src[e.start]):04X} with '{e.text}'", e0, f'lex-conf@{e.start}'))
    if ('non-ASCII' in msg or 'invalid character' in msg or 'stray' in msg) and pos < len(src):
        out.append(Cand(Edit(pos, pos + 1, ''), 0.6, f"delete illegal character '{src[pos]}'", e0, 'lex-del@c'))
    if "missing terminating '\"'" in msg or 'missing terminating "' in msg:
        q = pos if src[pos:pos + 1] == '"' else src.rfind('"', 0, pos + 1)
        if q >= ls:
            rest = src[q + 1:le]
            r = 0
            for j in range(len(rest) - 1, -1, -1):
                if rest[j] in ');,+':
                    at = q + 1 + j
                    while at > q + 1 and src[at - 1] == ' ':
                        at -= 1
                    out.append(Cand(Edit(at, at, '"'), 0.5 + 0.01 * r, 'close string literal', e0, 'lex-str'))
                    r += 1
            end = le
            while end > q + 1 and src[end - 1].isspace():
                end -= 1
            out.append(Cand(Edit(end, end, '"'), 0.6, 'close string literal at end of line', e0, 'lex-str'))
            out.append(Cand(Edit(q, q + 1, ''), 1.0, "delete stray '\"'", e0, 'lex-strdel'))
    if "missing terminating ' character" in msg or 'empty character constant' in msg:
        q = pos if src[pos:pos + 1] == "'" else src.rfind("'", 0, pos + 1)
        if q >= ls:
            q2 = src.find("'", q + 1)
            if q < q2 < le:
                out.append(Cand(Edit(q, q2 + 1, '"' + src[q + 1:q2] + '"'), 0.5, 'character literal -> string', e0, 'lex-chr'))
            if q + 2 <= le:
                out.append(Cand(Edit(q + 2, q + 2, "'"), 0.6, 'close character literal', e0, 'lex-chr'))
            out.append(Cand(Edit(q, q + 1, ''), 1.0, "delete stray '''", e0, 'lex-chrdel'))
    if 'unterminated /* comment' in msg:
        c = src.rfind('/*', 0, pos + 2)
        if c >= 0:
            line_end = src.find('\n', c)
            j = 0
            while j < 30 and line_end > 0:
                out.append(Cand(Edit(line_end, line_end, ' */'), 0.5 + 0.02 * j, 'close comment', e0, 'lex-cmt'))
                line_end = src.find('\n', line_end + 1)
                j += 1
            out.append(Cand(Edit(c, c + 2, ''), 1.0, "delete '/*'", e0, 'lex-cmtdel'))
    if 'invalid suffix' in msg or 'invalid digit' in msg or 'exponent has no digits' in msg:
        i = min(e0, len(toks) - 1)
        if i >= 0 and toks[i].kind == lexer.NUMBER:
            t = toks[i]
            digits = re.sub(r'[^0-9.]', '', t.text)
            if digits.count('.') > 1:
                first = digits.find('.')
                digits = digits[:digits.find('.', first + 1)]
            if digits and digits != t.text:
                out.append(Cand(Edit(t.start, t.end, digits), 0.8, 'normalize number literal', i, 'lex-num'))
    for e, rule in brace_candidates(src):
        out.append(Cand(e, 1.0, rule, e0, 'brace'))
    if "expected '}'" in msg and pos >= len(src.rstrip()) - 1:
        out.append(Cand(Edit(len(src), len(src), '\n}\n'), 1.1, 'close block at end of file', e0, 'eof'))
    return out


def excisions(src, toks, e0):
    """Lossy last resort: delete the smallest line range around the error, then truncate and close open blocks."""
    out = []
    pos = toks[e0].start if e0 < len(toks) else max(0, len(src) - 1)
    line = line_of(src, pos)
    lines = src.split('\n')
    starts = [0] * (len(lines) + 1)
    for i in range(1, len(lines) + 1):
        starts[i] = starts[i - 1] + len(lines[i - 1]) + 1
    reach = (0, 1, 2, 4, 8, 16, 32)
    seen = set()
    for up in reach:
        for down in reach:
            first, last = max(1, line - up), min(len(lines), line + down)
            if (first, last) in seen:
                continue
            seen.add((first, last))
            s, e = starts[first - 1], min(len(src), starts[last])
            if any(t.kind == lexer.PP and s <= t.start < e for t in toks):
                continue
            out.append(Cand(Edit(s, e, ''), 10.0 + (e - s) * 0.001, f'delete lines {first}-{last}', e0, 'cut'))
    if e0 < len(toks):
        s = toks[e0].start
        e = src.find('\n', s)
        if e > s:
            out.append(Cand(Edit(s, e, ''), 9.0 + (e - s) * 0.001, f'delete rest of line {line}', e0, 'cut'))
    code = code_mask(src)
    depth_at = [0] * (len(lines) + 1)
    depth = 0
    for li, l in enumerate(lines):
        depth_at[li] = depth
        for i in range(starts[li], starts[li] + len(l)):
            if code[i] and src[i] == '{':
                depth += 1
            elif code[i] and src[i] == '}':
                depth -= 1
    for li in range(max(0, line - 1), -1, -1):
        prev = lines[li - 1].strip() if li > 0 else ''
        if li > 0 and not (prev.endswith(';') or prev.endswith('{') or prev.endswith('}') or prev == '' or prev.startswith('#')):
            continue
        close = ''.join('    ' * k + '}\n' for k in range(max(0, depth_at[li]) - 1, -1, -1))
        ls = starts[li]
        out.append(Cand(Edit(ls, len(src), close), 100.0 + (len(src) - ls) * 0.001,
                        f'truncate from line {li + 1} and close blocks', e0, 'cut'))
    return out


DIRECTIVES = ('include', 'define')


def repair_directives(src: str, headers):
    """Misspelled #include directives and header names (``#inlcude <stdoi.h>``), before the gate sees them."""
    log = []
    for t in reversed(lexer.lex(src)):
        if t.kind != lexer.PP:
            continue
        m = re.match(r'#(\s*)([A-Za-z_]*)(\s*)(.*)', t.text, re.S)
        if not m:
            continue
        name, rest = m.group(2), m.group(4)
        new_name = name
        if name not in ('include', 'define', 'undef', 'if', 'ifdef', 'ifndef', 'elif', 'else', 'endif', 'error', 'line'):
            close = [d for d in DIRECTIVES if distance(name, d) <= 2]
            if len(close) == 1:
                new_name = close[0]
        new_rest = rest
        if new_name == 'include':
            h = re.match(r'([<"])\s*([A-Za-z0-9_.]*?)\s*(\.h|,h|\.H)?\s*([>"])?\s*$', rest.strip())
            if h:
                base = h.group(2)
                if base not in headers:
                    close = sorted((distance(base, x), x) for x in headers)
                    if close and close[0][0] <= 2 and (len(close) == 1 or close[1][0] > close[0][0]):
                        base = close[0][1]
                if base in headers:
                    new_rest = f'<{base}.h>'
        if new_name != name or new_rest != rest:
            new = '#' + m.group(1) + new_name + (m.group(3) or ' ') + new_rest
            log.append({'phase': 'syntax', 'line': line_of(src, t.start), 'diagnostic': 'preprocessor directive',
                        'message': 'directive or header name', 'stage': 'lexical/structural',
                        'rule': f'repair directive', 'before': t.text, 'after': new})
            src = src[:t.start] + new + src[t.end:]
    return src, log


class SyntaxRepair:
    def __init__(self, budget_sec=90.0):
        self.front = cfront.front()
        self.log = []
        self.holes = []
        self.failure = None
        self.lossy = False
        self.parses = 0
        self.memo = {}
        self.budget = budget_sec
        self.cutoff = None

    def score(self, s: str) -> Score:
        m = self.memo.get(s)
        if m is not None:
            return m
        self.parses += 1
        u = self.front.parse(s)
        errs = u.syntax_errors
        offs = tuple(sorted(d.offset for d in u.errors))
        if not errs:
            bad = len(u.semantic_errors) + sum(1 for d in u.warnings if 'empty body' in d.message)
            r = Score(True, 0, 0, bad, offs)
        else:
            t = lexer.lex(s)
            r = Score(False, len(t) - lexer.index_at(t, errs[0].offset), len(errs), -1, offs)
        if len(self.memo) > 20000:
            self.memo.clear()
        self.memo[s] = r
        return r

    @staticmethod
    def moved(cutoff, edits):
        for e in edits:
            if cutoff >= e.end:
                cutoff += len(e.text) - (e.end - e.start)
            elif cutoff > e.start:
                cutoff = e.start + len(e.text)
        return cutoff

    def earlier_errors(self, base, s, edits) -> bool:
        """clang suppresses errors after a semantic error in the same statement, so an edit that creates a new error
        before the original error site can look like progress. Such edits are rejected."""
        if self.cutoff is None:
            return False
        return s.before(self.moved(self.cutoff, edits)) > base.before(self.cutoff)

    def best(self, src, toks, e0, base, k, cands):
        cands.sort(key=lambda c: c.cost)
        best = None
        accepted = []
        seen = set()
        evaluated = 0
        for c in cands:
            if best is not None:
                limit = min(effective(best.c, best.s), best.c.cost + 4.0)
                if c.cost >= limit - 1e-9 or (evaluated > 3000 and c.group != 'cut'):
                    break
            evaluated += 1
            try:
                ns = c.edit.apply(src)
            except Exception:
                continue
            if ns in seen:
                continue
            seen.add(ns)
            if not c.group.startswith('lex') and c.group != 'cut' and not local(toks, c.edit, ns):
                continue
            s = self.score(ns)
            if not accept(base, s, k) or (c.group != 'cut' and self.earlier_errors(base, s, [c.edit])):
                continue
            accepted.append(c)
            dist = abs(e0 - c.site)
            dist_b = 0 if best is None else abs(e0 - best.c.site)
            if better(best, c, s, dist, dist_b):
                best = Found(c, ns, s, [], [c])
        if best is None:
            return None
        ties = []
        for c in accepted:
            if abs(c.cost - best.c.cost) < 1e-9 and c.group == best.c.group:
                sc = self.score(c.edit.apply(src))
                if abs(effective(c, sc) - effective(best.c, best.s)) < 1e-9:
                    ties.append(c)
        best.ties = ties
        return best

    def beam(self, src, toks, d, e0, base, k):
        """Depth-2 search: expand the most promising non-accepted single edits by a second edit near their new error."""
        first = token_edits(src, toks, d, e0, 8, False, 4)
        partial = []
        seen = set()
        for c in first:
            ns = c.edit.apply(src)
            if ns in seen or not local(toks, c.edit, ns):
                continue
            seen.add(ns)
            s = self.score(ns)
            if not s.clean:
                partial.append((c, ns, s))
        partial.sort(key=lambda p: (p[2].after, p[0].cost))
        best, best_cost = None, float('inf')
        for c1, s1, _ in partial[:14]:
            errs = self.front.parse(s1).syntax_errors
            if not errs:
                continue
            t2 = lexer.lex(s1)
            e1 = lexer.index_at(t2, errs[0].offset)
            for c2 in token_edits(s1, t2, errs[0], e1, 6, False, 4):
                cost = c1.cost + c2.cost
                if cost >= best_cost:
                    continue
                ns = c2.edit.apply(s1)
                if ns in seen or not local(t2, c2.edit, ns):
                    continue
                seen.add(ns)
                s = self.score(ns)
                if accept(base, s, k) and not self.earlier_errors(base, s, [c1.edit, c2.edit]):
                    cost += TYPE_PENALTY * min(s.types, 5) if s.clean else 0
                    if cost >= best_cost:
                        continue
                    best_cost = cost
                    comb = Cand(c1.edit, cost, c1.rule + ' + ' + c2.rule, c1.site, 'pair')
                    best = Found(comb, ns, s, [], [c1, c2])
        return best

    def run(self, src: str) -> str:
        deadline = time.time() + self.budget
        if self.front.parse(src).syntax_errors:
            norm, changed = normalize_confusables(src)
            if norm != src:
                self.log.append({'phase': 'syntax', 'line': 0, 'diagnostic': 'non-ASCII characters',
                                 'message': 'confusable characters outside literals', 'stage': 'lexical/structural',
                                 'rule': f'normalize {changed} confusable characters (smart quotes, Unicode dashes, NBSP, '
                                         'full-width punctuation)', 'before': '', 'after': ''})
                src = norm
        stages = ['lexical/structural', 'priority tokens', 'full vocabulary', 'two edits', 'excision']
        for _ in range(400):
            u = self.front.parse(src)
            errs = u.syntax_errors
            if not errs:
                return src
            d = errs[0]
            toks = lexer.lex(src)
            e0 = lexer.index_at(toks, d.offset)
            base = Score(False, len(toks) - e0, len(errs), -1, tuple(sorted(x.offset for x in u.errors)))
            self.cutoff = d.offset
            k = max(1, min(8, base.after))
            f, stage = None, None
            late = time.time() > deadline
            lvl = 4 if late else 0
            while lvl < len(stages) and f is None:
                if lvl == 0:
                    f = self.best(src, toks, e0, base, k, lexical(src, toks, d, e0))
                elif lvl == 1:
                    f = self.best(src, toks, e0, base, k, token_edits(src, toks, d, e0, 6, False, 5))
                elif lvl == 2:
                    f = self.best(src, toks, e0, base, k, token_edits(src, toks, d, e0, 12, True, 6))
                elif lvl == 3:
                    f = self.beam(src, toks, d, e0, base, k)
                else:
                    f = self.best(src, toks, e0, base, 1, excisions(src, toks, e0))
                stage = stages[lvl]
                if f is not None and lvl < 2 and not (f.s.clean and f.s.types == 0 and f.c.cost <= 1.3) and not late:
                    g = self.best(src, toks, e0, base, k, token_edits(src, toks, d, e0, 8, True, 5))
                    if g is not None and effective(g.c, g.s) < effective(f.c, f.s) - 1e-9:
                        f, stage = g, stages[2]
                lvl += 1
            if f is None:
                self.failure = 'no syntax repair found for ' + d.brief()
                return src
            if stage == 'excision':
                self.lossy = True
            at = f.chain[0].edit.start
            m = {'phase': 'syntax', 'line': d.line, 'diagnostic': d.category, 'message': d.message, 'stage': stage,
                 'rule': ' + '.join(c.rule for c in f.chain), 'before': line_text(src, at),
                 'after': line_text(f.src, min(at, len(f.src)))}
            if len(f.ties) > 1:
                m['tied_alternatives'] = len(f.ties)
            if stage == 'excision':
                m['deleted'] = src[f.c.edit.start:f.c.edit.end]
            self.log.append(m)
            for c in f.chain:
                shift_all(self.holes, c.edit)
            if len(f.chain) == 1 and len(f.ties) > 1 and f.c.token is not None:
                e = f.c.edit
                hs = e.start + e.text.find(f.c.token)
                alts = []
                for t in f.ties:
                    if t.token is not None and t.token not in alts:
                        alts.append(t.token)
                if len(alts) > 1 and hs >= e.start:
                    self.holes.append(Hole(hs, hs + len(f.c.token), alts, 'syntax ' + d.message))
            src = f.src
        self.failure = 'syntax repair iteration limit'
        return src

    def type_step(self, src, errs):
        """Generic compile-error repair for errors the rule stage cannot fix: token edits around the first semantic
        error. The file must still parse and the number of errors must fall; key = edit cost + remaining errors."""
        for which in range(min(3, len(errs))):
            d = errs[which]
            toks = lexer.lex(src)
            e0 = min(lexer.index_at(toks, d.offset), max(0, len(toks) - 1))
            for lvl in range(4):
                if lvl == 0:
                    cands = token_edits(src, toks, d, e0, 6, False, 7)
                    cands += [Cand(e, 1.0, r, e0, 'brace') for e, r in brace_candidates(src)]
                elif lvl == 1:
                    cands = [c for c in token_edits(src, toks, d, e0, 8, True, 8)
                             if c.token is not None and (is_word(c.token[0]) or re.match(r'[0-9"]', c.token))
                             and c.token not in lexer.KEYWORDS]
                elif lvl == 2:
                    cands = token_edits(src, toks, d, e0, 8, True, 8)
                else:
                    cands = excisions(src, toks, e0)
                cands.sort(key=lambda c: c.cost)
                best, best_src, best_key = None, None, float('inf')
                seen, keys = set(), []
                tried = 0
                for c in cands:
                    if c.cost >= best_key or tried > 2500:
                        break
                    tried += 1
                    ns = c.edit.apply(src)
                    if ns in seen or (c.group != 'cut' and not local(toks, c.edit, ns)):
                        continue
                    seen.add(ns)
                    self.parses += 1
                    u = self.front.parse(ns)
                    if u.syntax_errors:
                        continue
                    cnt = len(u.errors)
                    if cnt >= len(errs):
                        continue
                    key = c.cost + cnt
                    keys.append((c, key))
                    if key < best_key:
                        best_key, best, best_src = key, c, ns
                if best is not None:
                    m = {'phase': 'types (token search)', 'line': d.line, 'diagnostic': d.category, 'message': d.message,
                         'rule': best.rule, 'before': line_text(src, best.edit.start),
                         'after': line_text(best_src, min(best.edit.start, len(best_src)))}
                    if lvl == 3:
                        m['deleted'] = src[best.edit.start:best.edit.end]
                    hole = None
                    if best.token is not None:
                        alts = []
                        for c, key in keys:
                            if abs(key - best_key) < 1e-9 and c.group == best.group and c.token is not None and c.token not in alts:
                                alts.append(c.token)
                        hs = best.edit.start + best.edit.text.find(best.token)
                        if len(alts) > 1 and hs >= best.edit.start:
                            hole = Hole(hs, hs + len(best.token), alts, 'types ' + d.message)
                            m['tied_alternatives'] = len(alts)
                    return best_src, m, lvl == 3, best.edit, hole
        return None
