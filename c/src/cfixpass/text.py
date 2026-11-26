"""Text edits, positions and similarity measures."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str

    def apply(self, src: str) -> str:
        return src[:self.start] + self.text + src[self.end:]

    def shift(self, pos: int) -> int:
        """Where position ``pos`` of the old text lands after this edit (None if it was inside the edit)."""
        if pos <= self.start:
            return pos
        if pos >= self.end:
            return pos + len(self.text) - (self.end - self.start)
        return -1


def apply_all(src: str, edits) -> str:
    """Apply non-overlapping edits given in old-text positions."""
    for e in sorted(edits, key=lambda e: (e.start, e.end), reverse=True):
        src = e.apply(src)
    return src


def line_of(src: str, pos: int) -> int:
    return src.count('\n', 0, max(0, min(pos, len(src)))) + 1


def line_start(src: str, pos: int) -> int:
    return src.rfind('\n', 0, max(0, pos)) + 1


def line_text(src: str, pos: int) -> str:
    a = line_start(src, pos)
    b = src.find('\n', a)
    return src[a:b if b >= 0 else len(src)].strip()


def code_mask(src: str):
    """True for characters that are C code (not inside comments, string or character literals)."""
    n = len(src)
    mask = [True] * n
    i = 0
    while i < n:
        c = src[i]
        if c == '/' and i + 1 < n and src[i + 1] == '/':
            j = src.find('\n', i)
            j = n if j < 0 else j
            for k in range(i, j):
                mask[k] = False
            i = j
        elif c == '/' and i + 1 < n and src[i + 1] == '*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                mask[k] = False
            i = j
        elif c in '"\'':
            j = i + 1
            while j < n and src[j] != c and src[j] != '\n':
                j += 2 if src[j] == '\\' else 1
            j = min(n, j + 1)
            for k in range(i, j):
                mask[k] = False
            i = j
        else:
            i += 1
    return mask


def distance(a: str, b: str) -> int:
    """Optimal string alignment distance (adjacent transpositions count 1)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def normalize_output(s: str) -> str:
    """Output comparison as with diff -ZB: trailing whitespace on each line and blank lines are ignored."""
    return '\n'.join(l.rstrip() for l in s.replace('\r\n', '\n').split('\n') if l.strip())


def line_similarity(got: str, want: str) -> float:
    """Longest common subsequence of lines over the longer line count, in [0, 1]."""
    a = [l.strip() for l in normalize_output(got).split('\n')] if normalize_output(got) else []
    b = [l.strip() for l in normalize_output(want).split('\n')] if normalize_output(want) else []
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if len(a) * len(b) > 4_000_000:
        a, b = a[:2000], b[:2000]
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            cur[j] = prev[j - 1] + 1 if ai == b[j - 1] else max(prev[j], cur[j - 1])
        prev = cur
    return prev[len(b)] / max(len(a), len(b))


class Hole:
    """A tied choice made by a compile stage (several repairs removed the error equally well), revisitable by tests."""

    def __init__(self, start, end, alternatives, reason):
        self.start, self.end, self.alternatives, self.reason = start, end, list(alternatives), reason

    def shift(self, e: Edit) -> bool:
        """Move this hole across edit ``e``; False if the edit overlaps it (the hole is then dropped)."""
        if e.start == self.start and e.end == self.end:
            self.end = self.start + len(e.text)
            return True
        if e.end <= self.start:
            d = len(e.text) - (e.end - e.start)
            self.start += d
            self.end += d
            return True
        return e.start >= self.end

    def copy(self):
        return Hole(self.start, self.end, self.alternatives, self.reason)


def shift_all(holes, e: Edit):
    holes[:] = [h for h in holes if h.shift(e)]
