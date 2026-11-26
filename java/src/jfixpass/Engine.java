package jfixpass;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.stream.Stream;

/**
 * Pipeline: gate -> unchanged check -> compiler-guided repair -> static rules -> coverage-ranked, test-driven
 * edit search -> final recompilation and full test validation. Success artifacts are written only when every
 * supplied test passes.
 */
final class Engine {
    static final int ROUND_BUDGET = Integer.getInteger("jfixpass.roundBudget", 2500);
    static final int WINDOW = Integer.getInteger("jfixpass.window", 80);
    static final int MAX_ROUNDS = Integer.getInteger("jfixpass.maxRounds", 30);
    static final int PAIR_POOL = Integer.getInteger("jfixpass.pairPool", 24);
    static final int BACKTRACK_BUDGET = Integer.getInteger("jfixpass.backtrackBudget", 400);
    static final long TIME_BUDGET_MS = Long.getLong("jfixpass.timeBudgetSec", 1800) * 1000;

    final Path work;
    final List<Runner.Test> tests;
    final Map<String, Object> report = new LinkedHashMap<>();
    private final long t0 = System.currentTimeMillis();
    private Runner runner;
    private int counter;
    private int evaluated;
    private final Map<String, Eval> cache = new HashMap<>();
    private long fuelLimit = 50_000_000L;
    private boolean metered = true;

    Engine(Path work, List<Runner.Test> tests) {
        this.work = work;
        this.tests = tests;
    }

    static final class Eval {
        Runner.Outcome[] res;
        double score;
        final Set<Integer> pass = new TreeSet<>();
        final Set<Integer> timeouts = new TreeSet<>();
        boolean aborted;

        boolean allPass(int n) {
            return pass.size() == n;
        }
    }

    private Set<Integer> all() {
        Set<Integer> s = new TreeSet<>();
        for (int i = 0; i < tests.size(); i++) {
            s.add(i);
        }
        return s;
    }

    private Eval evaluate(String src, Set<Integer> mustPass, Set<Integer> mayTimeout, boolean cov) {
        return evaluate(src, mustPass, mayTimeout, cov, null);
    }

    /**
     * Compile and run. With a baseline, tests that timed out in the baseline run last; if one times out again the
     * rest of them are assumed unchanged and their baseline outcomes are carried forward (saves a timeout each).
     */
    private Eval evaluate(String src, Set<Integer> mustPass, Set<Integer> mayTimeout, boolean cov, Eval baseline) {
        if (!cov && mustPass.isEmpty() && cache.containsKey(src)) {
            return cache.get(src);
        }
        Path dir = work.resolve("tmp").resolve("c" + (counter++));
        try {
            String exe = metered ? Fuel.instrument(src, fuelLimit) : src;
            if (exe == null) {
                return null;
            }
            List<Javac.Diag> ds = Javac.compileTo(exe, dir);
            if (Javac.hasErrors(ds)) {
                return null;
            }
            List<Integer> order = new ArrayList<>(mustPass);
            for (int i = 0; i < tests.size(); i++) {
                if (!mustPass.contains(i) && (baseline == null || !baseline.timeouts.contains(i))) {
                    order.add(i);
                }
            }
            if (baseline != null) {
                order.addAll(baseline.timeouts);
            }
            boolean[] ab = new boolean[1];
            Eval e = new Eval();
            e.res = runner.run(dir, Javac.className, cov, order, mustPass, mayTimeout, ab, baseline != null);
            e.aborted = ab[0];
            if (baseline != null && !e.aborted) {
                for (int i = 0; i < e.res.length; i++) {
                    if (e.res[i] == null && baseline.timeouts.contains(i)) {
                        e.res[i] = baseline.res[i];
                    }
                }
            }
            for (Runner.Outcome o : e.res) {
                if (o == null) {
                    continue;
                }
                e.score += o.score();
                if (o.pass()) {
                    e.pass.add(o.index());
                }
                if (o.timeout()) {
                    e.timeouts.add(o.index());
                }
            }
            if (!cov && !e.aborted) {
                cache.put(src, e);
            }
            return e;
        } finally {
            deleteTree(dir);
        }
    }

