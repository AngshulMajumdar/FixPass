package jfixpass;

import com.sun.source.tree.BinaryTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import java.util.ArrayList;
import java.util.List;
import javax.lang.model.type.TypeMirror;

/** Semantic checks javac accepts but that are almost always bugs. */
final class StaticRules {
    record Finding(Text.Edit edit, String rule, int line) {
    }

    /** String compared with == / != : rewrite to null-safe equals. */
    static List<Finding> stringEquality(Javac.Unit u) {
        List<Finding> out = new ArrayList<>();
        new TreePathScanner<Void, Void>() {
            boolean isString(ExpressionTree e) {
                try {
                    TypeMirror t = u.trees.getTypeMirror(new TreePath(getCurrentPath(), e));
                    return t != null && t.toString().equals("java.lang.String");
                } catch (RuntimeException ex) {
                    return false;
                }
            }

            @Override
            public Void visitBinary(BinaryTree b, Void p) {
                boolean eq = b.getKind() == Tree.Kind.EQUAL_TO;
                if ((eq || b.getKind() == Tree.Kind.NOT_EQUAL_TO) && isString(b.getLeftOperand()) && isString(b.getRightOperand())) {
                    ExpressionTree l = b.getLeftOperand();
                    ExpressionTree r = b.getRightOperand();
                    String call;
                    if (r.getKind() == Tree.Kind.STRING_LITERAL) {
                        call = u.text(r) + ".equals(" + u.text(l) + ")";
                    } else if (l.getKind() == Tree.Kind.STRING_LITERAL) {
                        call = u.text(l) + ".equals(" + u.text(r) + ")";
                    } else {
                        call = "java.util.Objects.equals(" + u.text(l) + ", " + u.text(r) + ")";
                    }
                    out.add(new Finding(new Text.Edit(u.start(b), u.end(b), (eq ? "" : "!") + call),
                            "String compared by reference", Text.lineOf(u.src, u.start(b))));
                }
                return super.visitBinary(b, p);
            }
        }.scan(u.tree, null);
        return out;
    }
}
