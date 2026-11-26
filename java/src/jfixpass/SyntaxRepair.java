package jfixpass;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * General syntax repair, in the style of Burke-Fisher / least-cost parser error recovery, with javac as the oracle.
 *
 * <p>For the first parse error, candidate token edits (insert any token from the vocabulary, delete, replace,
 * transpose, plus lexical fixes for literals, comments, escapes and confusable characters) are generated in a window
 * before the error and tried in order of increasing cost. A candidate is accepted only if the file then parses, or the
 * first error moves at least K tokens closer to the end of file. Escalation: priority vocabulary, full vocabulary
 * with a wider window, depth-2 beam, and finally a reported lossy excision of the lines around the error.
 * Every accepted step strictly reduces the number of tokens after the first error, so the loop terminates. Equally
 * cheap alternatives at the same site become holes for type checking and the test-driven search to settle.
 */
final class SyntaxRepair {
    record Cand(Text.Edit edit, double cost, String rule, int site, String group, String token) {
    }

    /** clean: parses; after: tokens after the first parse error; errors: parse errors; types: attribution errors if clean. */
    private record Score(boolean clean, int after, int errors, int types) {
    }

    private static final double TYPE_PENALTY = 0.7;

    /** Edit cost plus an estimate of the repair cost still ahead: remaining parse errors, or type errors once clean. */
    private static double effective(Cand c, Score s) {
        return c.cost() + (s.clean() ? TYPE_PENALTY * Math.min(s.types(), 5) : 1.0 * Math.min(s.errors(), 8));
    }

    private record Found(Cand c, String src, Score s, List<Cand> ties, List<Cand> chain) {
    }

    final List<Map<String, Object>> log = new ArrayList<>();
    final List<CompileRepair.Hole> holes = new ArrayList<>();
    String failure;
    boolean lossy;
    int parses;
    private final Map<String, Score> memo = new LinkedHashMap<>(4096, 0.75f, true) {
        @Override
        protected boolean removeEldestEntry(Map.Entry<String, Score> e) {
            return size() > 20000;
        }
    };

    private static final List<String> PRIORITY = List.of(";", ")", "(", "}", "{", ",", "]", "[", ".", "=");
    private static final Set<String> NO_SPACE_BEFORE = Set.of(";", ",", ")", "]", ".", "++", "--", "::", "[", "(");
    private static final List<String> KEYWORDS = List.of("new", "return", "else", "if", "for", "while", "int", "long", "double",
            "boolean", "char", "void", "static", "final", "public", "private", "class", "this", "null", "true", "false", "break",
            "continue", "case", "default", "do", "try", "catch", "throw", "throws", "instanceof", "String", "extends");
    private static final Map<Character, String> CONFUSABLE = Map.ofEntries(
            Map.entry('“', "\""), Map.entry('”', "\""), Map.entry('„', "\""), Map.entry('″', "\""),
            Map.entry('‘', "'"), Map.entry('’', "'"), Map.entry('′', "'"), Map.entry('−', "-"),
            Map.entry('–', "-"), Map.entry('—', "-"), Map.entry('×', "*"), Map.entry('÷', "/"),
            Map.entry('≤', "<="), Map.entry('≥', ">="), Map.entry('≠', "!="), Map.entry(' ', " "),
            Map.entry('​', ""), Map.entry('﻿', ""), Map.entry('；', ";"), Map.entry('（', "("),
            Map.entry('）', ")"), Map.entry('，', ","), Map.entry('　', " "), Map.entry('…', "..."),
            Map.entry('«', "\""), Map.entry('»', "\""), Map.entry('＝', "="), Map.entry('→', "->"));

    // ------------------------------------------------------------------ scoring

    private Score score(String s) {
        Score m = memo.get(s);
        if (m != null) {
            return m;
        }
        parses++;
        List<Javac.Diag> errs = Javac.parse(s).errors();
        Score r;
        if (errs.isEmpty()) {
            Javac.Unit au = Javac.analyze(s);
            int bad = au.errors().size();
            for (Javac.Diag d : au.diags) {
                if (d.code().equals("compiler.warn.empty.if")) {
                    bad++;
                }
            }
            r = new Score(true, 0, 0, bad);
        } else {
            List<Lexer.Tok> t = Lexer.lex(s);
            r = new Score(false, t.size() - Lexer.indexAt(t, errs.get(0).pos()), errs.size(), -1);
        }
        memo.put(s, r);
        return r;
    }

    private static boolean accept(Score base, Score s, int k) {
        if (s.clean()) {
            return true;
        }
        if (s.after() <= base.after() - k) {
            return true;
        }
        return base.after() <= k && s.after() < base.after() || (base.after() <= k && s.after() <= base.after() && s.errors() < base.errors());
    }

    private static boolean better(Found a, Cand c, Score s, int dist, int distA) {
        if (a == null) {
            return true;
        }
        double ec = effective(c, s);
        double ea = effective(a.c(), a.s());
        if (Math.abs(ec - ea) > 1e-9) {
            return ec < ea;
        }
        if (s.clean() != a.s().clean()) {
            return s.clean();
        }
        if (s.after() != a.s().after()) {
            return s.after() < a.s().after();
        }
        return dist < distA;
    }