    static void deleteTree(Path p) {
        if (!Files.exists(p)) {
            return;
        }
        try (Stream<Path> w = Files.walk(p)) {
            w.sorted(Comparator.reverseOrder()).forEach(x -> {
                try {
                    Files.delete(x);
                } catch (IOException ignored) {
                    // best effort
                }
            });
        } catch (IOException ignored) {
            // best effort
        }
    }

    private static String line(String src, int pos) {
        int a = Text.lineStart(src, pos);
        int b = src.indexOf('\n', pos);
        return src.substring(a, b < 0 ? src.length() : b).strip();
    }

    private void write(String name, String content) {
        try {
            Files.writeString(work.resolve(name), content);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Returns exit code: 0 success, 1 repair failed validation, 2 invalid input. */
    int run(String original, Path outClasses) throws IOException {
        Files.createDirectories(work.resolve("tmp"));
        write("original.java", original);
        report.put("tool", "jfixpass 0.1.0");
        report.put("tests", (double) tests.size());
        String gate = Gate.check(original);
        if (gate != null) {
            return finish(2, "rejected: " + gate, original, null);
        }
        Javac.className = mainClassName(original);
        report.put("main_class", Javac.className);
        runner = new Runner(tests, work, 2000);
        try {
            return pipeline(original, outClasses);
        } finally {
            runner.close();
            deleteTree(work.resolve("tmp"));
        }
    }

    static String mainClassName(String src) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile("public\\s+(?:final\\s+|abstract\\s+)*class\\s+(\\w+)").matcher(src);
        return m.find() ? m.group(1) : "Main";
    }

    private int pipeline(String original, Path outClasses) throws IOException {
        // Stage 0: unchanged if already valid.
        Javac.Unit au0 = Javac.analyze(original);
        report.put("original_compiles", au0.errors().isEmpty());
        report.put("original_diagnostics", au0.errors().stream().map(Javac.Diag::brief).toList());
        if (au0.errors().isEmpty() && StaticRules.stringEquality(au0).isEmpty()) {
            Eval e = tests.isEmpty() ? null : evaluate(original, Set.of(), all(), false);
            if (tests.isEmpty() || e.allPass(tests.size())) {
                report.put("original_passes", true);
                return finalize(original, original, outClasses, List.of());
            }
        }
        // Stage 1: compiler-guided repair.
        CompileRepair cr = new CompileRepair();
        String src = cr.run(original);
        report.put("compile_repairs", cr.log);
        report.put("compile_candidates_checked", (double) cr.candidatesTried);
        report.put("syntax_parses", (double) cr.syntaxParses);
        if (cr.lossy) {
            report.put("lossy_syntax_repair", true);
        }
        if (cr.failure != null) {
            return finish(1, cr.failure, src, null);
        }
        List<CompileRepair.Hole> holes = cr.holes;
        // Stage 2: static rules (verified against tests when tests exist).
        List<Map<String, Object>> staticLog = new ArrayList<>();
        Javac.Unit au = Javac.analyze(src);
        List<StaticRules.Finding> fs = StaticRules.stringEquality(au);
        fs.sort((a, b) -> Integer.compare(b.edit().start(), a.edit().start()));
        for (StaticRules.Finding f : fs) {
            String ns = f.edit().apply(src);
            boolean keep = true;
            if (!tests.isEmpty()) {
                Eval before = evaluate(src, Set.of(), all(), false);
                Eval after = evaluate(ns, Set.of(), all(), false);
                keep = after != null && after.pass.containsAll(before.pass) && after.score >= before.score;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("line", (double) f.line());
            m.put("rule", f.rule());
            m.put("before", line(src, f.edit().start()));
            m.put("after", line(ns, f.edit().start()));
            m.put("applied", keep);
            staticLog.add(m);
            if (keep) {
                CompileRepair.shiftAll(holes, f.edit());
                src = ns;
            }
        }
        report.put("static_rules", staticLog);
        // Stage 3: test-driven search.
        if (!tests.isEmpty()) {
            src = search(src, holes);
        }
        List<String> sigBefore = cr.signatures;
        return finalize(original, src, outClasses, sigBefore);
    }

    record Ranked(Mutations.Cand c, double s, int stmt) {
    }

    record Hit(List<Mutations.Cand> cs, String src, Eval e, double s, int rank) {
    }

    /** Coverage-ranked candidate edits for {@code base}, whose test results are {@code baseEval}. */
    private List<Ranked> rank(String base, Eval baseEval, List<CompileRepair.Hole> holes) {
        Coverage cov = Coverage.instrument(Javac.parse(base));
        Eval ce = evaluate(cov.instrumented, Set.of(), all(), true);
        List<int[]> hits = new ArrayList<>();
        List<Boolean> passed = new ArrayList<>();
        for (int i = 0; i < tests.size(); i++) {
            Runner.Outcome o = ce == null ? null : ce.res[i];
            hits.add(o == null ? new int[0] : o.hits());
            passed.add(baseEval.pass.contains(i));
        }
        double[] susp = cov.ochiai(hits, passed);
        crashBoost(base, cov, ce, baseEval, susp);
        List<Mutations.Cand> cands = new ArrayList<>(Mutations.generate(Javac.analyze(base)));
        for (CompileRepair.Hole h : holes) {
            String now = base.substring(h.start, h.end);
            for (String alt : h.alternatives) {
                if (!alt.equals(now)) {
                    cands.add(new Mutations.Cand(new Text.Edit(h.start, h.end, alt), 1, "revisit compile choice (" + h.reason + ")", h.start));
                }
            }
        }
        List<Ranked> ranked = new ArrayList<>();
        for (Mutations.Cand c : cands) {
            int sid = cov.statementAt(c.anchor());
            double sc = sid < 0 ? 0 : susp[sid];
            if (sc > 0) {
                ranked.add(new Ranked(c, sc, cov.stmts.get(sid).start()));
            }
        }
        // most suspicious statement first; all edits of one statement together, cheapest first
        ranked.sort((x, y) -> x.s() != y.s() ? Double.compare(y.s(), x.s())
                : x.stmt() != y.stmt() ? Integer.compare(x.stmt(), y.stmt())
                : x.c().cost() != y.c().cost() ? Integer.compare(x.c().cost(), y.c().cost())
                : Integer.compare(x.c().anchor(), y.c().anchor()));
        return ranked;
    }

    /**
     * Crash-site localization: failing tests that end in an exception (including fuel exhaustion) point at program
     * lines. Statements starting on the top program frame's line get +2, other statements of that method +1,
     * statements on lower frames' lines +0.5, each weighted by the fraction of failing tests with that trace.
     */
    private void crashBoost(String base, Coverage cov, Eval ce, Eval baseEval, double[] susp) {
        if (ce == null) {
            return;
        }
        int failing = tests.size() - baseEval.pass.size();
        if (failing == 0) {
            return;
        }
        Javac.Unit pu = Javac.parse(base);
        List<int[]> methods = new ArrayList<>();
        new com.sun.source.util.TreeScanner<Void, Void>() {
            @Override
            public Void visitMethod(com.sun.source.tree.MethodTree m, Void p) {
                methods.add(new int[] {Text.lineOf(base, pu.start(m)), Text.lineOf(base, pu.end(m))});
                return super.visitMethod(m, p);
            }
        }.scan(pu.tree, null);
        int[] stmtLine = new int[cov.stmts.size()];
        for (Coverage.Stmt st : cov.stmts) {
            stmtLine[st.id()] = Text.lineOf(base, st.start());
        }
        double[] bonus = new double[susp.length];
        for (Runner.Outcome o : ce.res) {
            if (o == null || baseEval.pass.contains(o.index()) || o.trace() == null || o.trace().length == 0) {
                continue;
            }
            int top = o.trace()[0];
            int[] method = null;
            for (int[] m : methods) {
                if (m[0] <= top && top <= m[1] && (method == null || m[1] - m[0] < method[1] - method[0])) {
                    method = m;
                }
            }
            for (int i = 0; i < susp.length; i++) {
                double b = 0;
                if (stmtLine[i] == top) {
                    b = 2.0;
                } else if (method != null && method[0] <= stmtLine[i] && stmtLine[i] <= method[1]) {
                    b = 1.0;
                } else {
                    for (int k = 1; k < o.trace().length; k++) {
                        if (o.trace()[k] == stmtLine[i]) {
                            b = 0.5;
                        }
                    }
                }
                bonus[i] = Math.max(bonus[i], 0) + b / failing;
            }
        }
        for (int i = 0; i < susp.length; i++) {
            if (susp[i] > 0) {
                susp[i] += bonus[i];
            }
        }
    }

    /**
     * Evaluate ranked single edits on {@code base} (optionally preceded by {@code pre}, an undo edit) and return the
     * best edit that beats {@code cur}. Non-improving, non-regressing edits are collected into {@code viable}.
     */
    private Hit scan(String base, List<Ranked> ranked, Eval cur, Mutations.Cand pre, Set<String> tried, List<Hit> viable,
                     int budget, int[] stats) {
        return scan(base, ranked, cur, pre, tried, viable, budget, stats, true);
    }

    /**
     * {@code strict}: previously passing tests must keep passing (fail-fast). Non-strict (backtracking): passing tests
     * may change, but the total score must rise and the number of passing tests must not fall.
     */
    private Hit scan(String base, List<Ranked> ranked, Eval cur, Mutations.Cand pre, Set<String> tried, List<Hit> viable,
                     int budget, int[] stats, boolean strict) {
        Hit best = null;
        int firstAt = -1;
        int n = 0;
        lastTies = new ArrayList<>();
        for (int k = 0; k < ranked.size() && n < budget; k++) {
            if (firstAt >= 0 && n - firstAt > WINDOW) {
                break;
            }
            if (System.currentTimeMillis() - t0 > TIME_BUDGET_MS) {
                break;
            }
            Ranked r = ranked.get(k);
            String ns = r.c().edit().apply(base);
            if (!tried.add(ns)) {
                continue;
            }
            n++;
            evaluated++;
            stats[0]++;
            Eval e = strict ? evaluate(ns, cur.pass, cur.timeouts, false) : evaluate(ns, Set.of(), all(), false);
            if (e == null) {
                stats[1]++;
                continue;
            }
            if (e.aborted || (!strict && e.pass.size() < cur.pass.size())) {
                continue;
            }
            List<Mutations.Cand> cs = pre == null ? List.of(r.c()) : List.of(pre, r.c());
            Hit h = new Hit(cs, ns, e, r.s(), k + 1);
            if (e.score > cur.score + 1e-9) {
                if (firstAt < 0) {
                    firstAt = n;
                }
                lastTies.add(h);
                if (best == null || e.score > best.e().score + 1e-9
                        || (Math.abs(e.score - best.e().score) <= 1e-9 && r.c().cost() < best.cs().get(best.cs().size() - 1).cost())) {
                    best = h;
                }
            } else if (viable != null && e.score >= cur.score - 1e-9) {
                viable.add(h);
            }
        }
        if (best != null) {
            Hit b = best;
            lastTies.removeIf(x -> Math.abs(x.e().score - b.e().score) > 1e-9);
        }
        return best;
    }

    private List<Hit> lastTies = new ArrayList<>();
    private List<Runner.Test> amplified;

    /** Deterministic input amplification: drop, duplicate or swap lines of the visible test inputs. */
    private List<Runner.Test> amplifiedInputs() {
        if (amplified != null) {
            return amplified;
        }
        amplified = new ArrayList<>();
        java.util.Random rnd = new java.util.Random(7);
        Set<String> seen = new HashSet<>();
        for (Runner.Test t : tests) {
            seen.add(t.input());
        }
        java.util.regex.Pattern num = java.util.regex.Pattern.compile("(?<![\\w.])\\d{1,6}(?![\\w.])");
        for (int round = 0; round < 8 && amplified.size() < 60; round++) {
            for (Runner.Test t : tests) {
                String[] ls = t.input().split("\n", -1);
                if (ls.length < 3) {
                    continue;
                }
                List<String> v = new ArrayList<>(List.of(ls));
                int k = 1 + rnd.nextInt(Math.max(1, v.size() - 2));
                switch (rnd.nextInt(5)) {
                    case 3, 4 -> {
                        // perturb integers on up to three lines (keeps structure, changes values)
                        for (int rep = 0; rep < 3; rep++) {
                            int li = rnd.nextInt(v.size());
                            java.util.regex.Matcher m = num.matcher(v.get(li));
                            List<int[]> spans = new ArrayList<>();
                            while (m.find()) {
                                spans.add(new int[] {m.start(), m.end()});
                            }
                            if (spans.isEmpty()) {
                                continue;
                            }
                            int[] sp = spans.get(rnd.nextInt(spans.size()));
                            long val = Long.parseLong(v.get(li).substring(sp[0], sp[1]));
                            long nv = switch (rnd.nextInt(3)) {
                                case 0 -> val + 1 + rnd.nextInt(9);
                                case 1 -> val * 3 + 1;
                                default -> Math.max(0, val - 1 - rnd.nextInt(5));
                            };
                            v.set(li, v.get(li).substring(0, sp[0]) + nv + v.get(li).substring(sp[1]));
                        }
                    }
                    case 0 -> v.remove(k);
                    case 1 -> v.add(k, v.get(k));
                    default -> {
                        if (k + 1 < v.size()) {
                            String x = v.get(k);
                            v.set(k, v.get(k + 1));
                            v.set(k + 1, x);
                        }
                    }
                }
                String in = String.join("\n", v);
                if (seen.add(in)) {
                    amplified.add(new Runner.Test(in, ""));
                }
            }
        }
        return amplified;
    }

    /**
     * Several edits reach the same best score: run them on amplified inputs, group them by identical behaviour, and
     * prefer the largest group (then lowest cost, then rank). Groups that disagree mean the tests do not determine
     * the repair; this is reported as ambiguous.
     */
    private Hit disambiguate(Hit best, Map<String, Object> log) {
        List<Hit> ties = new ArrayList<>(lastTies);
        if (ties.size() < 2) {
            return best;
        }
        if (ties.size() > 8) {
            ties = new ArrayList<>(ties.subList(0, 8));
            if (!ties.contains(best)) {
                ties.add(best);
            }
        }
        List<Runner.Test> amp = amplifiedInputs();
        if (amp.isEmpty()) {
            return best;
        }
        Map<String, List<Hit>> groups = new LinkedHashMap<>();
        try (Runner ar = new Runner(amp, work.resolve("tmp"), runnerTimeout)) {
            for (Hit h : ties) {
                Path dir = work.resolve("tmp").resolve("a" + (counter++));
                String exe = Fuel.instrument(h.src(), fuelLimit);
                if (exe == null || Javac.hasErrors(Javac.compileTo(exe, dir))) {
                    continue;
                }
                List<Integer> order = new ArrayList<>();
                for (int i = 0; i < amp.size(); i++) {
                    order.add(i);
                }
                Runner.Outcome[] res = ar.run(dir, Javac.className, false, order, Set.of(), new HashSet<>(order), new boolean[1]);
                deleteTree(dir);
                StringBuilder sig = new StringBuilder();
                for (Runner.Outcome o : res) {
                    sig.append(o == null ? "?" : (o.exc() != null || o.timeout() ? "!" : "") + o.out()).append('\u0000');
                }
                groups.computeIfAbsent(sig.toString(), k -> new ArrayList<>()).add(h);
            }
        }
        if (groups.isEmpty()) {
            return best;
        }
        List<List<Hit>> gs = new ArrayList<>(groups.values());
        gs.sort((a, b) -> Integer.compare(b.size(), a.size()));
        List<Hit> top = gs.get(0);
        boolean ambiguous = gs.size() > 1;
        Hit chosen = top.get(0);
        for (Hit h : top) {
            int hc = h.cs().get(h.cs().size() - 1).cost();
            int cc = chosen.cs().get(chosen.cs().size() - 1).cost();
            if (hc < cc || (hc == cc && h.rank() < chosen.rank())) {
                chosen = h;
            }
        }
        log.put("equal_score_alternatives", (double) ties.size());
        List<String> all = new ArrayList<>();
        for (Hit h : ties) {
            Mutations.Cand c = h.cs().get(h.cs().size() - 1);
            all.add(c.op() + " @L" + Text.lineOf(h.src(), c.edit().start()) + ": " + line(h.src(), c.edit().start()));
        }
        log.put("alternatives", all);
        log.put("behaviour_groups", gs.stream().map(g -> (double) g.size()).toList());
        if (gs.size() > 1) {
            List<String> alts = new ArrayList<>();
            for (List<Hit> g : gs) {
                Mutations.Cand c = g.get(0).cs().get(g.get(0).cs().size() - 1);
                alts.add(c.op() + " @L" + Text.lineOf(g.get(0).src(), c.edit().start()) + ": " + line(g.get(0).src(), c.edit().start()));
            }
            log.put("alternative_behaviours", alts);
        }
        if (ambiguous) {
            log.put("ambiguous", true);
            log.put("resolved_by", gs.get(1).size() < top.size() ? "largest behaviour group" : "lowest cost (tie)");
            ambiguousRounds++;
        }
        return chosen;
    }

    private long runnerTimeout = 2000;
    private int ambiguousRounds;

    private String search(String src, List<CompileRepair.Hole> holes) {
        List<Map<String, Object>> rounds = new ArrayList<>();
        report.put("search", rounds);
        Eval cur = evaluate(src, Set.of(), all(), false);
        long maxMs = 0;
        for (Runner.Outcome o : cur.res) {
            if (o != null && !o.timeout() && o.index() != 0) {
                maxMs = Math.max(maxMs, o.ms());
            }
        }
        long timeout = Math.max(500, Math.min(2000, 20 * maxMs));
        long maxFuel = 0;
        for (Runner.Outcome o : cur.res) {
            if (o != null && !o.timeout() && (o.exc() == null || !o.exc().contains("fuel exhausted"))) {
                maxFuel = Math.max(maxFuel, o.fuel());
            }
        }
        fuelLimit = Math.max(1_000_000L, Math.min(50_000_000L, 50 * maxFuel));
        report.put("fuel_limit", (double) fuelLimit);
        runner.close();
        runner = new Runner(tests, work, timeout);
        runnerTimeout = timeout;
        report.put("test_timeout_ms", (double) timeout);
        cache.clear();
        cur = evaluate(src, Set.of(), all(), false);
        Map<String, Object> start = new LinkedHashMap<>();
        start.put("initial_passing", (double) cur.pass.size());
        start.put("initial_score", cur.score);
        rounds.add(start);
        Set<String> tried = new HashSet<>();
        List<CompileRepair.Hole> undo = new ArrayList<>();
        for (int round = 1; round <= MAX_ROUNDS && !cur.allPass(tests.size()); round++) {
            if (System.currentTimeMillis() - t0 > TIME_BUDGET_MS) {
                report.put("search_stopped", "time budget");
                break;
            }
            List<Ranked> ranked = rank(src, cur, holes);
            List<Hit> viable = new ArrayList<>();
            int[] stats = new int[2];
            Hit best = scan(src, ranked, cur, null, tried, viable, ROUND_BUDGET, stats);
            int evalSingles = stats[0];
            Map<String, Object> log = new LinkedHashMap<>();
            if (best != null) {
                best = disambiguate(best, log);
            }
            // backtracking: undo one earlier search edit, re-rank on the undone program, try one new edit on top
            int[] bstats = new int[2];
            List<Map<String, Object>> btLog = new ArrayList<>();
            if (best == null && !undo.isEmpty()) {
                for (int ui = undo.size() - 1; ui >= 0 && best == null; ui--) {
                    CompileRepair.Hole h = undo.get(ui);
                    Text.Edit ue = new Text.Edit(h.start, h.end, h.alternatives.get(0));
                    Mutations.Cand uc = new Mutations.Cand(ue, 1, "undo earlier edit", h.start);
                    String base = ue.apply(src);
                    Eval be = evaluate(base, Set.of(), all(), false);
                    Map<String, Object> bl = new LinkedHashMap<>();
                    bl.put("undo_line", line(base, ue.start()));
                    bl.put("base_score", be == null ? null : Math.round(be.score * 1000) / 1000.0);
                    btLog.add(bl);
                    if (be == null) {
                        continue;
                    }
                    int before = bstats[0];
                    List<CompileRepair.Hole> shifted = new ArrayList<>();
                    for (CompileRepair.Hole x : holes) {
                        CompileRepair.Hole y = new CompileRepair.Hole(x.start, x.end, x.alternatives, x.reason);
                        if (y.shift(ue)) {
                            shifted.add(y);
                        }
                    }
                    List<Ranked> br = rank(base, be, shifted);
                    Hit hb = scan(base, br, cur, uc, tried, null, BACKTRACK_BUDGET, bstats, false);
                    bl.put("ranked", (double) br.size());
                    bl.put("evaluated", (double) (bstats[0] - before));
                    if (hb != null) {
                        best = hb;
                        undo.remove(ui);
                    }
                }
            }
            int pairsTried = 0;
            if (best == null && viable.size() >= 2) {
                viable.sort((x, y) -> Double.compare(y.s(), x.s()));
                List<Hit> pool = viable.subList(0, Math.min(PAIR_POOL, viable.size()));
                outer:
                for (int i = 0; i < pool.size(); i++) {
                    for (int j = i + 1; j < pool.size(); j++) {
                        Text.Edit a = pool.get(i).cs().get(0).edit();
                        Text.Edit b = pool.get(j).cs().get(0).edit();
                        if (a.start() < b.end() && b.start() < a.end()) {
                            continue;
                        }
                        String ns = Text.apply(src, List.of(a, b));
                        if (!tried.add(ns)) {
                            continue;
                        }
                        pairsTried++;
                        evaluated++;
                        Eval e = evaluate(ns, cur.pass, cur.timeouts, false);
                        if (e != null && !e.aborted && e.score > cur.score + 1e-9 && (best == null || e.score > best.e().score + 1e-9)) {
                            best = new Hit(List.of(pool.get(i).cs().get(0), pool.get(j).cs().get(0)), ns, e,
                                    Math.max(pool.get(i).s(), pool.get(j).s()), -1);
                        }
                        if (System.currentTimeMillis() - t0 > TIME_BUDGET_MS) {
                            break outer;
                        }
                    }
                }
            }
            log.put("round", (double) round);
            log.put("ranked_candidates", (double) ranked.size());
            log.put("evaluated", (double) evalSingles);
            log.put("did_not_compile", (double) stats[1]);
            if (!btLog.isEmpty()) {
                log.put("backtrack", btLog);
            }
            if (pairsTried > 0) {
                log.put("pairs_evaluated", (double) pairsTried);
            }
            if (best == null) {
                log.put("result", "no improving edit");
                rounds.add(log);
                break;
            }
            // edits in the chosen hit apply to src in sequence: an undo (optional) then one edit on the undone source
            List<Map<String, Object>> edits = new ArrayList<>();
            String step = src;
            List<Text.Edit> appliedSeq = new ArrayList<>();
            if (best.cs().size() == 2 && best.cs().get(0).op().equals("undo earlier edit")) {
                appliedSeq.add(best.cs().get(0).edit());
                appliedSeq.add(best.cs().get(1).edit());
            } else if (best.cs().size() == 2) {
                // independent pair on src: apply the later one first so positions stay valid
                Text.Edit a = best.cs().get(0).edit();
                Text.Edit b = best.cs().get(1).edit();
                Text.Edit first = a.start() > b.start() ? a : b;
                Text.Edit second = first == a ? b : a;
                appliedSeq.add(first);
                appliedSeq.add(second);
            } else {
                appliedSeq.add(best.cs().get(0).edit());
            }
            for (int i = 0; i < appliedSeq.size(); i++) {
                Text.Edit e = appliedSeq.get(i);
                Mutations.Cand c = null;
                for (Mutations.Cand x : best.cs()) {
                    if (x.edit() == e) {
                        c = x;
                    }
                }
                Map<String, Object> em = new LinkedHashMap<>();
                em.put("operator", c.op());
                em.put("line", (double) Text.lineOf(step, e.start()));
                em.put("before", line(step, e.start()));
                String next = e.apply(step);
                em.put("after", line(next, e.start()));
                edits.add(em);
                CompileRepair.shiftAll(holes, e);
                CompileRepair.shiftAll(undo, e);
                if (!c.op().equals("undo earlier edit")) {
                    undo.add(new CompileRepair.Hole(e.start(), e.start() + e.text().length(),
                            List.of(step.substring(e.start(), e.end())), "search edit"));
                }
                step = next;
            }
            if (!step.equals(best.src())) {
                throw new IllegalStateException("edit replay mismatch");
            }
            log.put("edits", edits);
            log.put("suspiciousness", Math.round(best.s() * 1000) / 1000.0);
            log.put("rank", (double) best.rank());
            log.put("passing", (double) best.e().pass.size());
            log.put("score", Math.round(best.e().score * 1000) / 1000.0);
            rounds.add(log);
            src = best.src();
            cur = best.e();
        }
        report.put("candidates_evaluated", (double) evaluated);
        report.put("ambiguous_rounds", (double) ambiguousRounds);
        return src;
    }

    private int finalize(String original, String src, Path outClasses, List<String> sigBefore) throws IOException {
        write("candidate.java", src);
        if (Gate.check(src) != null) {
            return finish(1, "repaired source left the supported subset: " + Gate.check(src), src, null);
        }
        if (sigBefore != null && !sigBefore.isEmpty()) {
            List<String> after = CompileRepair.signatures(Javac.parse(src));
            if (!after.equals(sigBefore)) {
                return finish(1, "method signatures changed during repair", src, null);
            }
        }
        Path classes = work.resolve("classes");
        deleteTree(classes);
        List<Javac.Diag> ds = Javac.compileTo(src, classes);
        report.put("final_warnings", ds.stream().filter(d -> !d.error()).map(Javac.Diag::brief).toList());
        if (Javac.hasErrors(ds)) {
            deleteTree(classes);
            return finish(1, "final source does not compile: " + ds.get(0).brief(), src, null);
        }
        if (!tests.isEmpty()) {
            try (Runner fresh = new Runner(tests, work, 10000)) {
                runner = fresh;
                metered = false;
                Eval e = evaluate(src, Set.of(), all(), false);
                cache.clear();
                List<Map<String, Object>> fails = new ArrayList<>();
                for (Runner.Outcome o : e.res) {
                    if (!o.pass()) {
                        Map<String, Object> f = new LinkedHashMap<>();
                        f.put("test", (double) o.index());
                        f.put("timeout", o.timeout());
                        f.put("exception", o.exc());
                        f.put("output_similarity", Math.round(o.similarity() * 1000) / 1000.0);
                        fails.add(f);
                    }
                }
                report.put("final_passing", (double) e.pass.size());
                report.put("final_failures", fails);
                if (!e.allPass(tests.size())) {
                    deleteTree(classes);
                    return finish(1, "repair failed validation: " + e.pass.size() + "/" + tests.size() + " tests pass", src, null);
                }
            }
        }
        write("corrected.java", src);
        if (outClasses != null) {
            Files.createDirectories(outClasses);
            try (Stream<Path> w = Files.walk(classes)) {
                for (Path p : (Iterable<Path>) w::iterator) {
                    if (Files.isRegularFile(p)) {
                        Path dst = outClasses.resolve(classes.relativize(p).toString());
                        Files.createDirectories(dst.getParent());
                        Files.copy(p, dst, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                    }
                }
            }
        }
        report.put("changed", !src.equals(original));
        return finish(0, tests.isEmpty() ? "compiles (no tests supplied: behaviour not validated)" : "all tests pass", src, src);
    }

    private int finish(int code, String status, String candidate, String corrected) {
        report.put("status", status);
        report.put("exit_code", (double) code);
        report.put("javac_invocations", (double) Javac.calls);
        report.put("seconds", Math.round((System.currentTimeMillis() - t0) / 100.0) / 10.0);
        if (runner != null) {
            report.put("test_executions", (double) runner.testRuns);
        }
        if (candidate != null) {
            write("candidate.java", candidate);
        }
        write("report.json", Json.write(report));
        return code;
    }
}
