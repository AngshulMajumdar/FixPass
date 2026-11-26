package jfixpass;

import com.sun.source.tree.ClassTree;
import com.sun.source.tree.ImportTree;
import com.sun.source.tree.LiteralTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.ReturnTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.TreeScanner;
import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.net.URI;
import java.nio.file.DirectoryStream;
import java.nio.file.FileSystem;
import java.nio.file.FileSystems;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Compiler-guided repair. Stage 1 removes syntax errors (javac parse diagnostics); stage 2 removes attribution
 * errors (unresolved names, type mismatches, missing returns). Every candidate edit is re-checked by javac and kept
 * only if it resolves the targeted diagnostic without making the file worse. When several candidates are equally
 * good the choice is recorded as a hole so the test-driven search can revisit it.
 */
final class CompileRepair {
    static final class Hole {
        int start;
        int end;
        final List<String> alternatives;
        final String reason;

        Hole(int start, int end, List<String> alternatives, String reason) {
            this.start = start;
            this.end = end;
            this.alternatives = alternatives;
            this.reason = reason;
        }

        /** Returns false when the edit overlaps the hole partially (hole is dropped). */
        boolean shift(Text.Edit e) {
            if (e.start() == start && e.end() == end) {
                end = start + e.text().length();
                return true;
            }
            if (e.end() <= start) {
                start += e.delta();
                end += e.delta();
                return true;
            }
            return e.start() >= end;
        }
    }

    record Fix(Text.Edit edit, int cost, String rule) {
    }

    static void shiftAll(List<Hole> holes, Text.Edit e) {
        holes.removeIf(h -> !h.shift(e));
    }

    final List<Map<String, Object>> log = new ArrayList<>();
    final List<Hole> holes = new ArrayList<>();
    String failure;
    List<String> signatures;
    int candidatesTried;

    // ------------------------------------------------------------------ driver

    boolean lossy;
    int syntaxParses;
    private int tokenSteps;

    String run(String src) {
        SyntaxRepair sr = new SyntaxRepair();
        src = sr.run(src);
        log.addAll(sr.log);
        syntaxParses = sr.parses;
        candidatesTried += sr.parses;
        lossy = sr.lossy;
        if (sr.failure != null) {
            failure = sr.failure;
            return src;
        }
        src = typeDirected(src, sr.holes);
        holes.addAll(sr.holes);
        signatures = signatures(Javac.parse(src));
        Set<String> seen = new HashSet<>();
        seen.add(src);
        return attributePhase(src, seen);
    }

    /** Settle tied syntax alternatives by type checking: keep those with the fewest attribution errors. */
    private String typeDirected(String src, List<Hole> syntaxHoles) {
        List<Hole> order = new ArrayList<>(syntaxHoles);
        order.sort((a, b) -> Integer.compare(b.start, a.start));
        for (Hole h : order) {
            if (!syntaxHoles.contains(h)) {
                continue;
            }
            String now = src.substring(h.start, h.end);
            int bestCount = Integer.MAX_VALUE;
            String bestAlt = now;
            Map<String, Integer> counts = new LinkedHashMap<>();
            for (String alt : h.alternatives) {
                String ns = new Text.Edit(h.start, h.end, alt).apply(src);
                candidatesTried++;
                if (!Javac.parse(ns).errors().isEmpty()) {
                    continue;
                }
                int c = Javac.analyze(ns).errors().size();
                counts.put(alt, c);
                if (c < bestCount || (c == bestCount && alt.equals(now))) {
                    bestCount = c;
                    bestAlt = alt;
                }
            }
            if (!bestAlt.equals(now)) {
                Text.Edit e = new Text.Edit(h.start, h.end, bestAlt);
                String prev = src;
                src = e.apply(src);
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("phase", "syntax (type-directed choice)");
                m.put("line", (double) Text.lineOf(src, h.start));
                m.put("diagnostic", h.reason);
                m.put("message", "tied syntax alternatives ranked by attribution errors");
                m.put("rule", "'" + now + "' -> '" + bestAlt + "'");
                m.put("before", lineText(prev, h.start));
                m.put("after", lineText(src, h.start));
                log.add(m);
                for (Hole o : syntaxHoles) {
                    if (o != h) {
                        o.shift(e);
                    }
                }
                h.end = h.start + bestAlt.length();
            }
            final int bc = bestCount;
            h.alternatives.removeIf(a -> counts.getOrDefault(a, Integer.MAX_VALUE) > bc);
            if (h.alternatives.size() < 2) {
                syntaxHoles.remove(h);
            }
        }
        return src;
    }

