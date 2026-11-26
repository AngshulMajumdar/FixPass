"""Pipeline: gate -> unchanged check -> compile repair (syntax + semantic) -> warning rules -> test-driven search ->
final compilation and validation. Success artifacts are written only when every supplied test passes."""
from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import __version__, cfront, gate, instrument, mutations, rules, runner
from .compile import CompileRepair
from .syntax import repair_directives
from .text import Edit, Hole, line_of, line_text, shift_all

ROUND_BUDGET = int(os.environ.get('CFIXPASS_ROUND_BUDGET', 2500))
WINDOW = int(os.environ.get('CFIXPASS_WINDOW', 80))
MAX_ROUNDS = int(os.environ.get('CFIXPASS_MAX_ROUNDS', 30))
PAIR_POOL = int(os.environ.get('CFIXPASS_PAIR_POOL', 24))
BACKTRACK_BUDGET = int(os.environ.get('CFIXPASS_BACKTRACK_BUDGET', 400))


class Eval:
    def __init__(self, n):
        self.res = [None] * n
        self.score = 0.0
        self.passed = set()
        self.timeouts = set()
        self.aborted = False

    def all_pass(self, n):
        return len(self.passed) == n


class Hit:
    def __init__(self, cs, src, e, s, rank):
        self.cs, self.src, self.e, self.s, self.rank = cs, src, e, s, rank