    // ------------------------------------------------------------------ driver

    private long deadline = Long.MAX_VALUE;

    /** Confusable characters (smart quotes, Unicode minus, NBSP, full-width punctuation) outside literals and comments. */
    static String normalizeConfusables(String src) {
        boolean[] code = Text.codeMask(src);
        StringBuilder out = new StringBuilder(src.length());
        for (int i = 0; i < src.length(); i++) {
            String r = CONFUSABLE.get(src.charAt(i));
            out.append(r != null && code[i] ? r : String.valueOf(src.charAt(i)));
        }
        return out.toString();
    }

    String run(String src) {
        deadline = System.currentTimeMillis() + Long.getLong("jfixpass.syntaxBudgetSec", 90) * 1000;
        if (!Javac.parse(src).errors().isEmpty()) {
            String norm = normalizeConfusables(src);
            if (!norm.equals(src)) {
                int changed = 0;
                boolean[] mask = Text.codeMask(src);
                for (int i = 0; i < src.length(); i++) {
                    if (CONFUSABLE.containsKey(src.charAt(i)) && mask[i]) {
                        changed++;
                    }
                }
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("phase", "syntax");
                m.put("line", (double) 0);
                m.put("diagnostic", "compiler.err.illegal.char");
                m.put("message", "confusable characters outside literals");
                m.put("stage", "lexical/structural");
                m.put("rule", "normalize " + changed + " confusable characters (smart quotes, Unicode dashes, NBSP, full-width punctuation)");
                m.put("before", "");
                m.put("after", "");
                log.add(m);
                src = norm;
            }
        }
        for (int iter = 0; iter < 400; iter++) {
            Javac.Unit pu = Javac.parse(src);
            List<Javac.Diag> errs = pu.errors();
            if (errs.isEmpty()) {
                return src;
            }
            Javac.Diag d = errs.get(0);
            List<Lexer.Tok> toks = Lexer.lex(src);
            int e0 = Lexer.indexAt(toks, d.pos());
            Score base = new Score(false, toks.size() - e0, errs.size(), -1);
            int k = Math.max(1, Math.min(8, base.after()));
            Found f = null;
            String stage = null;
            String[] stages = {"lexical/structural", "priority tokens", "full vocabulary", "two edits", "excision"};
            boolean late = System.currentTimeMillis() > deadline;
            for (int lvl = late ? 4 : 0; lvl < stages.length && f == null; lvl++) {
                f = switch (lvl) {
                    case 0 -> best(src, toks, e0, base, k, lexical(src, toks, d, e0));
                    case 1 -> best(src, toks, e0, base, k, tokenEdits(src, toks, d, e0, 6, false, 5));
                    case 2 -> best(src, toks, e0, base, k, tokenEdits(src, toks, d, e0, 12, true, 6));
                    case 3 -> beam(src, toks, d, e0, base, k);
                    default -> best(src, toks, e0, base, 1, excisions(src, toks, e0));
                };
                stage = stages[lvl];
                // not a clean, well-typed, cheap repair: also try the full vocabulary and keep the cheaper total
                if (f != null && lvl < 2 && !(f.s().clean() && f.s().types() == 0 && f.c().cost() <= 1.3) && !late) {
                    Found g = best(src, toks, e0, base, k, tokenEdits(src, toks, d, e0, 8, true, 5));
                    if (g != null && effective(g.c(), g.s()) < effective(f.c(), f.s()) - 1e-9) {
                        f = g;
                        stage = stages[2];
                    }
                }
            }
            if (f == null) {
                failure = "no syntax repair found for " + d.brief();
                return src;
            }
            if (stage.equals("excision")) {
                lossy = true;
            }
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("phase", "syntax");
            m.put("line", (double) d.line());
            m.put("diagnostic", d.code());
            m.put("message", d.message().split("\n")[0]);
            m.put("stage", stage);
            List<String> rules = new ArrayList<>();
            for (Cand c : f.chain()) {
                rules.add(c.rule());
            }
            m.put("rule", String.join(" + ", rules));
            int at = f.chain().get(0).edit().start();
            m.put("before", lineText(src, at));
            m.put("after", lineText(f.src(), Math.min(at, f.src().length())));
            if (f.ties().size() > 1) {
                m.put("tied_alternatives", (double) f.ties().size());
            }
            if (stage.equals("excision")) {
                m.put("deleted", src.substring(f.c().edit().start(), f.c().edit().end()));
            }
            log.add(m);
            for (Cand c : f.chain()) {
                CompileRepair.shiftAll(holes, c.edit());
            }
            if (f.chain().size() == 1 && f.ties().size() > 1 && f.c().token() != null) {
                Text.Edit e = f.c().edit();
                int hs = e.start() + e.text().indexOf(f.c().token());
                List<String> alts = new ArrayList<>();
                for (Cand t : f.ties()) {
                    if (t.token() != null && !alts.contains(t.token())) {
                        alts.add(t.token());
                    }
                }
                if (alts.size() > 1 && hs >= e.start()) {
                    holes.add(new CompileRepair.Hole(hs, hs + f.c().token().length(), alts, "syntax " + d.code()));
                }
            }
            src = f.src();
        }
        failure = "syntax repair iteration limit";
        return src;
    }