    private void record(String phase, Javac.Diag d, Fix f, String before, String after, int alternatives) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("phase", phase);
        m.put("line", (double) d.line());
        m.put("diagnostic", d.code());
        m.put("message", d.message().split("\n")[0]);
        m.put("rule", f.rule());
        m.put("before", before);
        m.put("after", after);
        if (alternatives > 1) {
            m.put("tied_alternatives", (double) alternatives);
        }
        log.add(m);
    }

    private static String lineText(String src, int pos) {
        int a = Text.lineStart(src, pos);
        int b = src.indexOf('\n', a);
        return src.substring(a, b < 0 ? src.length() : b).strip();
    }

    // ------------------------------------------------------------------ syntax (see SyntaxRepair)

    private static int adjust(int pos, Text.Edit e) {
        return pos >= e.start() + e.text().length() ? pos - e.delta() : pos;
    }

    /** Indentation-guided brace recovery: find the first line whose indentation says a block should have closed. */
    static List<Fix> braceCandidates(String src) {
        List<Fix> out = new ArrayList<>();
        boolean[] code = Text.codeMask(src);
        String[] lines = src.split("\n", -1);
        int[] starts = new int[lines.length];
        for (int i = 1; i < lines.length; i++) {
            starts[i] = starts[i - 1] + lines[i - 1].length() + 1;
        }
        int unit = 0;
        for (String l : lines) {
            int ind = indent(l);
            if (!l.isBlank() && ind > 0 && (unit == 0 || ind < unit)) {
                unit = ind;
            }
        }
        if (unit == 0) {
            unit = 4;
        }
        int depth = 0;
        String prevSig = "";
        for (int i = 0; i < lines.length; i++) {
            String t = lines[i].strip();
            boolean comment = t.startsWith("//") || t.startsWith("*") || t.startsWith("/*");
            if (!t.isEmpty() && !comment) {
                int ind = indent(lines[i]);
                boolean closes = t.startsWith("}");
                boolean continuation = !(prevSig.isEmpty() || prevSig.endsWith(";") || prevSig.endsWith("{")
                        || prevSig.endsWith("}") || prevSig.endsWith(":") || prevSig.startsWith("@"));
                if (!continuation && ind % unit == 0) {
                    int expected = ind / unit + (closes ? 1 : 0);
                    if (depth > expected) {
                        StringBuilder ins = new StringBuilder();
                        for (int k = depth - 1; k >= expected; k--) {
                            ins.append(" ".repeat(k * unit)).append("}\n");
                        }
                        out.add(new Fix(new Text.Edit(starts[i], starts[i], ins.toString()), 1,
                                "insert missing '}' (indentation)"));
                        return out;
                    }
                }
                for (int c = starts[i]; c < starts[i] + lines[i].length(); c++) {
                    if (code[c]) {
                        if (src.charAt(c) == '{') {
                            depth++;
                        } else if (src.charAt(c) == '}') {
                            depth--;
                        }
                    }
                }
                if (depth < 0 && t.equals("}")) {
                    out.add(new Fix(new Text.Edit(starts[i], starts[i] + lines[i].length() + (i + 1 < lines.length ? 1 : 0), ""), 1,
                            "delete extra '}'"));
                    return out;
                }
                prevSig = t;
            }
        }
        if (depth > 0) {
            StringBuilder ins = new StringBuilder(src.endsWith("\n") ? "" : "\n");
            for (int k = depth - 1; k >= 0; k--) {
                ins.append(" ".repeat(k * unit)).append("}\n");
            }
            out.add(new Fix(new Text.Edit(src.length(), src.length(), ins.toString()), 2, "close blocks at end of file"));
        }
        return out;
    }

    private static int indent(String l) {
        int n = 0;
        for (int i = 0; i < l.length(); i++) {
            char c = l.charAt(i);
            if (c == ' ') {
                n++;
            } else if (c == '\t') {
                n += 4;
            } else {
                break;
            }
        }
        return n;
    }

    // ------------------------------------------------------------------ attribution

    private String attributePhase(String src, Set<String> seen) {
        for (int iter = 0; iter < 80; iter++) {
            Javac.Unit au = Javac.analyze(src);
            List<Javac.Diag> errs = au.errors();
            if (errs.isEmpty()) {
                return src;
            }
            boolean fixed = false;
            for (int k = 0; k < Math.min(5, errs.size()) && !fixed; k++) {
                Javac.Diag d = errs.get(k);
                List<Fix> cands = attributeCandidates(au, d);
                record Tried(Fix f, String src, int count) {
                }
                List<Tried> ok = new ArrayList<>();
                for (Fix f : cands) {
                    String ns = f.edit().apply(src);
                    if (seen.contains(ns)) {
                        continue;
                    }
                    candidatesTried++;
                    Javac.Unit nu = Javac.analyze(ns);
                    List<Javac.Diag> ne = nu.errors();
                    if (Javac.hasErrors(Javac.parse(ns).diags)) {
                        continue;
                    }
                    boolean resolved = true;
                    for (Javac.Diag x : ne) {
                        if (x.code().equals(d.code()) && adjust(x.pos(), f.edit()) == d.pos()) {
                            resolved = false;
                            break;
                        }
                    }
                    if (resolved && ne.size() <= errs.size() + 2) {
                        ok.add(new Tried(f, ns, ne.size()));
                    }
                }
                if (ok.isEmpty()) {
                    continue;
                }
                int minCount = ok.stream().mapToInt(Tried::count).min().getAsInt();
                Tried best = null;
                List<Tried> tied = new ArrayList<>();
                for (Tried t : ok) {
                    if (t.count() == minCount) {
                        tied.add(t);
                        if (best == null || t.f().cost() < best.f().cost()) {
                            best = t;
                        }
                    }
                }
                seen.add(best.src());
                record("types", d, best.f(), lineText(src, best.f().edit().start()), lineText(best.src(), best.f().edit().start()), tied.size());
                shiftAll(holes, best.f().edit());
                if (tied.size() > 1 && best.f().rule().startsWith("value:")) {
                    // hole spans the chosen value inside the inserted text
                    String prefix = best.f().rule().substring("value:".length());
                    int hs = best.f().edit().start() + best.f().edit().text().indexOf(prefix);
                    List<String> alts = new ArrayList<>();
                    for (Tried t : tied) {
                        String r = t.f().rule().substring("value:".length());
                        if (!alts.contains(r)) {
                            alts.add(r);
                        }
                    }
                    holes.add(new Hole(hs, hs + prefix.length(), alts, d.code()));
                }
                src = best.src();
                fixed = true;
            }
            if (!fixed && tokenSteps < Integer.getInteger("jfixpass.typeTokenSteps", 12)) {
                tokenSteps++;
                SyntaxRepair.Step st = new SyntaxRepair().typeStep(src, errs);
                if (st != null && seen.add(st.src())) {
                    log.add(st.log());
                    lossy |= st.lossy();
                    shiftAll(holes, st.edit());
                    if (st.hole() != null) {
                        holes.add(st.hole());
                    }
                    src = st.src();
                    fixed = true;
                }
            }
            if (!fixed) {
                failure = "no type repair found for " + errs.get(0).brief();
                return src;
            }
        }
        failure = "type repair iteration limit";
        return src;
    }

    private static final Pattern SYMBOL = Pattern.compile("symbol:\\s+(class|variable|method|interface|enum)\\s+([\\w$]+)");
    private static final Pattern LOC_TYPE = Pattern.compile("location:\\s+(?:class|interface|enum|record)\\s+([\\w.$\\[\\]<>,]+)");
    private static final Pattern LOC_VAR = Pattern.compile("location:\\s+variable\\s+\\w+\\s+of type\\s+([\\w.$\\[\\]<>,]+)");
    private static final Pattern LOSSY = Pattern.compile("possible lossy conversion from (\\S+) to (\\S+)");
    private static final Pattern CONVERT = Pattern.compile("incompatible types: (\\S+) cannot be converted to (\\S+)");

    List<Fix> attributeCandidates(Javac.Unit u, Javac.Diag d) {
        String src = u.src;
        String msg = d.message();
        List<Fix> out = new ArrayList<>();
        Names names = Names.of(u);
        if (d.code().startsWith("compiler.err.cant.resolve")) {
            Matcher sm = SYMBOL.matcher(msg);
            if (!sm.find()) {
                return out;
            }
            String kind = sm.group(1);
            String name = sm.group(2);
            String locType = null;
            Matcher lm = LOC_VAR.matcher(msg);
            if (lm.find()) {
                locType = lm.group(1);
            } else {
                lm = LOC_TYPE.matcher(msg);
                if (lm.find()) {
                    locType = lm.group(1);
                }
            }
            int[] span = findIdent(src, name, d);
            if (span == null) {
                return out;
            }
            boolean qualified = Text.prevTokenEnd(src, span[0]) > 0 && src.charAt(Text.prevTokenEnd(src, span[0]) - 1) == '.';
            Set<String> pool = new LinkedHashSet<>();
            switch (kind) {
                case "class", "interface", "enum" -> {
                    for (String q : JdkIndex.lookup(name)) {
                        Text.Edit imp = importEdit(src, q);
                        if (imp != null) {
                            out.add(new Fix(imp, 1, "add import " + q));
                        }
                    }
                    pool.addAll(names.types);
                    pool.addAll(JdkIndex.javaLang());
                }
                case "variable" -> {
                    if (!qualified && Character.isUpperCase(name.charAt(0))) {
                        // javac reports an unimported class used as a qualifier (Collections.sort) as a variable
                        for (String q : JdkIndex.lookup(name)) {
                            Text.Edit imp = importEdit(src, q);
                            if (imp != null) {
                                out.add(new Fix(imp, 1, "add import " + q));
                            }
                        }
                        pool.addAll(names.types);
                    }
                    if (locType != null && hasMember(names, locType, name, true)) {
                        out.add(new Fix(new Text.Edit(span[1], span[1], "()"), 1, "field access -> method call " + name + "()"));
                    }
                    if (!qualified) {
                        pool.addAll(names.variables);
                    }
                    if (locType != null) {
                        pool.addAll(members(names, locType, false));
                    }
                }
                case "method" -> {
                    if (locType != null && locType.endsWith("[]") && name.equals("length")) {
                        int p = src.indexOf("()", span[1]);
                        if (p == span[1] || (p > span[1] && src.substring(span[1], p).isBlank())) {
                            out.add(new Fix(new Text.Edit(span[1], p + 2, ""), 1, "array length is a field"));
                        }
                    }
                    if (!qualified && (JdkIndex.javaLang().contains(name) || names.types.contains(name))) {
                        out.add(new Fix(new Text.Edit(span[0], span[0], "new "), 1, "missing 'new' before constructor call"));
                    }
                    if (locType != null) {
                        pool.addAll(members(names, locType, true));
                    }
                    if (!qualified) {
                        pool.addAll(names.methods);
                    }
                }
                default -> {
                }
            }
            int limit = name.length() <= 3 ? 1 : 2;
            List<String> ranked = new ArrayList<>(pool);
            ranked.removeIf(c -> c.equals(name) || Text.distance(c, name) > limit);
            ranked.sort((a, b) -> Integer.compare(Text.distance(a, name), Text.distance(b, name)));
            for (String c : ranked) {
                out.add(new Fix(new Text.Edit(span[0], span[1], c), 1 + Text.distance(c, name), "rename " + name + " -> " + c));
            }
        } else if (d.code().equals("compiler.err.prob.found.req")) {
            String first = msg.split("\n")[0];
            String from = null;
            String to = null;
            Matcher m = LOSSY.matcher(first);
            if (m.find()) {
                from = m.group(1);
                to = m.group(2);
            } else {
                m = CONVERT.matcher(first);
                if (m.find()) {
                    from = m.group(1);
                    to = m.group(2);
                }
            }
            if (to != null && d.start() >= 0 && d.end() > d.start()) {
                String expr = src.substring(d.start(), d.end());
                if (to.equals("boolean")) {
                    int a = d.start();
                    int b = d.end();
                    int eq = loneAssign(src, a, b);
                    if (eq < 0) {
                        eq = loneAssign(src, Text.lineStart(src, d.pos()), lineEnd(src, d.pos()));
                    }
                    if (eq >= 0) {
                        out.add(new Fix(new Text.Edit(eq, eq + 1, "=="), 1, "assignment used as condition: '=' -> '=='"));
                    }
                }
                String simpleTo = to.startsWith("java.lang.") ? to.substring(10) : to;
                boolean primTo = List.of("int", "long", "short", "byte", "char", "float", "double").contains(to);
                if (primTo || (!to.equals("boolean") && !to.contains("<"))) {
                    String wrapped = expr.matches("[\\w.$\\[\\]]+(\\([^()]*\\))?") ? expr : "(" + expr + ")";
                    out.add(new Fix(new Text.Edit(d.start(), d.end(), "(" + simpleTo + ") " + wrapped), 1, "explicit cast to " + simpleTo));
                }
                if (to.equals("java.lang.String") && from != null && !from.contains(".")) {
                    out.add(new Fix(new Text.Edit(d.start(), d.end(), "String.valueOf(" + expr + ")"), 2, "convert to String"));
                }
                if ("java.lang.String".equals(from)) {
                    String parse = switch (to) {
                        case "int" -> "Integer.parseInt";
                        case "long" -> "Long.parseLong";
                        case "double" -> "Double.parseDouble";
                        default -> null;
                    };
                    if (parse != null) {
                        out.add(new Fix(new Text.Edit(d.start(), d.end(), parse + "(" + expr + ")"), 2, "parse String as " + to));
                    }
                }
            }
        } else if (d.code().equals("compiler.err.missing.ret.stmt")) {
            MethodTree m = enclosingMethod(u, d.pos());
            if (m != null && m.getBody() != null) {
                int close = u.end(m.getBody()) - 1;
                if (close > 0 && src.charAt(close) == '}') {
                    int ls = Text.lineStart(src, close);
                    String ind = Text.indentOf(src, close) + "    ";
                    for (String[] v : returnValues(u, m)) {
                        String text = ind + "return " + v[0] + ";\n";
                        if (src.substring(ls, close).isBlank()) {
                            out.add(new Fix(new Text.Edit(ls, ls, text), Integer.parseInt(v[1]), "value:" + v[0]));
                        }
                    }
                }
            }
        } else if (d.code().equals("compiler.err.var.might.not.have.been.initialized")) {
            Matcher vm = Pattern.compile("variable (\\w+) might not").matcher(msg);
            if (vm.find()) {
                String var = vm.group(1);
                Matcher decl = Pattern.compile("\\b(int|long|double|boolean|char|String|float|short|byte)\\s+" + var + "\\s*;").matcher(src);
                int found = -1;
                String ty = null;
                while (decl.find() && decl.start() < d.pos()) {
                    found = decl.end() - 1;
                    ty = decl.group(1);
                }
                if (found > 0) {
                    out.add(new Fix(new Text.Edit(found, found, " = " + defaultValue(ty)), 2, "initialize " + var));
                }
            }
        } else if (d.code().equals("compiler.err.unreported.exception.need.to.catch.or.throw")) {
            Matcher em = Pattern.compile("unreported exception ([\\w.]+)").matcher(msg);
            MethodTree m = enclosingMethod(u, d.pos());
            if (em.find() && m != null && m.getBody() != null && m.getThrows().isEmpty() && m.getName().contentEquals("main")) {
                int b = u.start(m.getBody());
                String ex = em.group(1);
                String simple = ex.substring(ex.lastIndexOf('.') + 1);
                out.add(new Fix(new Text.Edit(b, b, "throws " + simple + " "), 2, "declare throws " + simple + " on main"));
            }
        }
        return out;
    }

    private static int lineEnd(String s, int pos) {
        int e = s.indexOf('\n', pos);
        return e < 0 ? s.length() : e;
    }

    /** Position of a single '=' (assignment) in [a,b) outside literals, or -1. */
    private static int loneAssign(String src, int a, int b) {
        boolean[] code = Text.codeMask(src);
        for (int i = Math.max(1, a); i < Math.min(b, src.length() - 1); i++) {
            if (code[i] && src.charAt(i) == '=' && "=!<>+-*/%&|^".indexOf(src.charAt(i - 1)) < 0 && src.charAt(i + 1) != '=') {
                return i;
            }
        }
        return -1;
    }

    private static int[] findIdent(String src, String name, Javac.Diag d) {
        boolean[] code = Text.codeMask(src);
        Matcher m = Pattern.compile("(?<![\\w$])" + Pattern.quote(name) + "(?![\\w$])").matcher(src);
        int[] best = null;
        int bestDist = Integer.MAX_VALUE;
        while (m.find()) {
            if (!code[m.start()]) {
                continue;
            }
            int dist = Math.abs(m.start() - d.pos());
            if (d.start() >= 0 && m.start() >= d.start() && m.end() <= Math.max(d.end(), d.pos() + name.length())) {
                dist = 0;
            }
            if (dist < bestDist) {
                bestDist = dist;
                best = new int[] {m.start(), m.end()};
            }
        }
        return best;
    }

    /** Insert "import q;" keeping imports sorted. */
    static Text.Edit importEdit(String src, String q) {
        Matcher m = Pattern.compile("(?m)^import\\s+(static\\s+)?([\\w.]+)\\s*;[ \\t]*\\n?").matcher(src);
        int insertAt = -1;
        int lastEnd = -1;
        while (m.find()) {
            if (m.group(2).equals(q)) {
                return null;
            }
            if (insertAt < 0 && m.group(1) == null && m.group(2).compareTo(q) > 0) {
                insertAt = m.start();
            }
            lastEnd = m.end();
        }
        String line = "import " + q + ";\n";
        if (insertAt >= 0) {
            return new Text.Edit(insertAt, insertAt, line);
        }
        if (lastEnd >= 0) {
            return new Text.Edit(lastEnd, lastEnd, line);
        }
        Matcher pk = Pattern.compile("(?m)^package\\s+[\\w.]+\\s*;\\s*\\n").matcher(src);
        int at = pk.find() ? pk.end() : 0;
        return new Text.Edit(at, at, line + "\n");
    }

    private static MethodTree enclosingMethod(Javac.Unit u, int pos) {
        MethodTree[] found = new MethodTree[1];
        new TreeScanner<Void, Void>() {
            @Override
            public Void visitMethod(MethodTree m, Void p) {
                if (u.start(m) <= pos && pos < u.end(m)) {
                    found[0] = m;
                }
                return super.visitMethod(m, p);
            }
        }.scan(u.tree, null);
        return found[0];
    }

    /** Candidate return values: [text, cost]. */
    private static List<String[]> returnValues(Javac.Unit u, MethodTree m) {
        String rt = m.getReturnType() == null ? "void" : u.text(m.getReturnType());
        Map<String, Integer> vals = new LinkedHashMap<>();
        List<String> rets = new ArrayList<>();
        new TreeScanner<Void, Void>() {
            @Override
            public Void visitReturn(ReturnTree r, Void p) {
                if (r.getExpression() != null) {
                    rets.add(u.text(r.getExpression()));
                }
                return super.visitReturn(r, p);
            }

            @Override
            public Void visitVariable(VariableTree v, Void p) {
                if (v.getType() != null && u.text(v.getType()).equals(rt)) {
                    vals.putIfAbsent(v.getName().toString(), 1);
                }
                return super.visitVariable(v, p);
            }
        }.scan(m.getBody(), null);
        for (VariableTree v : m.getParameters()) {
            if (u.text(v.getType()).equals(rt)) {
                vals.putIfAbsent(v.getName().toString(), 1);
            }
        }
        for (int i = rets.size() - 1; i >= 0; i--) {
            vals.putIfAbsent(rets.get(i), 1);
        }
        Tree.Kind litKind = switch (rt) {
            case "String" -> Tree.Kind.STRING_LITERAL;
            case "int", "long", "short", "byte" -> Tree.Kind.INT_LITERAL;
            case "char" -> Tree.Kind.CHAR_LITERAL;
            case "double", "float" -> Tree.Kind.DOUBLE_LITERAL;
            default -> null;
        };
        if (litKind != null) {
            new TreeScanner<Void, Void>() {
                @Override
                public Void visitLiteral(LiteralTree l, Void p) {
                    if (l.getKind() == litKind) {
                        vals.putIfAbsent(u.text(l), 2);
                    }
                    return null;
                }
            }.scan(u.tree, null);
        }
        if (rt.equals("boolean")) {
            vals.putIfAbsent("false", 2);
            vals.putIfAbsent("true", 2);
        }
        vals.putIfAbsent(defaultValue(rt), 3);
        List<String[]> out = new ArrayList<>();
        vals.forEach((k, v) -> out.add(new String[] {k, String.valueOf(v)}));
        return out;
    }

    static String defaultValue(String t) {
        return switch (t) {
            case "int", "short", "byte" -> "0";
            case "long" -> "0L";
            case "double" -> "0.0";
            case "float" -> "0.0f";
            case "boolean" -> "false";
            case "char" -> "'\\0'";
            default -> "null";
        };
    }

    // ------------------------------------------------------------------ names and members

    /** Names declared or imported in the compilation unit. */
    static final class Names {
        final Set<String> types = new LinkedHashSet<>();
        final Set<String> variables = new LinkedHashSet<>();
        final Set<String> methods = new LinkedHashSet<>();
        final Map<String, Set<String>> classMethods = new TreeMap<>();
        final Map<String, Set<String>> classFields = new TreeMap<>();
        final Map<String, String> imports = new TreeMap<>();

        static Names of(Javac.Unit u) {
            Names n = new Names();
            if (u.tree == null) {
                return n;
            }
            for (ImportTree it : u.tree.getImports()) {
                String q = it.getQualifiedIdentifier().toString();
                String simple = q.substring(q.lastIndexOf('.') + 1);
                if (!simple.equals("*")) {
                    n.types.add(simple);
                    n.imports.put(simple, q);
                }
            }
            new TreeScanner<Void, String>() {
                @Override
                public Void visitClass(ClassTree c, String owner) {
                    String name = c.getSimpleName().toString();
                    n.types.add(name);
                    n.classMethods.computeIfAbsent(name, k -> new LinkedHashSet<>());
                    n.classFields.computeIfAbsent(name, k -> new LinkedHashSet<>());
                    for (Tree mem : c.getMembers()) {
                        if (mem instanceof MethodTree mt) {
                            n.classMethods.get(name).add(mt.getName().toString());
                        } else if (mem instanceof VariableTree vt) {
                            n.classFields.get(name).add(vt.getName().toString());
                        }
                    }
                    return super.visitClass(c, name);
                }

                @Override
                public Void visitMethod(MethodTree m, String owner) {
                    if (!m.getName().contentEquals("<init>")) {
                        n.methods.add(m.getName().toString());
                    }
                    return super.visitMethod(m, owner);
                }

                @Override
                public Void visitVariable(VariableTree v, String owner) {
                    n.variables.add(v.getName().toString());
                    return super.visitVariable(v, owner);
                }
            }.scan(u.tree, null);
            return n;
        }
    }

    private static String eraseType(String t) {
        int lt = t.indexOf('<');
        return lt < 0 ? t : t.substring(0, lt);
    }

    private static Class<?> resolveClass(Names names, String type) {
        String t = eraseType(type);
        List<String> tries = new ArrayList<>(List.of(t, "java.lang." + t));
        if (names.imports.containsKey(t)) {
            tries.add(0, names.imports.get(t));
        }
        for (String q : tries) {
            try {
                return Class.forName(q, false, ClassLoader.getPlatformClassLoader());
            } catch (ClassNotFoundException | LinkageError e) {
                // try next
            }
        }
        return null;
    }

    static Set<String> members(Names names, String type, boolean methods) {
        Set<String> out = new LinkedHashSet<>();
        String t = eraseType(type);
        if (t.endsWith("[]")) {
            if (!methods) {
                out.add("length");
            } else {
                out.add("clone");
            }
            return out;
        }
        String simple = t.substring(t.lastIndexOf('.') + 1);
        if (names.classMethods.containsKey(simple)) {
            out.addAll(methods ? names.classMethods.get(simple) : names.classFields.get(simple));
            return out;
        }
        Class<?> c = resolveClass(names, t);
        if (c != null) {
            if (methods) {
                for (Method m : c.getMethods()) {
                    out.add(m.getName());
                }
            } else {
                for (Field f : c.getFields()) {
                    out.add(f.getName());
                }
            }
        }
        return out;
    }

    private static boolean hasMember(Names names, String type, String name, boolean methods) {
        if (!methods) {
            return members(names, type, false).contains(name);
        }
        Class<?> c = resolveClass(names, type);
        if (c != null) {
            for (Method m : c.getMethods()) {
                if (m.getName().equals(name) && m.getParameterCount() == 0) {
                    return true;
                }
            }
            return false;
        }
        return members(names, type, true).contains(name);
    }

    static List<String> signatures(Javac.Unit u) {
        List<String> out = new ArrayList<>();
        if (u.tree == null) {
            return out;
        }
        new TreeScanner<Void, String>() {
            @Override
            public Void visitClass(ClassTree c, String owner) {
                return super.visitClass(c, (owner == null ? "" : owner + ".") + c.getSimpleName());
            }

            @Override
            public Void visitMethod(MethodTree m, String owner) {
                StringBuilder sb = new StringBuilder(owner + "." + m.getName() + "(");
                for (int i = 0; i < m.getParameters().size(); i++) {
                    sb.append(i == 0 ? "" : ",").append(u.text(m.getParameters().get(i).getType()).replaceAll("\\s+", ""));
                }
                sb.append(")").append(m.getReturnType() == null ? "" : ":" + u.text(m.getReturnType()).replaceAll("\\s+", ""));
                out.add(sb.toString());
                return null;
            }
        }.scan(u.tree, null);
        return out;
    }

    /** Public top-level classes of selected java.base packages, by simple name. */
    static final class JdkIndex {
        private static final String[] PACKAGES = {"java.util", "java.io", "java.math", "java.util.function", "java.util.stream",
            "java.util.regex", "java.text", "java.time"};
        private static Map<String, List<String>> index;
        private static Set<String> lang;

        static synchronized void load() {
            if (index != null) {
                return;
            }
            index = new TreeMap<>();
            lang = new LinkedHashSet<>();
            try {
                FileSystem jrt = FileSystems.getFileSystem(URI.create("jrt:/"));
                for (String pkg : PACKAGES) {
                    scan(jrt, pkg, (s, q) -> index.computeIfAbsent(s, k -> new ArrayList<>()).add(q));
                }
                scan(jrt, "java.lang", (s, q) -> lang.add(s));
            } catch (IOException | RuntimeException e) {
                // index unavailable: import repair disabled
            }
        }

        private static void scan(FileSystem jrt, String pkg, java.util.function.BiConsumer<String, String> sink) throws IOException {
            Path dir = jrt.getPath("/modules/java.base/" + pkg.replace('.', '/'));
            if (!Files.isDirectory(dir)) {
                return;
            }
            try (DirectoryStream<Path> ds = Files.newDirectoryStream(dir, "*.class")) {
                List<String> names = new ArrayList<>();
                for (Path p : ds) {
                    String f = p.getFileName().toString();
                    if (!f.contains("$") && !f.equals("module-info.class") && !f.equals("package-info.class")) {
                        names.add(f.substring(0, f.length() - 6));
                    }
                }
                names.sort(null);
                for (String s : names) {
                    try {
                        Class<?> c = Class.forName(pkg + "." + s, false, ClassLoader.getPlatformClassLoader());
                        if (java.lang.reflect.Modifier.isPublic(c.getModifiers())) {
                            sink.accept(s, pkg + "." + s);
                        }
                    } catch (ClassNotFoundException | LinkageError e) {
                        // skip
                    }
                }
            }
        }

        static List<String> lookup(String simple) {
            load();
            return index.getOrDefault(simple, List.of());
        }

        static Set<String> javaLang() {
            load();
            return lang;
        }
    }
}