class Engine:
    def __init__(self, work, tests, time_budget=None):
        self.work = work
        self.tests = tests
        self.report = {}
        self.t0 = time.time()
        self.budget = float(time_budget if time_budget is not None else os.environ.get('CFIXPASS_TIME_BUDGET', 1800))
        self.cache = {}
        self.counter = 0
        self.evaluated = 0
        self.compiles = 0
        self.test_runs = 0
        self.fuel = 50_000_000
        self.timeout = 2.0
        self.metered = True
        self.ambiguous_rounds = 0
        self.last_ties = []
        self.amplified = None
        self.tmp = None
        self.front = cfront.front()
        self.lock = threading.Lock()
        self.workers = max(1, int(os.environ.get('CFIXPASS_JOBS', os.cpu_count() or 1)))
        self.pool = ThreadPoolExecutor(self.workers)

    # ------------------------------------------------------------------ execution

    def all(self):
        return set(range(len(self.tests)))

    def out_of_time(self):
        return time.time() - self.t0 > self.budget

    def prepare(self, src, coverage=False):
        """Instrumented text for ``src`` (main thread: libclang is not thread-safe). None if it does not compile."""
        if not (self.metered or coverage):
            return src, None
        unit = self.front.analyze(src)
        if unit.errors:
            return None, None
        inst = instrument.instrument(src, unit, coverage)
        return inst.text, inst

    def build(self, src, coverage=False):
        """Compile ``src`` (instrumented unless final). Returns (binary, Instrumented or None) or (None, None)."""
        exe, inst = self.prepare(src, coverage)
        if exe is None:
            return None, None
        path = self.compile_exe(exe)
        if path is None and inst is not None and coverage:
            return self.build(src, False)
        return path, inst

    def compile_exe(self, exe):
        with self.lock:
            self.counter += 1
            path = os.path.join(self.tmp, f'c{self.counter}')
            self.compiles += 1
        ok, _ = runner.compile_c(exe, path)
        return path if ok else None

    def run_tests(self, binary, inst, must_pass, coverage, tests):
        e = Eval(len(tests))
        e.inst = inst
        order = sorted(must_pass) + [i for i in range(len(tests)) if i not in must_pass]
        try:
            for i in order:
                o = runner.run(binary, tests[i], i, self.timeout, self.fuel if (self.metered or coverage) else None,
                               trace=coverage and inst is not None and bool(inst.stmts))
                with self.lock:
                    self.test_runs += 1
                e.res[i] = o
                e.score += o.score()
                if o.passed:
                    e.passed.add(i)
                if o.timeout:
                    e.timeouts.add(i)
                if i in must_pass and not o.passed:
                    e.aborted = True
                    break
        finally:
            for ext in ('', '.c'):
                try:
                    os.unlink(binary + ext)
                except OSError:
                    pass
        return e

    def evaluate(self, src, must_pass=frozenset(), coverage=False, tests=None):
        return self.evaluate_many([src], must_pass, coverage, tests)[0]

    def evaluate_many(self, srcs, must_pass=frozenset(), coverage=False, tests=None):
        """Evaluate candidates in parallel (compilation and test runs); results in the order given."""
        tests = self.tests if tests is None else tests
        cacheable = not coverage and not must_pass and tests is self.tests
        out = [None] * len(srcs)
        jobs = []
        for k, src in enumerate(srcs):
            if cacheable and src in self.cache:
                out[k] = self.cache[src]
                continue
            exe, inst = self.prepare(src, coverage)
            if exe is not None:
                jobs.append((k, exe, inst))

        def work(job):
            k, exe, inst = job
            path = self.compile_exe(exe)
            if path is None:
                return k, None, coverage and inst is not None
            return k, self.run_tests(path, inst, must_pass, coverage, tests), False

        results = list(self.pool.map(work, jobs)) if len(jobs) > 1 else [work(j) for j in jobs]
        for k, e, retry in results:
            if retry:
                e = self.evaluate(srcs[k], must_pass, False, tests)
            out[k] = e
            if cacheable and e is not None:
                self.cache[srcs[k]] = e
        return out

    # ------------------------------------------------------------------ pipeline

    def write(self, name, content):
        with open(os.path.join(self.work, name), 'w') as f:
            f.write(content)

    def run(self, original, out_binary=None):
        os.makedirs(self.work, exist_ok=True)
        self.tmp = tempfile.mkdtemp(prefix='tmp-', dir=self.work)
        self.write('original.c', original)
        self.report['tool'] = f'cfixpass {__version__}'
        self.report['tests'] = len(self.tests)
        self.report['compiler'] = runner.compiler()
        try:
            return self.pipeline(original, out_binary)
        finally:
            self.pool.shutdown(wait=True)
            shutil.rmtree(self.tmp, ignore_errors=True)

    def pipeline(self, original, out_binary):
        src, dlog = repair_directives(original, gate.HEADERS)
        reason = gate.check(src)
        if reason is not None:
            return self.finish(2, 'rejected: ' + reason, original, None)
        u0 = self.front.analyze(src)
        self.report['original_compiles'] = not u0.errors and not dlog
        self.report['original_diagnostics'] = [d.brief() for d in u0.errors][:50]
        if not u0.errors and not dlog:
            e = self.evaluate(src) if self.tests else None
            if not self.tests:
                safe = [f for f in rules.findings(u0) if f.safe]
                if not safe:
                    self.report['original_passes'] = True
                    return self.finalize(original, src, out_binary, None)
            elif e is not None and e.all_pass(len(self.tests)):
                self.report['original_passes'] = True
                return self.finalize(original, src, out_binary, None)
        cr = CompileRepair()
        src = cr.run(src)
        self.report['compile_repairs'] = dlog + cr.log
        self.report['compile_candidates_checked'] = cr.candidates_tried
        if cr.lossy:
            self.report['lossy_syntax_repair'] = True
        if cr.failure:
            return self.finish(1, cr.failure, src, None)
        holes = cr.holes
        src = self.static_rules(src, holes)
        if self.tests:
            src = self.search(src, holes)
        return self.finalize(original, src, out_binary, cr.signatures)

    def static_rules(self, src, holes):
        log = []
        decided = set()
        for _ in range(40):
            unit = self.front.analyze(src)
            fs = [f for f in rules.findings(unit) if (f.rule, line_text(src, f.alternatives[0].start)) not in decided]
            if not fs:
                break
            f = fs[0]
            decided.add((f.rule, line_text(src, f.alternatives[0].start)))
            entry = {'line': f.line, 'rule': f.rule, 'before': line_text(src, f.alternatives[0].start)}
            if self.tests:
                before = self.evaluate(src)
                best, best_e, ties = None, None, []
                for alt in f.alternatives:
                    ns = alt.apply(src)
                    if self.front.parse(ns).errors:
                        continue
                    e = self.evaluate(ns)
                    if e is None or before is None or not e.passed >= before.passed or e.score < before.score - 1e-9:
                        continue
                    if best_e is None or e.score > best_e.score + 1e-9:
                        best, best_e, ties = alt, e, [alt]
                    elif abs(e.score - best_e.score) <= 1e-9:
                        ties.append(alt)
                keep = best is not None
            else:
                best, ties, keep = (f.alternatives[0], [f.alternatives[0]], True) if f.safe else (None, [], False)
            entry['applied'] = keep
            if keep:
                ns = best.apply(src)
                entry['after'] = line_text(ns, best.start)
                shift_all(holes, best)
                if len(ties) > 1 and all((t.start, t.end) == (best.start, best.end) for t in ties):
                    holes.append(Hole(best.start, best.start + len(best.text), [t.text for t in ties], f.rule))
                    entry['tied_alternatives'] = len(ties)
                src = ns
            log.append(entry)
        self.report['static_rules'] = log
        return src

    # ------------------------------------------------------------------ search

    def rank(self, base, base_eval, holes):
        cov = self.evaluate(base, coverage=True)
        susp, stmts = None, []
        if cov is not None and cov.inst is not None and cov.inst.stmts:
            stmts = cov.inst.stmts
            n = len(stmts)
            failing_total = len(self.tests) - len(base_eval.passed)
            ef = [0] * n
            ep = [0] * n
            for o in cov.res:
                if o is None or o.hits is None:
                    continue
                for k in range(min(n, len(o.hits))):
                    if o.hits[k]:
                        if o.index in base_eval.passed:
                            ep[k] += 1
                        else:
                            ef[k] += 1
            susp = [ef[k] / math.sqrt(failing_total * (ef[k] + ep[k])) if failing_total and ef[k] else 0.0 for k in range(n)]
            self.crash_boost(cov, base_eval, stmts, susp)
        unit = self.front.analyze(base)
        cands = mutations.generate(unit)
        for h in holes:
            now = base[h.start:h.end]
            for alt in h.alternatives:
                if alt != now:
                    cands.append(mutations.Cand(Edit(h.start, h.end, alt), 1, f'revisit compile choice ({h.reason})', h.start))
        ranked = []
        for c in cands:
            if susp is None:
                ranked.append((c, 0.001, c.anchor))
                continue
            sid = self.stmt_at(stmts, c.anchor)
            sc = susp[sid] if sid >= 0 else 0.0
            if sc > 0:
                ranked.append((c, sc, stmts[sid].start))
        ranked.sort(key=lambda r: (-r[1], r[2], r[0].cost, r[0].anchor))
        return ranked

    @staticmethod
    def stmt_at(stmts, pos):
        best, size = -1, None
        for s in stmts:
            if s.start <= pos < s.end and (size is None or s.end - s.start < size):
                best, size = s.id, s.end - s.start
        return best

    def crash_boost(self, cov, base_eval, stmts, susp):
        """Failing tests that crash or run out of fuel point at a statement: +2 for it, +1 for the rest of its
        function, each weighted by the fraction of failing tests with that crash site."""
        failing = len(self.tests) - len(base_eval.passed)
        if not failing:
            return
        bonus = [0.0] * len(susp)
        for o in cov.res:
            if o is None or o.index in base_eval.passed or o.crash is None or o.last < 0 or o.last >= len(stmts):
                continue
            top = stmts[o.last]
            for i, s in enumerate(stmts):
                b = 2.0 if s.id == top.id else 1.0 if s.function == top.function else 0.0
                bonus[i] += b / failing
        for i in range(len(susp)):
            if susp[i] > 0:
                susp[i] += bonus[i]

    def scan(self, base, ranked, cur, pre, tried, viable, budget, stats, strict=True):
        """Evaluate ranked single edits on ``base`` (optionally after ``pre``, an undo) in rank order, ``workers`` at a
        time; return the best edit that beats ``cur``. Non-improving, non-regressing edits are collected in ``viable``."""
        best = None
        first_at = -1
        n = 0
        self.last_ties = []
        k = 0
        must = frozenset(cur.passed) if strict else frozenset()
        while k < len(ranked):
            if n >= budget or (first_at >= 0 and n - first_at > WINDOW) or self.out_of_time():
                break
            batch = []
            while k < len(ranked) and len(batch) < self.workers:
                c, s, _ = ranked[k]
                ns = c.edit.apply(base)
                if ns not in tried:
                    tried.add(ns)
                    batch.append((k, c, s, ns))
                k += 1
            if not batch:
                continue
            evals = self.evaluate_many([b[3] for b in batch], must)
            for (kk, c, s, ns), e in zip(batch, evals):
                n += 1
                self.evaluated += 1
                stats[0] += 1
                if e is None:
                    stats[1] += 1
                    continue
                if e.aborted or (not strict and len(e.passed) < len(cur.passed)):
                    continue
                cs = [c] if pre is None else [pre, c]
                h = Hit(cs, ns, e, s, kk + 1)
                if e.score > cur.score + 1e-9:
                    if first_at < 0:
                        first_at = n
                    self.last_ties.append(h)
                    if best is None or e.score > best.e.score + 1e-9 or (
                            abs(e.score - best.e.score) <= 1e-9 and c.cost < best.cs[-1].cost):
                        best = h
                elif viable is not None and e.score >= cur.score - 1e-9:
                    viable.append(h)
        if best is not None:
            self.last_ties = [x for x in self.last_ties if abs(x.e.score - best.e.score) <= 1e-9]
        return best

    def amplified_inputs(self):
        if self.amplified is not None:
            return self.amplified
        self.amplified = []
        rnd = random.Random(7)
        seen = {t.input for t in self.tests}
        num = re.compile(r'(?<![\w.])\d{1,6}(?![\w.])')
        for _ in range(8):
            if len(self.amplified) >= 60:
                break
            for t in self.tests:
                ls = t.input.split('\n')
                if len(ls) < 3:
                    continue
                v = list(ls)
                k = 1 + rnd.randrange(max(1, len(v) - 2))
                choice = rnd.randrange(5)
                if choice in (3, 4):
                    for _ in range(3):
                        li = rnd.randrange(len(v))
                        spans = [m.span() for m in num.finditer(v[li])]
                        if not spans:
                            continue
                        a, b = spans[rnd.randrange(len(spans))]
                        val = int(v[li][a:b])
                        r = rnd.randrange(3)
                        nv = val + 1 + rnd.randrange(9) if r == 0 else val * 3 + 1 if r == 1 else max(0, val - 1 - rnd.randrange(5))
                        v[li] = v[li][:a] + str(nv) + v[li][b:]
                elif choice == 0:
                    del v[k]
                elif choice == 1:
                    v.insert(k, v[k])
                elif k + 1 < len(v):
                    v[k], v[k + 1] = v[k + 1], v[k]
                inp = '\n'.join(v)
                if inp not in seen:
                    seen.add(inp)
                    self.amplified.append(runner.Test(inp, ''))
        return self.amplified

    def disambiguate(self, best, log):
        ties = list(self.last_ties)
        if len(ties) < 2:
            return best
        if len(ties) > 8:
            ties = ties[:8]
            if best not in ties:
                ties.append(best)
        amp = self.amplified_inputs()
        groups = {}
        if amp:
            for h in ties:
                binary, _ = self.build(h.src)
                if binary is None:
                    continue
                sig = []
                for i, t in enumerate(amp):
                    o = runner.run(binary, t, i, self.timeout, self.fuel, False)
                    self.test_runs += 1
                    sig.append(('!' if o.crash else '') + o.out)
                for ext in ('', '.c'):
                    try:
                        os.unlink(binary + ext)
                    except OSError:
                        pass
                groups.setdefault('\x00'.join(sig), []).append(h)
        log['equal_score_alternatives'] = len(ties)
        log['alternatives'] = [f'{h.cs[-1].op} @L{line_of(h.src, h.cs[-1].edit.start)}: {line_text(h.src, h.cs[-1].edit.start)}'
                               for h in ties]
        if not groups:
            return best
        gs = sorted(groups.values(), key=len, reverse=True)
        top = gs[0]
        chosen = top[0]
        for h in top:
            if h.cs[-1].cost < chosen.cs[-1].cost or (h.cs[-1].cost == chosen.cs[-1].cost and h.rank < chosen.rank):
                chosen = h
        log['behaviour_groups'] = [len(g) for g in gs]
        if len(gs) > 1:
            log['ambiguous'] = True
            log['resolved_by'] = 'largest behaviour group' if len(gs[1]) < len(top) else 'lowest cost (tie)'
            self.ambiguous_rounds += 1
        return chosen

    def search(self, src, holes):
        rounds = []
        self.report['search'] = rounds
        cur = self.evaluate(src, coverage=True)
        if cur is None:
            self.report['search_stopped'] = 'candidate does not compile with ' + runner.compiler()
            return src
        max_ms = max([o.ms for o in cur.res if o is not None and not o.timeout] or [0])
        self.timeout = max(0.5, min(2.0, 20 * max_ms / 1000.0))
        max_fuel = max([o.fuel_used for o in cur.res if o is not None and o.crash != 'fuel exhausted'] or [0])
        self.fuel = max(1_000_000, min(50_000_000, 50 * max_fuel))
        self.report['fuel_limit'] = self.fuel
        self.report['test_timeout_ms'] = round(self.timeout * 1000)
        self.cache.clear()
        cur = self.evaluate(src)
        if cur is None:
            self.report['search_stopped'] = 'candidate does not compile'
            return src
        rounds.append({'initial_passing': len(cur.passed), 'initial_score': round(cur.score, 3)})
        tried = set()
        undo = []
        for rnd in range(1, MAX_ROUNDS + 1):
            if cur.all_pass(len(self.tests)):
                break
            if self.out_of_time():
                self.report['search_stopped'] = 'time budget'
                break
            ranked = self.rank(src, cur, holes)
            viable = []
            stats = [0, 0]
            best = self.scan(src, ranked, cur, None, tried, viable, ROUND_BUDGET, stats)
            log = {}
            if best is not None:
                best = self.disambiguate(best, log)
            bstats = [0, 0]
            bt_log = []
            if best is None and undo:
                for ui in range(len(undo) - 1, -1, -1):
                    if best is not None or self.out_of_time():
                        break
                    h = undo[ui]
                    ue = Edit(h.start, h.end, h.alternatives[0])
                    uc = mutations.Cand(ue, 1, 'undo earlier edit', h.start)
                    base = ue.apply(src)
                    be = self.evaluate(base)
                    bl = {'undo_line': line_text(base, ue.start), 'base_score': None if be is None else round(be.score, 3)}
                    bt_log.append(bl)
                    if be is None:
                        continue
                    before = bstats[0]
                    shifted = []
                    for x in holes:
                        y = x.copy()
                        if y.shift(ue):
                            shifted.append(y)
                    br = self.rank(base, be, shifted)
                    hb = self.scan(base, br, cur, uc, tried, None, BACKTRACK_BUDGET, bstats, strict=False)
                    bl['ranked'] = len(br)
                    bl['evaluated'] = bstats[0] - before
                    if hb is not None:
                        best = hb
                        del undo[ui]
            pairs = 0
            if best is None and len(viable) >= 2:
                viable.sort(key=lambda h: -h.s)
                pool = viable[:PAIR_POOL]
                combos = []
                for i in range(len(pool)):
                    for j in range(i + 1, len(pool)):
                        a, b = pool[i].cs[0].edit, pool[j].cs[0].edit
                        if a.start < b.end and b.start < a.end:
                            continue
                        first, second = (a, b) if a.start > b.start else (b, a)
                        ns = second.apply(first.apply(src))
                        if ns in tried:
                            continue
                        tried.add(ns)
                        combos.append((i, j, ns))
                for q in range(0, len(combos), self.workers):
                    if self.out_of_time():
                        break
                    chunk = combos[q:q + self.workers]
                    for (i, j, ns), e in zip(chunk, self.evaluate_many([x[2] for x in chunk], frozenset(cur.passed))):
                        pairs += 1
                        self.evaluated += 1
                        if e is not None and not e.aborted and e.score > cur.score + 1e-9 and (
                                best is None or e.score > best.e.score + 1e-9):
                            best = Hit([pool[i].cs[0], pool[j].cs[0]], ns, e, max(pool[i].s, pool[j].s), -1)
            log.update({'round': rnd, 'ranked_candidates': len(ranked), 'evaluated': stats[0], 'did_not_compile': stats[1]})
            if bt_log:
                log['backtrack'] = bt_log
            if pairs:
                log['pairs_evaluated'] = pairs
            if best is None:
                log['result'] = 'no improving edit'
                rounds.append(log)
                break
            if len(best.cs) == 2 and best.cs[0].op == 'undo earlier edit':
                seq = [best.cs[0], best.cs[1]]
            elif len(best.cs) == 2:
                seq = sorted(best.cs, key=lambda c: -c.edit.start)
            else:
                seq = [best.cs[0]]
            step = src
            edits = []
            for c in seq:
                e = c.edit
                nxt = e.apply(step)
                edits.append({'operator': c.op, 'line': line_of(step, e.start), 'before': line_text(step, e.start),
                              'after': line_text(nxt, e.start)})
                shift_all(holes, e)
                shift_all(undo, e)
                if c.op != 'undo earlier edit':
                    undo.append(Hole(e.start, e.start + len(e.text), [step[e.start:e.end]], 'search edit'))
                step = nxt
            if step != best.src:
                raise RuntimeError('edit replay mismatch')
            log.update({'edits': edits, 'suspiciousness': round(best.s, 3), 'rank': best.rank,
                        'passing': len(best.e.passed), 'score': round(best.e.score, 3)})
            rounds.append(log)
            src, cur = best.src, best.e
        self.report['candidates_evaluated'] = self.evaluated
        self.report['ambiguous_rounds'] = self.ambiguous_rounds
        return src

    # ------------------------------------------------------------------ finish

    def finalize(self, original, src, out_binary, sig_before):
        self.write('candidate.c', src)
        reason = gate.check(src)
        if reason is not None:
            return self.finish(1, 'repaired source left the supported subset: ' + reason, src, None)
        unit = self.front.analyze(src)
        if sig_before:
            if cfront.signatures(unit) != sig_before:
                return self.finish(1, 'function signatures changed during repair', src, None)
        self.report['final_warnings'] = [d.brief() for d in unit.warnings][:50]
        if unit.errors:
            return self.finish(1, 'final source does not compile: ' + unit.errors[0].brief(), src, None)
        binary = os.path.join(self.work, 'program')
        self.compiles += 1
        ok, diag = runner.compile_c(src, binary)
        try:
            os.unlink(binary + '.c')
        except OSError:
            pass
        if not ok:
            return self.finish(1, f'final source does not compile with {runner.compiler()}: ' + diag.strip()[:500], src, None)
        if self.tests:
            self.metered = False
            fails = []
            passed = 0
            for i, t in enumerate(self.tests):
                o = runner.run(binary, t, i, 10.0, None, False)
                self.test_runs += 1
                if o.passed:
                    passed += 1
                else:
                    fails.append({'test': i, 'crash': o.crash, 'output_similarity': round(o.similarity, 3)})
            self.report['final_passing'] = passed
            self.report['final_failures'] = fails
            if passed != len(self.tests):
                os.unlink(binary)
                return self.finish(1, f'repair failed validation: {passed}/{len(self.tests)} tests pass', src, None)
        self.write('corrected.c', src)
        if out_binary:
            os.makedirs(os.path.dirname(os.path.abspath(out_binary)), exist_ok=True)
            shutil.copy2(binary, out_binary)
        self.report['changed'] = src != original
        status = 'compiles (no tests supplied: behaviour not validated)' if not self.tests else 'all tests pass'
        return self.finish(0, status, src, src)

    def finish(self, code, status, candidate, corrected):
        self.report['status'] = status
        self.report['exit_code'] = code
        self.report['clang_parses'] = self.front.parses
        self.report['compiles'] = self.compiles
        self.report['test_executions'] = self.test_runs
        self.report['seconds'] = round(time.time() - self.t0, 1)
        if candidate is not None:
            self.write('candidate.c', candidate)
        self.write('report.json', json.dumps(self.report, indent=1))
        return code