    private static String lineText(String src, int pos) {
        int a = Text.lineStart(src, pos);
        int b = src.indexOf('\n', a);
        return src.substring(a, b < 0 ? src.length() : b).strip();
    }

    /**
     * Token edits must not re-lex code outside their own window (no accidental comment, string or text-block openers
     * swallowing the rest of the file). Lexical-stage candidates are exempt; they change literal boundaries on purpose.
     */
    static boolean local(List<Lexer.Tok> old, Text.Edit e, String ns) {
        List<Lexer.Tok> nt = Lexer.lex(ns);
        int b = Lexer.indexAt(old, e.end());
        while (b < old.size() && old.get(b).start() < e.end()) {
            b++;
        }
        int m = old.size() - b;
        if (nt.size() < m || Math.abs(nt.size() - old.size()) > 4) {
            return false;
        }
        for (int i = 0; i < m; i++) {
            Lexer.Tok a = old.get(old.size() - 1 - i);
            Lexer.Tok c = nt.get(nt.size() - 1 - i);
            if (!a.text().equals(c.text()) || a.kind() != c.kind()) {
                return false;
            }
        }
        int a = Lexer.indexAt(old, e.start());
        for (int i = 0; i < a && i < nt.size(); i++) {
            if (!old.get(i).text().equals(nt.get(i).text())) {
                return false;
            }
        }
        for (Lexer.Tok t : nt) {
            if (t.unterminated() && t.start() >= e.start() - 1 && t.start() <= e.start() + e.text().length()) {
                return false;
            }
        }
        return true;
    }

    /** Try candidates in cost order; collect equally cheap acceptable alternatives at the winner's site. */
    private Found best(String src, List<Lexer.Tok> toks, int e0, Score base, int k, List<Cand> cands) {
        cands.sort((a, b) -> Double.compare(a.cost(), b.cost()));
        Found best = null;
        List<Cand> accepted = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        int evaluated = 0;
        for (Cand c : cands) {
            if (best != null) {
                double limit = Math.min(effective(best.c(), best.s()), best.c().cost() + 4.0);
                if (c.cost() >= limit - 1e-9 || (evaluated > 3000 && !c.group().equals("cut"))) {
                    break;
                }
            }
            evaluated++;
            String ns;
            try {
                ns = c.edit().apply(src);
            } catch (RuntimeException e) {
                continue;
            }
            if (!seen.add(ns)) {
                continue;
            }
            if (!c.group().startsWith("lex") && !c.group().equals("cut") && !local(toks, c.edit(), ns)) {
                continue;
            }
            Score s = score(ns);
            if (!accept(base, s, k)) {
                continue;
            }
            accepted.add(c);
            int dist = Math.abs(e0 - c.site());
            int distB = best == null ? 0 : Math.abs(e0 - best.c().site());
            if (better(best, c, s, dist, distB)) {
                best = new Found(c, ns, s, List.of(), List.of(c));
            }
        }
        if (best == null) {
            return null;
        }
        List<Cand> ties = new ArrayList<>();
        for (Cand c : accepted) {
            if (Math.abs(c.cost() - best.c().cost()) < 1e-9 && c.group().equals(best.c().group())) {
                Score sc = score(c.edit().apply(src));
                if (Math.abs(effective(c, sc) - effective(best.c(), best.s())) < 1e-9) {
                    ties.add(c);
                }
            }
        }
        return new Found(best.c(), best.src(), best.s(), ties, best.chain());
    }

