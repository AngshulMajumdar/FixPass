"""Source instrumentation: statement coverage, crash site, and fuel (deterministic termination).

Every statement gets a probe ``__cc_hit(k);``. Every loop body and function body starts with ``__cc_tick();``, which
spends one unit of fuel; when the fuel is gone the program records where it was and exits with status 97, so a
non-terminating candidate fails deterministically instead of being killed by a wall clock. On exit, on a crash signal
and on fuel exhaustion, the hit counters and the last statement reached are written to an inherited file descriptor.
"""
from __future__ import annotations

from dataclasses import dataclass

import clang.cindex as ci

from . import cfront

K = ci.CursorKind
LOOPS = (K.FOR_STMT, K.WHILE_STMT, K.DO_STMT)
LABELS = (K.CASE_STMT, K.DEFAULT_STMT, K.LABEL_STMT)

FUEL_STATUS = 97

SYM = r'''#ifdef __APPLE__
#define __CC_SYM(x) "_" x
#else
#define __CC_SYM(x) x
#endif
'''

PRELUDE = SYM + r'''/* cfixpass instrumentation */
static unsigned int __cc_hits[%(n)d + 1];
static int __cc_last = -1;
static int __cc_fd = -1;
static unsigned long long __cc_fuel = 50000000ULL;
extern long __cc_write(int, const void *, unsigned long) __asm__(__CC_SYM("write"));
extern char *__cc_getenv(const char *) __asm__(__CC_SYM("getenv"));
extern void __cc__exit(int) __asm__(__CC_SYM("_exit"));
extern int __cc_atexit(void (*)(void)) __asm__(__CC_SYM("atexit"));
extern void (*__cc_signal(int, void (*)(int)))(int) __asm__(__CC_SYM("signal"));
extern int __cc_fflush(void *) __asm__(__CC_SYM("fflush"));
static void __cc_dump(int why) {
    if (__cc_fd >= 0) {
        int h[4];
        unsigned long long left = __cc_fuel;
        h[0] = %(n)d; h[1] = __cc_last; h[2] = why; h[3] = 0;
        __cc_write(__cc_fd, h, sizeof h);
        __cc_write(__cc_fd, &left, sizeof left);
        __cc_write(__cc_fd, __cc_hits, sizeof(unsigned int) * %(n)d);
        __cc_fd = -1;
    }
}
static void __cc_on_exit(void) { __cc_dump(0); }
static void __cc_on_signal(int s) { __cc_dump(s); __cc__exit(128 + s); }
__attribute__((constructor)) static void __cc_init(void) {
    const char *p = __cc_getenv("CFIXPASS_TRACE_FD");
    const char *f = __cc_getenv("CFIXPASS_FUEL");
    if (p) { int v = 0; while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0'); __cc_fd = v; }
    if (f) { unsigned long long v = 0; while (*f >= '0' && *f <= '9') v = v * 10 + (unsigned long long)(*f++ - '0'); __cc_fuel = v; }
    __cc_atexit(__cc_on_exit);
    __cc_signal(11, __cc_on_signal); __cc_signal(8, __cc_on_signal);
    __cc_signal(6, __cc_on_signal); __cc_signal(4, __cc_on_signal);
}
static void __cc_tick(void) {
    if (__cc_fuel-- == 0) { __cc_fflush(0); __cc_dump(-1); __cc__exit(%(status)d); }
}
#define __cc_hit(k) (__cc_last = (k), __cc_hits[k]++)
'''

PRELUDE_FUEL_ONLY = SYM + r'''/* cfixpass instrumentation */
static unsigned long long __cc_fuel = 50000000ULL;
extern char *__cc_getenv(const char *) __asm__(__CC_SYM("getenv"));
extern void __cc__exit(int) __asm__(__CC_SYM("_exit"));
extern int __cc_fflush(void *) __asm__(__CC_SYM("fflush"));
__attribute__((constructor)) static void __cc_init(void) {
    const char *f = __cc_getenv("CFIXPASS_FUEL");
    if (f) { unsigned long long v = 0; while (*f >= '0' && *f <= '9') v = v * 10 + (unsigned long long)(*f++ - '0'); __cc_fuel = v; }
}
static void __cc_tick(void) {
    if (__cc_fuel-- == 0) { __cc_fflush(0); __cc__exit(%(status)d); }
}
'''


