"""A tolerant C lexer: never fails, marks unterminated literals and comments as ERROR tokens."""
from __future__ import annotations

from dataclasses import dataclass

KEYWORDS = frozenset('''auto break case char const continue default do double else enum extern float for goto if inline int long
register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Bool bool
true false'''.split())

OPS = sorted('''>>= <<= ... -> ++ -- << >> <= >= == != && || += -= *= /= %= &= ^= |= ## + - * / % & | ^ ! ~ < > = ? : ; , . ( ) [ ] { } #'''
             .split(), key=len, reverse=True)

IDENT, KEYWORD, NUMBER, STRING, CHAR, OP, PP, ERROR = 'ident', 'keyword', 'number', 'string', 'char', 'op', 'pp', 'error'


@dataclass(frozen=True)
class Tok:
    kind: str
    text: str
    start: int
    end: int
    unterminated: bool = False


def _ident_start(c):
    return c.isalpha() or c == '_'


def _ident_part(c):
    return c.isalnum() or c == '_'


def lex(src: str):
    toks = []
    i, n = 0, len(src)
    at_line_start = True
    while i < n:
        c = src[i]
        if c == '\n':
            at_line_start = True
            i += 1
            continue
        if c.isspace():
            i += 1
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            i = n if j < 0 else j
            continue
        if c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            if j < 0:
                toks.append(Tok(ERROR, src[i:i + 2], i, i + 2, True))
                i += 2
                continue
            i = j + 2
            continue
        if c == '#' and at_line_start:
            # a preprocessor directive is one token (line splices included)
            j = i
            while True:
                k = src.find('\n', j)
                if k < 0:
                    k = n
                    break
                if k > 0 and src[k - 1] == '\\':
                    j = k + 1
                    continue
                break
            text = src[i:k]
            # strip a trailing // comment from the token text
            toks.append(Tok(PP, text.rstrip(), i, i + len(text.rstrip())))
            i = k
            continue
        at_line_start = False
        if _ident_start(c):
            j = i + 1
            while j < n and _ident_part(src[j]):
                j += 1
            # string/char prefixes L"", u8"" etc.
            if j < n and src[j] in '"\'' and src[i:j] in ('L', 'u', 'U', 'u8'):
                i0 = i
                i = j
                tok = _quoted(src, i, src[j])
                toks.append(Tok(tok.kind, src[i0:tok.end], i0, tok.end, tok.unterminated))
                i = tok.end
                continue
            w = src[i:j]
            toks.append(Tok(KEYWORD if w in KEYWORDS else IDENT, w, i, j))
            i = j
            continue
        if c.isdigit() or (c == '.' and i + 1 < n and src[i + 1].isdigit()):
            # pp-number: digits, letters, '_', '.', and a sign after an exponent letter
            hexnum = src[i:i + 2].lower() == '0x'
            j = i + 1
            while j < n:
                ch = src[j]
                if _ident_part(ch) or ch == '.':
                    j += 1
                elif ch in '+-' and (src[j - 1] in 'pP' or (src[j - 1] in 'eE' and not hexnum)):
                    j += 1
                else:
                    break
            toks.append(Tok(NUMBER, src[i:j], i, j))
            i = j
            continue
        if c in '"\'':
            tok = _quoted(src, i, c)
            toks.append(tok)
            i = tok.end
            continue
        for op in OPS:
            if src.startswith(op, i):
                toks.append(Tok(OP, op, i, i + len(op)))
                i += len(op)
                break
        else:
            toks.append(Tok(ERROR, c, i, i + 1))
            i += 1
    return toks


def _quoted(src, i, q):
    n = len(src)
    j = i + 1
    while j < n:
        if src[j] == '\\':
            j += 2
            continue
        if src[j] == q:
            return Tok(STRING if q == '"' else CHAR, src[i:j + 1], i, j + 1)
        if src[j] == '\n':
            break
        j += 1
    j = min(j, n)
    return Tok(ERROR, src[i:j], i, j, True)


def index_at(toks, pos: int) -> int:
    """Index of the first token that ends after ``pos`` (len(toks) if none)."""
    lo, hi = 0, len(toks)
    while lo < hi:
        mid = (lo + hi) // 2
        if toks[mid].end <= pos:
            lo = mid + 1
        else:
            hi = mid
    return lo