    /** Depth-2 search: expand the most promising non-accepted single edits by a second edit near their new error. */
    private Found beam(String src, List<Lexer.Tok> toks, Javac.Diag d, int e0, Score base, int k) {
        List<Cand> first = tokenEdits(src, toks, d, e0, 8, false, 4);
        record P(Cand c, String s, Score sc) {
        }
        List<P> partial = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Cand c : first) {
            String ns = c.edit().apply(src);
            if (!seen.add(ns) || !local(toks, c.edit(), ns)) {
                continue;
            }
            Score s = score(ns);
            if (!s.clean()) {
                partial.add(new P(c, ns, s));
            }
        }
        partial.sort((a, b) -> a.sc().after() != b.sc().after() ? Integer.compare(a.sc().after(), b.sc().after())
                : Double.compare(a.c().cost(), b.c().cost()));
        Found best = null;
        double bestCost = Double.MAX_VALUE;
        for (P p : partial.subList(0, Math.min(14, partial.size()))) {
            List<Javac.Diag> errs = Javac.parse(p.s()).errors();
            if (errs.isEmpty()) {
                continue;
            }
            List<Lexer.Tok> t2 = Lexer.lex(p.s());
            int e1 = Lexer.indexAt(t2, errs.get(0).pos());
            for (Cand c2 : tokenEdits(p.s(), t2, errs.get(0), e1, 6, false, 4)) {
                double cost = p.c().cost() + c2.cost();
                if (cost >= bestCost) {
                    continue;
                }
                String ns = c2.edit().apply(p.s());
                if (!seen.add(ns) || !local(t2, c2.edit(), ns)) {
                    continue;
                }
                Score s = score(ns);
                if (accept(base, s, k)) {
                    cost += s.clean() ? TYPE_PENALTY * Math.min(s.types(), 5) : 0;
                    if (cost >= bestCost) {
                        continue;
                    }
                    bestCost = cost;
                    Cand combined = new Cand(p.c().edit(), cost, p.c().rule() + " + " + c2.rule(), p.c().site(), "pair", null);
                    best = new Found(combined, ns, s, List.of(), List.of(p.c(), c2));
                }
            }
        }
        return best;
    }

    // ------------------------------------------------------------------ candidate generators

    private static String insertText(String src, int pos, String tok) {
        boolean wordTok = Character.isJavaIdentifierPart(tok.charAt(0)) || tok.startsWith("\"");
        char before = pos > 0 ? src.charAt(pos - 1) : ' ';
        char after = pos < src.length() ? src.charAt(pos) : ' ';
        String lead = NO_SPACE_BEFORE.contains(tok) && !wordTok ? "" : (Character.isWhitespace(before) || before == '(' || before == '['
                ? "" : " ");
        String trail = (wordTok || !NO_SPACE_BEFORE.contains(tok)) && !Character.isWhitespace(after) && after != ')'
                && after != ';' && after != ',' && after != ']' ? " " : "";
        return lead + tok + trail;
    }

    /** A ';' placed right after ';', '{' or '}' (or before ';') makes an empty statement: rarely intended. */
    private static double emptyStmt(List<Lexer.Tok> toks, int before, int after, String tok) {
        if (!tok.equals(";")) {
            return 0;
        }
        String p = before >= 0 && before < toks.size() ? toks.get(before).text() : "";
        String n = after >= 0 && after < toks.size() ? toks.get(after).text() : "";
        return p.equals(";") || p.equals("{") || p.equals("}") || n.equals(";") ? 0.6 : 0;
    }

    private static Cand insert(String src, List<Lexer.Tok> toks, int gap, String tok, double cost, String rule) {
        cost += emptyStmt(toks, gap - 1, gap, tok);
        int pos = gap == 0 ? (toks.isEmpty() ? 0 : toks.get(0).start()) : toks.get(gap - 1).end();
        return new Cand(new Text.Edit(pos, pos, insertText(src, pos, tok)), cost, rule + " '" + tok + "'", gap, "ins@" + gap, tok);
    }

    private static Cand replace(String src, List<Lexer.Tok> toks, int i, String tok, double cost) {
        cost += emptyStmt(toks, i - 1, i + 1, tok);
        Lexer.Tok t = toks.get(i);
        if (t.text().equals("{") || t.text().equals("}")) {
            cost += 0.5; // changing block structure: the indentation usually says the brace is right
        }
        String text = tok;
        if (Character.isJavaIdentifierPart(tok.charAt(0))) {
            if (t.start() > 0 && Character.isJavaIdentifierPart(src.charAt(t.start() - 1))) {
                text = " " + text;
            }
            if (t.end() < src.length() && Character.isJavaIdentifierPart(src.charAt(t.end()))) {
                text = text + " ";
            }
        }
        return new Cand(new Text.Edit(t.start(), t.end(), text), cost, "replace '" + t.text() + "' with '" + tok + "'", i,
                "rep@" + i, tok);
    }

    private static Cand delete(String src, List<Lexer.Tok> toks, int i, double cost) {
        Lexer.Tok t = toks.get(i);
        if (t.text().indexOf('\n') >= 0) {
            cost += 50;
        }
        if (t.text().equals("{") || t.text().equals("}")) {
            cost += 0.5;
        }
        // losing content (names, literals) is worse than losing punctuation
        switch (t.kind()) {
            case IDENT, NUMBER, STRING, CHAR -> cost += 0.5;
            case KEYWORD -> cost += 0.2;
            default -> {
            }
        }
        int s = t.start();
        int e = t.end();
        if (s > 0 && src.charAt(s - 1) == ' ' && (e >= src.length() || Character.isWhitespace(src.charAt(e)))) {
            s--;
        }
        return new Cand(new Text.Edit(s, e, ""), cost, "delete '" + t.text() + "'", i, "del@" + i, null);
    }

    private static final Pattern QUOTED = Pattern.compile("'([^']+)'");

    private static List<String> expected(Javac.Diag d) {
        List<String> out = new ArrayList<>();
        String first = d.message().split("\n")[0];
        if (first.contains("expected")) {
            Matcher m = QUOTED.matcher(first);
            while (m.find()) {
                out.add(m.group(1));
            }
            if (first.contains("<identifier> expected")) {
                out.add("x");
            }
        }
        return out;
    }

    private static List<Cand> tokenEdits(String src, List<Lexer.Tok> toks, Javac.Diag d, int e0, int window, boolean full) {
        return tokenEdits(src, toks, d, e0, window, full, 1);
    }

    private static List<Cand> tokenEdits(String src, List<Lexer.Tok> toks, Javac.Diag d, int e0, int window, boolean full, int ahead) {
        List<Cand> out = new ArrayList<>();
        int n = toks.size();
        int lo = Math.max(0, e0 - window);
        int hi = Math.min(n, e0 + ahead);
        List<String> exp = expected(d);
        Set<String> vocab = new LinkedHashSet<>(PRIORITY);
        if (full) {
            for (String o : Lexer.OPS) {
                vocab.add(o);
            }
            vocab.addAll(KEYWORDS);
            for (int i = Math.max(0, e0 - 40); i < Math.min(n, e0 + 40); i++) {
                if (toks.get(i).kind() == Lexer.K.IDENT) {
                    vocab.add(toks.get(i).text());
                }
            }
            vocab.add("0");
            vocab.add("1");
            vocab.add("\"\"");
        }
        for (int g = lo; g <= hi; g++) {
            double near = 0.001 * Math.abs(e0 - g);
            for (String x : exp) {
                if (!x.equals("x")) {
                    out.add(insert(src, toks, g, x, 0.5 + near, "insert expected"));
                }
            }
            for (String x : vocab) {
                double c = PRIORITY.contains(x) ? 1.0 : Lexer.KEYWORDS.contains(x) ? 1.5
                        : Character.isJavaIdentifierStart(x.charAt(0)) ? 1.6 : x.matches("[0-9\"].*") ? 1.7 : 1.3;
                if (full && PRIORITY.contains(x)) {
                    continue;
                }
                out.add(insert(src, toks, g, x, c + near, "insert"));
            }
        }
        for (int i = lo; i < Math.min(n, hi); i++) {
            double near = 0.001 * Math.abs(e0 - i);
            if (!full) {
                out.add(delete(src, toks, i, 1.0 + near));
                if (i + 1 < n) {
                    Lexer.Tok a = toks.get(i);
                    Lexer.Tok b = toks.get(i + 1);
                    out.add(new Cand(new Text.Edit(a.start(), b.end(), b.text() + src.substring(a.end(), b.start()) + a.text()), 1.0 + near,
                            "swap '" + a.text() + "' '" + b.text() + "'", i, "swap@" + i, null));
                }
            }
            for (String x : full ? vocab : new LinkedHashSet<>(PRIORITY)) {
                if (x.equals(toks.get(i).text())) {
                    continue;
                }
                boolean opToOp = toks.get(i).kind() == Lexer.K.OP && !Character.isJavaIdentifierStart(x.charAt(0)) && !x.matches("[0-9\"].*");
                double c = PRIORITY.contains(x) ? 1.2 : Lexer.KEYWORDS.contains(x) ? 1.8
                        : Character.isJavaIdentifierStart(x.charAt(0)) ? 1.8 : opToOp ? 1.3 : 1.5;
                if (full && PRIORITY.contains(x)) {
                    continue;
                }
                out.add(replace(src, toks, i, x, c + near));
            }
            for (String x : exp) {
                if (!x.equals("x") && !x.equals(toks.get(i).text())) {
                    out.add(replace(src, toks, i, x, 1.1 + near));
                }
            }
        }
        {
            Set<String> known = new HashSet<>(Lexer.KEYWORDS);
            for (Lexer.Tok t : toks) {
                if (t.kind() == Lexer.K.IDENT) {
                    known.add(t.text());
                }
            }
            for (int i = Math.max(0, e0 - 12); i < Math.min(n, hi); i++) {
                Lexer.Tok t = toks.get(i);
                double near = 0.001 * Math.abs(e0 - i);
                if (t.kind() == Lexer.K.IDENT && t.text().length() >= 3) {
                    String w = t.text();
                    // split a glued identifier at a known-word boundary, or cut a keyword glued inside it
                    for (int pcut = 1; pcut < w.length(); pcut++) {
                        String a = w.substring(0, pcut);
                        String b = w.substring(pcut);
                        if (known.contains(a) && known.contains(b)) {
                            out.add(new Cand(new Text.Edit(t.start(), t.end(), a), 0.95 + near, "un-glue '" + w + "' -> '" + a + "'", i,
                                    "unglue@" + i, a));
                            if (!b.equals(a)) {
                                out.add(new Cand(new Text.Edit(t.start(), t.end(), b), 0.95 + near, "un-glue '" + w + "' -> '" + b + "'", i,
                                        "unglue@" + i, b));
                            }
                        }
                        if (known.contains(a) || known.contains(b)) {
                            for (String sep : new String[] {" ", "(", ".", ","}) {
                                out.add(new Cand(new Text.Edit(t.start() + pcut, t.start() + pcut, sep), 1.0 + near,
                                        "split '" + w + "' with '" + sep.strip() + "'", i, "split@" + i + ":" + pcut, null));
                            }
                        }
                    }
                    for (String kw : Lexer.KEYWORDS) {
                        int at = w.indexOf(kw);
                        if (at > 0 && at + kw.length() < w.length()) {
                            for (String sep : new String[] {"(", " ", ".", ""}) {
                                out.add(new Cand(new Text.Edit(t.start() + at, t.start() + at + kw.length(), sep), 1.0 + near,
                                        "replace glued '" + kw + "' in '" + w + "' with '" + sep + "'", i, "glue@" + i, null));
                            }
                        }
                    }
                }
                if (i + 1 < n) {
                    Lexer.Tok b = toks.get(i + 1);
                    String joined = t.text() + b.text();
                    if (b.start() > t.end() && known.contains(joined) && Character.isJavaIdentifierPart(t.text().charAt(0))) {
                        out.add(new Cand(new Text.Edit(t.end(), b.start(), ""), 0.8 + near, "join '" + t.text() + "' '" + b.text() + "'",
                                i, "join@" + i, null));
                    }
                }
            }
        }
        if (!full) {
            // a run of two deletions (e.g. duplicated tokens) as one cheap candidate
            for (int i = lo; i + 1 < Math.min(n, hi); i++) {
                Lexer.Tok a = toks.get(i);
                Lexer.Tok b = toks.get(i + 1);
                out.add(new Cand(new Text.Edit(a.start(), b.end(), ""), 1.9 + 0.001 * Math.abs(e0 - i),
                        "delete '" + a.text() + " " + b.text() + "'", i, "del2@" + i, null));
            }
        }
        return out;
    }

    /** Diagnostic-specific lexical repairs plus indentation-guided brace repairs. */
    private static List<Cand> lexical(String src, List<Lexer.Tok> toks, Javac.Diag d, int e0) {
        List<Cand> out = new ArrayList<>();
        int pos = Math.max(0, Math.min(d.pos(), src.length() - 1));
        String code = d.code();
        int ls = Text.lineStart(src, pos);
        int le = src.indexOf('\n', pos);
        if (le < 0) {
            le = src.length();
        }
        // confusable characters anywhere on the error line, outside string literals
        boolean[] mask = Text.codeMask(src);
        List<Text.Edit> conf = new ArrayList<>();
        for (int i = ls; i < le; i++) {
            String r = CONFUSABLE.get(src.charAt(i));
            if (r != null && (mask[i] || src.charAt(i) == '“' || src.charAt(i) == '”')) {
                conf.add(new Text.Edit(i, i + 1, r));
            }
        }
        if (conf.size() > 1) {
            // all confusables on the line together (paired smart quotes must be fixed as a pair)
            StringBuilder line = new StringBuilder(src.substring(ls, le));
            for (int j = conf.size() - 1; j >= 0; j--) {
                Text.Edit e = conf.get(j);
                line.replace(e.start() - ls, e.end() - ls, e.text());
            }
            out.add(new Cand(new Text.Edit(ls, le, line.toString()), 0.25, "replace " + conf.size() + " confusable characters on the line",
                    e0, "lex-conf-line", null));
        }
        for (Text.Edit e : conf) {
            out.add(new Cand(e, 0.3, "replace confusable U+" + String.format("%04X", (int) src.charAt(e.start())) + " with '" + e.text() + "'",
                    e0, "lex-conf@" + e.start(), null));
        }
        if (code.equals("compiler.err.illegal.char") && pos < src.length()) {
            out.add(new Cand(new Text.Edit(pos, pos + 1, ""), 0.6, "delete illegal character '" + src.charAt(pos) + "'", e0, "lex-" + "del@c", null));
        }
        if (code.equals("compiler.err.unclosed.str.lit")) {
            int q = src.indexOf('"', pos) == pos ? pos : src.lastIndexOf('"', pos);
            if (q >= ls) {
                String rest = src.substring(q + 1, le);
                int r = 0;
                for (int j = rest.length() - 1; j >= 0; j--) {
                    char c = rest.charAt(j);
                    if (c == ')' || c == ';' || c == ',' || c == '+') {
                        int at = q + 1 + j;
                        while (at > q + 1 && src.charAt(at - 1) == ' ') {
                            at--;
                        }
                        out.add(new Cand(new Text.Edit(at, at, "\""), 0.5 + 0.01 * r++, "close string literal", e0, "lex-" + "str", null));
                    }
                }
                int end = le;
                while (end > q + 1 && Character.isWhitespace(src.charAt(end - 1))) {
                    end--;
                }
                out.add(new Cand(new Text.Edit(end, end, "\""), 0.6, "close string literal at end of line", e0, "lex-" + "str", null));
                out.add(new Cand(new Text.Edit(q, q + 1, ""), 1.0, "delete stray '\"'", e0, "lex-" + "strdel", null));
            }
        }
        if (code.equals("compiler.err.unclosed.char.lit") || code.equals("compiler.err.empty.char.lit")
                || code.equals("compiler.err.illegal.line.end.in.char.lit")) {
            int q = src.charAt(pos) == '\'' ? pos : src.lastIndexOf('\'', pos);
            if (q >= ls) {
                int q2 = src.indexOf('\'', q + 1);
                if (q2 > q && q2 < le) {
                    out.add(new Cand(new Text.Edit(q, q2 + 1, "\"" + src.substring(q + 1, q2) + "\""), 0.5, "character literal -> string", e0,
                            "lex-" + "chr", null));
                }
                if (q + 2 <= le) {
                    out.add(new Cand(new Text.Edit(q + 2, q + 2, "'"), 0.6, "close character literal", e0, "lex-" + "chr", null));
                }
                out.add(new Cand(new Text.Edit(q, q + 1, ""), 1.0, "delete stray '''", e0, "lex-" + "chrdel", null));
            }
        }
        if (code.equals("compiler.err.unclosed.comment")) {
            int c = src.lastIndexOf("/*", pos + 1);
            if (c >= 0) {
                int lineEnd = src.indexOf('\n', c);
                for (int j = 0; j < 30 && lineEnd > 0; j++) {
                    out.add(new Cand(new Text.Edit(lineEnd, lineEnd, " */"), 0.5 + 0.02 * j, "close comment", e0, "lex-" + "cmt", null));
                    lineEnd = src.indexOf('\n', lineEnd + 1);
                }
                out.add(new Cand(new Text.Edit(c, c + 2, ""), 1.0, "delete '/*'", e0, "lex-" + "cmtdel", null));
            }
        }
        if (code.equals("compiler.err.illegal.esc.char")) {
            int b = src.lastIndexOf('\\', pos);
            if (b >= ls) {
                out.add(new Cand(new Text.Edit(b, b, "\\"), 0.5, "escape the backslash", e0, "lex-" + "esc", null));
                out.add(new Cand(new Text.Edit(b, b + 1, ""), 0.6, "delete backslash", e0, "lex-" + "esc", null));
            }
        }
        if (code.startsWith("compiler.err.malformed") || code.startsWith("compiler.err.illegal.underscore")
                || code.startsWith("compiler.err.invalid") || code.equals("compiler.err.int.number.too.large")) {
            int i = Math.min(e0, toks.size() - 1);
            if (i >= 0 && toks.get(i).kind() == Lexer.K.NUMBER) {
                Lexer.Tok t = toks.get(i);
                String digits = t.text().replaceAll("[^0-9.]", "");
                int dot = digits.indexOf('.', digits.indexOf('.') + 1);
                if (dot > 0) {
                    digits = digits.substring(0, dot);
                }
                if (!digits.isEmpty() && !digits.equals(t.text())) {
                    out.add(new Cand(new Text.Edit(t.start(), t.end(), digits), 0.8, "normalize number literal", i, "lex-" + "num", null));
                }
            }
        }
        for (CompileRepair.Fix f : CompileRepair.braceCandidates(src)) {
            out.add(new Cand(f.edit(), 1.0, f.rule(), e0, "brace", null));
        }
        if (code.equals("compiler.err.premature.eof")) {
            out.add(new Cand(new Text.Edit(src.length(), src.length(), "\n}\n"), 1.1, "close block at end of file", e0, "eof", null));
        }
        return out;
    }

    record Step(String src, Map<String, Object> log, boolean lossy, Text.Edit edit, CompileRepair.Hole hole) {
    }

    /**
     * Generic compile-error repair for errors the rule-based type stage cannot fix: token edits on both sides of the
     * first attribution error. The file must still parse and the number of compile errors must fall; candidates are
     * ranked by edit cost plus one per remaining error. Excision of the error line is the last, reported resort.
     */
    Step typeStep(String src, List<Javac.Diag> errs) {
        for (int which = 0; which < Math.min(3, errs.size()); which++) {
            Javac.Diag d = errs.get(which);
            List<Lexer.Tok> toks = Lexer.lex(src);
            int e0 = Math.min(Lexer.indexAt(toks, Math.max(0, d.start() >= 0 ? d.start() : d.pos())), Math.max(0, toks.size() - 1));
            for (int lvl = 0; lvl < 4; lvl++) {
                List<Cand> cands = switch (lvl) {
                    case 0 -> {
                        List<Cand> l = tokenEdits(src, toks, d, e0, 6, false, 7);
                        for (CompileRepair.Fix f : CompileRepair.braceCandidates(src)) {
                            l.add(new Cand(f.edit(), 1.0, f.rule(), e0, "brace", null));
                        }
                        yield l;
                    }
                    case 1 -> {
                        // identifiers and literals first: type errors usually mean a wrong or missing name or value
                        List<Cand> l = new ArrayList<>();
                        for (Cand c : tokenEdits(src, toks, d, e0, 8, true, 8)) {
                            if (c.token() != null && (Character.isJavaIdentifierStart(c.token().charAt(0))
                                    || c.token().matches("[0-9\"].*")) && !Lexer.KEYWORDS.contains(c.token())) {
                                l.add(c);
                            }
                        }
                        yield l;
                    }
                    case 2 -> tokenEdits(src, toks, d, e0, 8, true, 8);
                    default -> excisions(src, toks, e0);
                };
                cands.sort((a, b) -> Double.compare(a.cost(), b.cost()));
                Cand best = null;
                String bestSrc = null;
                double bestKey = Double.MAX_VALUE;
                Set<String> seen = new HashSet<>();
                Map<Cand, Double> keys = new LinkedHashMap<>();
                int tried = 0;
                for (Cand c : cands) {
                    if (c.cost() >= bestKey || tried++ > 2500) {
                        break;
                    }
                    String ns = c.edit().apply(src);
                    if (!seen.add(ns) || (!c.group().equals("cut") && !local(toks, c.edit(), ns))) {
                        continue;
                    }
                    parses++;
                    if (!Javac.parse(ns).errors().isEmpty()) {
                        continue;
                    }
                    int cnt = Javac.analyze(ns).errors().size();
                    if (cnt >= errs.size()) {
                        continue;
                    }
                    double key = c.cost() + cnt;
                    keys.put(c, key);
                    if (key < bestKey) {
                        bestKey = key;
                        best = c;
                        bestSrc = ns;
                    }
                }
                if (best != null) {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("phase", "types (token search)");
                    m.put("line", (double) d.line());
                    m.put("diagnostic", d.code());
                    m.put("message", d.message().split("\n")[0]);
                    m.put("rule", best.rule());
                    m.put("before", lineText(src, best.edit().start()));
                    m.put("after", lineText(bestSrc, Math.min(best.edit().start(), bestSrc.length())));
                    if (lvl == 3) {
                        m.put("deleted", src.substring(best.edit().start(), best.edit().end()));
                    }
                    CompileRepair.Hole hole = null;
                    if (best.token() != null) {
                        List<String> alts = new ArrayList<>();
                        for (Map.Entry<Cand, Double> en : keys.entrySet()) {
                            Cand c = en.getKey();
                            if (Math.abs(en.getValue() - bestKey) < 1e-9 && c.group().equals(best.group()) && c.token() != null
                                    && !alts.contains(c.token())) {
                                alts.add(c.token());
                            }
                        }
                        int hs = best.edit().start() + best.edit().text().indexOf(best.token());
                        if (alts.size() > 1 && hs >= best.edit().start()) {
                            hole = new CompileRepair.Hole(hs, hs + best.token().length(), alts, "types " + d.code());
                            m.put("tied_alternatives", (double) alts.size());
                        }
                    }
                    return new Step(bestSrc, m, lvl == 3, best.edit(), hole);
                }
            }
        }
        return null;
    }

    /**
     * Lossy last resort: delete the smallest line range around the error (windows grow to 32 lines on each side),
     * then truncate from the error line and close the open blocks. Cost grows with the amount deleted.
     */
    private static List<Cand> excisions(String src, List<Lexer.Tok> toks, int e0) {
        List<Cand> out = new ArrayList<>();
        int pos = e0 < toks.size() ? toks.get(e0).start() : Math.max(0, src.length() - 1);
        int line = Text.lineOf(src, pos);
        String[] lines = src.split("\n", -1);
        int[] starts = new int[lines.length + 1];
        for (int i = 1; i <= lines.length; i++) {
            starts[i] = starts[i - 1] + lines[i - 1].length() + 1;
        }
        int[] reach = {0, 1, 2, 4, 8, 16, 32};
        Set<String> seen = new HashSet<>();
        for (int up : reach) {
            for (int down : reach) {
                int first = Math.max(1, line - up);
                int last = Math.min(lines.length, line + down);
                if (!seen.add(first + ":" + last)) {
                    continue;
                }
                int s = starts[first - 1];
                int e = Math.min(src.length(), starts[last]);
                out.add(new Cand(new Text.Edit(s, e, ""), 10.0 + (e - s) * 0.001, "delete lines " + first + "-" + last, e0, "cut", null));
            }
        }
        if (e0 < toks.size()) {
            int s = toks.get(e0).start();
            int e = src.indexOf('\n', s);
            if (e > s) {
                out.add(new Cand(new Text.Edit(s, e, ""), 9.0 + (e - s) * 0.001, "delete rest of line " + line, e0, "cut", null));
            }
        }
        // guaranteed last resort: truncate at the latest statement boundary at or before the error line that parses
        // once the open blocks are closed (the empty prefix always does)
        boolean[] code = Text.codeMask(src);
        int[] depthAt = new int[lines.length + 1];
        int depth = 0;
        for (int li = 0; li < lines.length; li++) {
            depthAt[li] = depth;
            for (int i = starts[li]; i < starts[li] + lines[li].length(); i++) {
                if (code[i] && src.charAt(i) == '{') {
                    depth++;
                } else if (code[i] && src.charAt(i) == '}') {
                    depth--;
                }
            }
        }
        for (int li = Math.max(0, line - 1); li >= 0; li--) {
            String prev = li == 0 ? "" : lines[li - 1].strip();
            if (li > 0 && !(prev.endsWith(";") || prev.endsWith("{") || prev.endsWith("}") || prev.isEmpty())) {
                continue;
            }
            StringBuilder close = new StringBuilder();
            for (int k = Math.max(0, depthAt[li]) - 1; k >= 0; k--) {
                close.append("    ".repeat(k)).append("}\n");
            }
            int ls = starts[li];
            out.add(new Cand(new Text.Edit(ls, src.length(), close.toString()), 100.0 + (src.length() - ls) * 0.001,
                    "truncate from line " + (li + 1) + " and close blocks", e0, "cut", null));
        }
        return out;
    }
}
