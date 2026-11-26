package jfixpass;

import com.sun.source.tree.BlockTree;
import com.sun.source.tree.CaseTree;
import com.sun.source.tree.ExpressionStatementTree;
import com.sun.source.tree.IdentifierTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.StatementTree;
import com.sun.source.util.TreeScanner;
import java.util.ArrayList;
import java.util.List;

/** Statement-level coverage by source instrumentation, and Ochiai suspiciousness. */
final class Coverage {
    record Stmt(int id, int start, int end) {
    }

    final List<Stmt> stmts = new ArrayList<>();
    final String instrumented;

    private Coverage(Javac.Unit u) {
        List<Text.Edit> ins = new ArrayList<>();
        new TreeScanner<Void, Void>() {
            private void add(StatementTree s) {
                if (isCtorCall(s)) {
                    return;
                }
                int st = u.start(s);
                int en = u.end(s);
                if (st < 0 || en <= st) {
                    return;
                }
                int id = stmts.size();
                stmts.add(new Stmt(id, st, en));
                ins.add(new Text.Edit(st, st, "__JCov.h(" + id + "); "));
            }

            @Override
            public Void visitBlock(BlockTree b, Void p) {
                for (StatementTree s : b.getStatements()) {
                    add(s);
                }
                return super.visitBlock(b, p);
            }

            @Override
            public Void visitCase(CaseTree c, Void p) {
                if (c.getStatements() != null && c.getCaseKind() == CaseTree.CaseKind.STATEMENT) {
                    for (StatementTree s : c.getStatements()) {
                        add(s);
                    }
                }
                return super.visitCase(c, p);
            }
        }.scan(u.tree, null);
        String body = Text.apply(u.src, ins);
        instrumented = body + "\nfinal class __JCov {\n    static final boolean[] H = new boolean[" + Math.max(1, stmts.size())
                + "];\n    static void h(int i) {\n        H[i] = true;\n    }\n}\n";
    }

    private static boolean isCtorCall(StatementTree s) {
        if (s instanceof ExpressionStatementTree es && es.getExpression() instanceof MethodInvocationTree mi
                && mi.getMethodSelect() instanceof IdentifierTree id) {
            String n = id.getName().toString();
            return n.equals("this") || n.equals("super");
        }
        return false;
    }

    static Coverage instrument(Javac.Unit parsed) {
        return new Coverage(parsed);
    }

    /** Ochiai score per statement: ef / sqrt(F * (ef + ep)). */
    double[] ochiai(List<int[]> hitsPerTest, List<Boolean> passed) {
        int n = stmts.size();
        int[] ef = new int[n];
        int[] ep = new int[n];
        int totalFailed = 0;
        for (int t = 0; t < hitsPerTest.size(); t++) {
            boolean ok = passed.get(t);
            if (!ok) {
                totalFailed++;
            }
            for (int h : hitsPerTest.get(t)) {
                if (h >= 0 && h < n) {
                    if (ok) {
                        ep[h]++;
                    } else {
                        ef[h]++;
                    }
                }
            }
        }
        double[] s = new double[n];
        for (int i = 0; i < n; i++) {
            if (ef[i] > 0 && totalFailed > 0) {
                s[i] = ef[i] / Math.sqrt((double) totalFailed * (ef[i] + ep[i]));
            }
        }
        return s;
    }

    /** Innermost instrumented statement containing pos, or -1. */
    int statementAt(int pos) {
        int best = -1;
        int bestLen = Integer.MAX_VALUE;
        for (Stmt s : stmts) {
            if (s.start() <= pos && pos < s.end() && s.end() - s.start() < bestLen) {
                best = s.id();
                bestLen = s.end() - s.start();
            }
        }
        return best;
    }
}
