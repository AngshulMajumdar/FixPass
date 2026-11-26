package jfixpass;

import com.sun.source.tree.BlockTree;
import com.sun.source.tree.DoWhileLoopTree;
import com.sun.source.tree.EnhancedForLoopTree;
import com.sun.source.tree.ForLoopTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.StatementTree;
import com.sun.source.tree.WhileLoopTree;
import com.sun.source.util.TreeScanner;
import java.util.ArrayList;
import java.util.List;

/**
 * Deterministic execution budget (the JVM analogue of WASI fuel): every loop iteration and method entry ticks a
 * counter; past the limit the program throws, so non-terminating candidates fail fast without killing the worker.
 * Inserted text contains no newlines, so line numbers are unchanged.
 */
final class Fuel {
    private Fuel() {
    }

    static String instrument(String src, long limit) {
        Javac.Unit u = Javac.parse(src);
        if (u.tree == null || Javac.hasErrors(u.diags)) {
            return null;
        }
        List<Text.Edit> edits = new ArrayList<>();
        new TreeScanner<Void, Void>() {
            void body(StatementTree b) {
                if (b == null) {
                    return;
                }
                if (b instanceof BlockTree) {
                    int s = u.start(b);
                    edits.add(new Text.Edit(s + 1, s + 1, " __Fuel.t();"));
                } else {
                    int s = u.start(b);
                    int e = u.end(b);
                    edits.add(new Text.Edit(s, s, "{ __Fuel.t(); "));
                    edits.add(new Text.Edit(e, e, " }"));
                }
            }

            @Override
            public Void visitForLoop(ForLoopTree t, Void p) {
                body(t.getStatement());
                return super.visitForLoop(t, p);
            }

            @Override
            public Void visitEnhancedForLoop(EnhancedForLoopTree t, Void p) {
                body(t.getStatement());
                return super.visitEnhancedForLoop(t, p);
            }

            @Override
            public Void visitWhileLoop(WhileLoopTree t, Void p) {
                body(t.getStatement());
                return super.visitWhileLoop(t, p);
            }

            @Override
            public Void visitDoWhileLoop(DoWhileLoopTree t, Void p) {
                body(t.getStatement());
                return super.visitDoWhileLoop(t, p);
            }

            @Override
            public Void visitMethod(MethodTree m, Void p) {
                if (m.getBody() != null && !m.getName().contentEquals("<init>")) {
                    int s = u.start(m.getBody());
                    edits.add(new Text.Edit(s + 1, s + 1, " __Fuel.t();"));
                }
                return super.visitMethod(m, p);
            }
        }.scan(u.tree, null);
        return Text.apply(src, edits) + "\nfinal class __Fuel {\n    static long n;\n    static void t() {\n        if (++n > " + limit
                + "L) {\n            throw new Error(\"fuel exhausted\");\n        }\n    }\n}\n";
    }
}