@dataclass(frozen=True)
class Stmt:
    id: int
    start: int
    end: int
    line: int
    function: str


def stmt_end(src, c):
    """End offset of a statement, including a trailing ';' that libclang leaves out of expression statements."""
    e = c.extent.end.offset
    if c.kind == K.COMPOUND_STMT:
        return e
    j = e
    while j < len(src) and src[j] in ' \t\r\n':
        j += 1
    if j < len(src) and src[j] == ';' and (e == 0 or src[e - 1] != ';'):
        return j + 1
    return e


class Instrumented:
    def __init__(self, src, unit, coverage=True):
        self.src = src
        self.stmts = []
        ins = []          # (pos, order, text) insertions in original coordinates
        self.ok = True
        for fn in cfront.functions(unit):
            body = next((c for c in fn.get_children() if c.kind == K.COMPOUND_STMT), None)
            if body is None:
                continue
            ins.append((body.extent.start.offset + 1, 0, ' __cc_tick();'))
            self._block(body, fn.spelling, ins, coverage)
        if not self.ok:
            return
        n = len(self.stmts)
        prelude = (PRELUDE % {'n': max(1, n), 'status': FUEL_STATUS}) if coverage else \
            (PRELUDE_FUEL_ONLY % {'status': FUEL_STATUS})
        out = src
        for pos, order, text in sorted(ins, key=lambda x: (x[0], x[1]), reverse=True):
            out = out[:pos] + text + out[pos:]
        self.text = prelude + '#line 1\n' + out

    def _probe(self, c, fname, ins, coverage):
        if not coverage:
            return
        k = len(self.stmts)
        self.stmts.append(Stmt(k, c.extent.start.offset, stmt_end(self.src, c), c.extent.start.line, fname))
        ins.append((c.extent.start.offset, 2, f'__cc_hit({k}); '))

    def _statement(self, c, fname, ins, coverage, wrap):
        """Instrument statement ``c``; ``wrap``: it is the sole body of an if/else/loop and needs braces for a probe."""
        if c.kind == K.NULL_STMT:
            return
        if c.kind in LABELS:
            kids = list(c.get_children())
            sub = kids[-1] if kids else None
            if sub is not None and (c.kind != K.CASE_STMT or len(kids) >= 2):
                self._statement(sub, fname, ins, coverage, False)
            return
        if c.kind == K.COMPOUND_STMT:
            self._block(c, fname, ins, coverage)
            return
        if wrap:
            ins.append((c.extent.start.offset, 1, '{ '))
            ins.append((stmt_end(self.src, c), 9, ' }'))
        self._probe(c, fname, ins, coverage)
        kids = list(c.get_children())
        if c.kind == K.IF_STMT:
            for b in kids[1:]:
                self._statement(b, fname, ins, coverage, b.kind != K.COMPOUND_STMT)
        elif c.kind in LOOPS:
            body = kids[0] if c.kind == K.DO_STMT else kids[-1]
            if body.kind == K.COMPOUND_STMT:
                ins.append((body.extent.start.offset + 1, 0, ' __cc_tick();'))
                self._block(body, fname, ins, coverage)
            elif body.kind == K.NULL_STMT:
                # 'while (cond);' -> 'while (cond){ __cc_tick(); };' : the empty body still spends fuel
                ins.append((body.extent.start.offset, 1, '{ __cc_tick(); }'))
            else:
                ins.append((body.extent.start.offset, 1, '{ __cc_tick(); '))
                ins.append((stmt_end(self.src, body), 9, ' }'))
                self._statement(body, fname, ins, coverage, False)
        elif c.kind == K.SWITCH_STMT:
            body = kids[-1]
            if body.kind == K.COMPOUND_STMT:
                self._block(body, fname, ins, coverage)

    def _block(self, block, fname, ins, coverage):
        for c in block.get_children():
            self._statement(c, fname, ins, coverage, False)


def instrument(src, unit, coverage=True):
    return Instrumented(src, unit, coverage)
