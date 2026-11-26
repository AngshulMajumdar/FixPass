"""In-process clang front end (libclang): parse, diagnostics with fix-its, and the typed AST."""
from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass, field

import clang.cindex as ci

FILE = 'program.c'
SYNTAX_CATEGORIES = ('Parse Issue', 'Lexical or Preprocessor Issue')
STD = '-std=gnu11'


@functools.lru_cache(maxsize=1)
def builtin_include() -> str | None:
    """Directory with the compiler's own headers (stddef.h, stdarg.h, ...), which the pip libclang lacks."""
    env = os.environ.get('CFIXPASS_BUILTIN_INCLUDE')
    if env:
        return env
    for cc, flag in (('clang', '-print-resource-dir'), ('gcc', '-print-file-name=include'), ('cc', '-print-file-name=include')):
        if shutil.which(cc):
            try:
                out = subprocess.run([cc, flag], capture_output=True, text=True, timeout=20).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                continue
            path = os.path.join(out, 'include') if flag == '-print-resource-dir' else out
            if os.path.isfile(os.path.join(path, 'stddef.h')):
                return path
    return None


def args(extra=()):
    a = [STD, '-x', 'c', '-ferror-limit=60', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Wno-sign-compare',
         '-Wno-unused-variable', '-Wno-unused-but-set-variable', '-Wno-missing-field-initializers']
    inc = builtin_include()
    if inc:
        a += ['-isystem', inc]
    return a + list(extra)


@dataclass(frozen=True)
class Fixit:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class Diag:
    severity: int          # 2 warning, 3 error, 4 fatal
    category: str
    option: str            # e.g. -Wformat
    message: str
    offset: int
    line: int
    col: int
    fixits: tuple = ()
    ranges: tuple = ()     # ((start, end), ...)
    notes: tuple = ()      # ((message, (Fixit, ...)), ...) from attached notes

    @property
    def error(self):
        return self.severity >= 3

    @property
    def syntax(self):
        return self.error and self.category in SYNTAX_CATEGORIES

    def brief(self):
        return f'line {self.line}: {self.message}'


def _offset(loc, src_len):
    return max(0, min(loc.offset, src_len))


def _diags(tu, src):
    out = []
    for d in tu.diagnostics:
        loc = d.location
        if loc.file is None or os.path.basename(loc.file.name) != FILE:
            if d.severity >= 3:
                out.append(Diag(d.severity, d.category_name or '', d.option or '', d.spelling, 0, 0, 0))
            continue
        fx = tuple(Fixit(_offset(f.range.start, len(src)), _offset(f.range.end, len(src)), f.value) for f in d.fixits
                   if f.range.start.file is not None)
        rg = tuple((_offset(r.start, len(src)), _offset(r.end, len(src))) for r in d.ranges if r.start.file is not None)
        notes = []
        for ch in d.children:
            nfx = tuple(Fixit(_offset(f.range.start, len(src)), _offset(f.range.end, len(src)), f.value) for f in ch.fixits
                        if f.range.start.file is not None and os.path.basename(f.range.start.file.name) == FILE)
            notes.append((ch.spelling, nfx))
        out.append(Diag(d.severity, d.category_name or '', d.option or '', d.spelling, _offset(loc, len(src)), loc.line,
                        loc.column, fx, rg, tuple(notes)))
    return out


@dataclass
class Unit:
    src: str
    diags: list
    tu: object = None
    _children: dict = field(default_factory=dict)

    @property
    def errors(self):
        return [d for d in self.diags if d.error]

    @property
    def syntax_errors(self):
        return [d for d in self.diags if d.syntax]

    @property
    def semantic_errors(self):
        return [d for d in self.diags if d.error and not d.syntax]

    @property
    def warnings(self):
        return [d for d in self.diags if d.severity == 2]

    @property
    def cursor(self):
        return self.tu.cursor

    def in_file(self, c):
        loc = c.location
        return loc.file is not None and os.path.basename(loc.file.name) == FILE

    def text(self, c):
        return self.src[c.extent.start.offset:c.extent.end.offset]


class Front:
    """Fast repeated parsing: one translation unit is reparsed with a precompiled preamble (the #include lines)."""

    def __init__(self):
        self.index = ci.Index.create()
        self._tu = None
        self.parses = 0

    def parse(self, src: str) -> Unit:
        """Diagnostics only (the returned unit has no AST: the translation unit is reused for the next parse)."""
        self.parses += 1
        if self._tu is None:
            self._tu = self.index.parse(FILE, args=args(), unsaved_files=[(FILE, src)],
                                        options=ci.TranslationUnit.PARSE_PRECOMPILED_PREAMBLE |
                                        ci.TranslationUnit.PARSE_INCOMPLETE)
        else:
            self._tu.reparse(unsaved_files=[(FILE, src)])
        return Unit(src, _diags(self._tu, src))

    def analyze(self, src: str) -> Unit:
        """A fresh translation unit with the full AST; cursors stay valid while the unit is alive."""
        self.parses += 1
        tu = self.index.parse(FILE, args=args(), unsaved_files=[(FILE, src)],
                              options=ci.TranslationUnit.PARSE_INCOMPLETE)
        return Unit(src, _diags(tu, src), tu)


_front = None


def front() -> Front:
    global _front
    if _front is None:
        _front = Front()
    return _front


def walk(c):
    yield c
    for ch in c.get_children():
        yield from walk(ch)


def file_cursors(unit: Unit):
    """Top-level cursors that belong to the program file."""
    return [c for c in unit.cursor.get_children() if unit.in_file(c)]


def functions(unit: Unit):
    return [c for c in file_cursors(unit) if c.kind == ci.CursorKind.FUNCTION_DECL and c.is_definition()]


def signatures(unit: Unit):
    out = []
    for f in functions(unit):
        out.append((f.spelling, f.result_type.spelling, tuple(a.type.spelling for a in f.get_arguments())))
    return sorted(out)
