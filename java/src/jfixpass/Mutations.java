package jfixpass;

import com.sun.source.tree.ArrayAccessTree;
import com.sun.source.tree.AssignmentTree;
import com.sun.source.tree.BinaryTree;
import com.sun.source.tree.CompoundAssignmentTree;
import com.sun.source.tree.ConditionalExpressionTree;
import com.sun.source.tree.ExpressionStatementTree;
import com.sun.source.tree.ExpressionTree;
import com.sun.source.tree.IdentifierTree;
import com.sun.source.tree.LiteralTree;
import com.sun.source.tree.MemberSelectTree;
import com.sun.source.tree.MethodInvocationTree;
import com.sun.source.tree.MethodTree;
import com.sun.source.tree.ParenthesizedTree;
import com.sun.source.tree.Tree;
import com.sun.source.tree.UnaryTree;
import com.sun.source.tree.VariableTree;
import com.sun.source.util.TreePath;
import com.sun.source.util.TreePathScanner;
import java.util.ArrayList;
import java.util.EnumMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.lang.model.element.Element;
import javax.lang.model.element.ElementKind;
import javax.lang.model.type.TypeKind;
import javax.lang.model.type.TypeMirror;
import javax.lang.model.util.Types;

/** Typed, AST-anchored program edits used by the test-driven search. */
final class Mutations {
    record Cand(Text.Edit edit, int cost, String op, int anchor) {
    }

    private static final Map<Tree.Kind, String> OPS = new EnumMap<>(Tree.Kind.class);
    private static final List<Tree.Kind> REL = List.of(Tree.Kind.LESS_THAN, Tree.Kind.LESS_THAN_EQUAL, Tree.Kind.GREATER_THAN,
            Tree.Kind.GREATER_THAN_EQUAL, Tree.Kind.EQUAL_TO, Tree.Kind.NOT_EQUAL_TO);
    private static final List<Tree.Kind> ARITH = List.of(Tree.Kind.PLUS, Tree.Kind.MINUS, Tree.Kind.MULTIPLY, Tree.Kind.DIVIDE,
            Tree.Kind.REMAINDER);

    static {
        OPS.put(Tree.Kind.LESS_THAN, "<");
        OPS.put(Tree.Kind.LESS_THAN_EQUAL, "<=");
        OPS.put(Tree.Kind.GREATER_THAN, ">");
        OPS.put(Tree.Kind.GREATER_THAN_EQUAL, ">=");
        OPS.put(Tree.Kind.EQUAL_TO, "==");
        OPS.put(Tree.Kind.NOT_EQUAL_TO, "!=");
        OPS.put(Tree.Kind.PLUS, "+");
        OPS.put(Tree.Kind.MINUS, "-");
        OPS.put(Tree.Kind.MULTIPLY, "*");
        OPS.put(Tree.Kind.DIVIDE, "/");
        OPS.put(Tree.Kind.REMAINDER, "%");
        OPS.put(Tree.Kind.CONDITIONAL_AND, "&&");
        OPS.put(Tree.Kind.CONDITIONAL_OR, "||");
    }

    private record Var(String name, TypeMirror type, int declStart) {
    }

    static List<Cand> generate(Javac.Unit u) {
        List<Cand> out = new ArrayList<>();
        Types types = u.task.getTypes();
        String src = u.src;
        new TreePathScanner<Void, Void>() {
            List<Var> vars = new ArrayList<>();
            Set<String> charLits = new LinkedHashSet<>();

            TypeMirror type(Tree child) {
                try {
                    return u.trees.getTypeMirror(new TreePath(getCurrentPath(), child));
                } catch (RuntimeException e) {
                    return null;
                }
            }

            boolean numeric(TypeMirror t) {
                if (t == null) {
                    return false;
                }
                TypeKind k = t.getKind();
                if (k.isPrimitive()) {
                    return k != TypeKind.BOOLEAN;
                }
                try {
                    return types.unboxedType(t).getKind() != TypeKind.BOOLEAN;
                } catch (RuntimeException e) {
                    return false;
                }
            }

            boolean integral(TypeMirror t) {
                if (!numeric(t)) {
                    return false;
                }
                TypeKind k = t.getKind().isPrimitive() ? t.getKind() : types.unboxedType(t).getKind();
                return k == TypeKind.INT || k == TypeKind.LONG || k == TypeKind.SHORT || k == TypeKind.BYTE || k == TypeKind.CHAR;
            }

            void add(int s, int e, String text, int cost, String op, int anchor) {
                if (s >= 0 && e >= s && e <= src.length() && !src.substring(s, e).equals(text)) {
                    out.add(new Cand(new Text.Edit(s, e, text), cost, op, anchor));
                }
            }

            int tokenBetween(String tok, int from, int to) {
                if (from < 0 || to < 0) {
                    return -1;
                }
                int p = src.indexOf(tok, from);
                return p >= 0 && p + tok.length() <= to ? p : -1;
            }

            @Override
            public Void visitMethod(MethodTree m, Void p) {
                List<Var> saved = vars;
                Set<String> savedChars = charLits;
                vars = new ArrayList<>();
                charLits = new LinkedHashSet<>();
                new TreePathScanner<Void, Void>() {
                    @Override
                    public Void visitVariable(VariableTree v, Void q) {
                        Element el = u.trees.getElement(getCurrentPath());
                        if (el != null && (el.getKind() == ElementKind.LOCAL_VARIABLE || el.getKind() == ElementKind.PARAMETER)) {
                            vars.add(new Var(v.getName().toString(), el.asType(), u.start(v)));
                        }
                        return super.visitVariable(v, q);
                    }

                    @Override
                    public Void visitLiteral(LiteralTree l, Void q) {
                        if (l.getKind() == Tree.Kind.CHAR_LITERAL) {
                            charLits.add(u.text(l));
                        }
                        return null;
                    }
                }.scan(getCurrentPath(), null);
                super.visitMethod(m, p);
                vars = saved;
                charLits = savedChars;
                return null;
            }

            void plusMinusOne(ExpressionTree e) {
                Tree.Kind k = e.getKind();
                if (k != Tree.Kind.IDENTIFIER && k != Tree.Kind.MEMBER_SELECT && k != Tree.Kind.METHOD_INVOCATION
                        && k != Tree.Kind.ARRAY_ACCESS) {
                    return;
                }
                TypeMirror t = type(e);
                if (t == null || t.getKind() != TypeKind.INT) {
                    return;
                }
                String txt = u.text(e);
                add(u.start(e), u.end(e), "(" + txt + " + 1)", 2, "expr+1", u.start(e));
                add(u.start(e), u.end(e), "(" + txt + " - 1)", 2, "expr-1", u.start(e));
            }

            @Override
            public Void visitBinary(BinaryTree b, Void p) {
                Tree.Kind k = b.getKind();
                String op = OPS.get(k);
                TypeMirror lt = type(b.getLeftOperand());
                TypeMirror rt = type(b.getRightOperand());
                int le = u.end(b.getLeftOperand());
                int rs = u.start(b.getRightOperand());
                int opPos = op == null ? -1 : tokenBetween(op, le, rs);
                if (opPos >= 0) {
                    List<Tree.Kind> group = null;
                    if (REL.contains(k)) {
                        group = numeric(lt) && numeric(rt) ? REL
                                : List.of(Tree.Kind.EQUAL_TO, Tree.Kind.NOT_EQUAL_TO).contains(k)
                                        ? List.of(Tree.Kind.EQUAL_TO, Tree.Kind.NOT_EQUAL_TO) : null;
                    } else if (ARITH.contains(k) && numeric(lt) && numeric(rt)) {
                        group = ARITH;
                    } else if (k == Tree.Kind.CONDITIONAL_AND || k == Tree.Kind.CONDITIONAL_OR) {
                        group = List.of(Tree.Kind.CONDITIONAL_AND, Tree.Kind.CONDITIONAL_OR);
                    }
                    if (group != null) {
                        for (Tree.Kind g : group) {
                            if (g != k) {
                                add(opPos, opPos + op.length(), OPS.get(g), 1, "operator " + op + "->" + OPS.get(g), opPos);
                            }
                        }
                    }
                }
                if (k == Tree.Kind.DIVIDE && integral(lt) && integral(rt)) {
                    int s = u.start(b.getLeftOperand());
                    add(s, s, "(double) ", 2, "floating division", s);
                }
                if ((k == Tree.Kind.PLUS || k == Tree.Kind.MINUS) && numeric(lt)
                        && b.getRightOperand().getKind() == Tree.Kind.INT_LITERAL) {
                    add(u.start(b), u.end(b), u.text(b.getLeftOperand()), 2, "drop constant offset", u.start(b));
                }
                if (REL.contains(k) || ARITH.contains(k)) {
                    plusMinusOne(b.getLeftOperand());
                    plusMinusOne(b.getRightOperand());
                }
                return super.visitBinary(b, p);
            }

            @Override
            public Void visitArrayAccess(ArrayAccessTree a, Void p) {
                plusMinusOne(a.getIndex());
                return super.visitArrayAccess(a, p);
            }

            @Override
            public Void visitLiteral(LiteralTree l, Void p) {
                int s = u.start(l);
                int e = u.end(l);
                Object v = l.getValue();
                switch (l.getKind()) {
                    case INT_LITERAL -> {
                        int x = (Integer) v;
                        add(s, e, String.valueOf(x + 1), 1, "literal+1", s);
                        if (x > 0) {
                            add(s, e, String.valueOf(x - 1), 1, "literal-1", s);
                        }
                    }
                    case LONG_LITERAL -> {
                        long x = (Long) v;
                        add(s, e, (x + 1) + "L", 1, "literal+1", s);
                        if (x > 0) {
                            add(s, e, (x - 1) + "L", 1, "literal-1", s);
                        }
                    }
                    case BOOLEAN_LITERAL -> add(s, e, String.valueOf(!(Boolean) v), 1, "boolean flip", s);
                    case CHAR_LITERAL -> {
                        char c = (Character) v;
                        Set<String> alts = new LinkedHashSet<>(charLits);
                        if (Character.isLetter(c)) {
                            char sw = Character.isUpperCase(c) ? Character.toLowerCase(c) : Character.toUpperCase(c);
                            alts.add("'" + sw + "'");
                        }
                        for (String a : alts) {
                            add(s, e, a, 1, "char literal", s);
                        }
                    }
                    default -> {
                    }
                }
                return super.visitLiteral(l, p);
            }

            @Override
            public Void visitUnary(UnaryTree t, Void p) {
                int s = u.start(t);
                int e = u.end(t);
                switch (t.getKind()) {
                    case LOGICAL_COMPLEMENT -> add(s, e, u.text(t.getExpression()), 1, "remove negation", s);
                    case POSTFIX_INCREMENT, PREFIX_INCREMENT -> {
                        int at = src.indexOf("++", s);
                        if (at >= 0 && at < e) {
                            add(at, at + 2, "--", 1, "++ -> --", s);
                        }
                    }
                    case POSTFIX_DECREMENT, PREFIX_DECREMENT -> {
                        int at = src.indexOf("--", s);
                        if (at >= 0 && at < e) {
                            add(at, at + 2, "++", 1, "-- -> ++", s);
                        }
                    }
                    default -> {
                    }
                }
                return super.visitUnary(t, p);
            }

            @Override
            public Void visitCompoundAssignment(CompoundAssignmentTree c, Void p) {
                String[][] alts = {{"+=", "-=", "="}, {"-=", "+=", "="}, {"*=", "/="}, {"/=", "*="}};
                int from = u.end(c.getVariable());
                int to = u.start(c.getExpression());
                for (String[] a : alts) {
                    int at = tokenBetween(a[0], from, to);
                    if (at >= 0) {
                        for (int i = 1; i < a.length; i++) {
                            add(at, at + 2, a[i], 1, "assign " + a[0] + "->" + a[i], u.start(c));
                        }
                        break;
                    }
                }
                return super.visitCompoundAssignment(c, p);
            }

            @Override
            public Void visitAssignment(AssignmentTree a, Void p) {
                Tree parent = getCurrentPath().getParentPath().getLeaf();
                if (parent instanceof ExpressionStatementTree && numeric(type(a.getVariable()))) {
                    int at = tokenBetween("=", u.end(a.getVariable()), u.start(a.getExpression()));
                    if (at >= 0) {
                        add(at, at + 1, "+=", 2, "assign = -> +=", u.start(a));
                        add(at, at + 1, "-=", 2, "assign = -> -=", u.start(a));
                    }
                }
                return super.visitAssignment(a, p);
            }

            @Override
            public Void visitConditionalExpression(ConditionalExpressionTree c, Void p) {
                int ts = u.start(c.getTrueExpression());
                int te = u.end(c.getTrueExpression());
                int fs = u.start(c.getFalseExpression());
                int fe = u.end(c.getFalseExpression());
                if (ts >= 0 && fe > ts) {
                    add(ts, fe, src.substring(fs, fe) + src.substring(te, fs) + src.substring(ts, te), 2, "swap ternary branches", ts);
                }
                return super.visitConditionalExpression(c, p);
            }

            @Override
            public Void visitMethodInvocation(MethodInvocationTree mi, Void p) {
                List<? extends ExpressionTree> args = mi.getArguments();
                for (int i = 0; i < args.size(); i++) {
                    for (int j = i + 1; j < args.size(); j++) {
                        TypeMirror ti = type(args.get(i));
                        TypeMirror tj = type(args.get(j));
                        if (ti != null && tj != null && types.isSameType(ti, tj)) {
                            int si = u.start(args.get(i));
                            int ei = u.end(args.get(i));
                            int sj = u.start(args.get(j));
                            int ej = u.end(args.get(j));
                            add(si, ej, src.substring(sj, ej) + src.substring(ei, sj) + src.substring(si, ei), 2, "swap arguments", si);
                        }
                    }
                }
                if (mi.getMethodSelect() instanceof MemberSelectTree ms && u.text(ms.getExpression()).equals("Math")) {
                    String n = ms.getIdentifier().toString();
                    String alt = switch (n) {
                        case "min" -> "max";
                        case "max" -> "min";
                        case "floor" -> "ceil";
                        case "ceil" -> "floor";
                        default -> null;
                    };
                    int e = u.end(ms);
                    if (alt != null && e >= n.length()) {
                        add(e - n.length(), e, alt, 1, "Math." + n + "->" + alt, u.start(mi));
                    }
                }
                return super.visitMethodInvocation(mi, p);
            }

            @Override
            public Void visitIdentifier(IdentifierTree id, Void p) {
                Element el = u.trees.getElement(getCurrentPath());
                if (el != null && (el.getKind() == ElementKind.LOCAL_VARIABLE || el.getKind() == ElementKind.PARAMETER)) {
                    int s = u.start(id);
                    String name = id.getName().toString();
                    for (Var v : vars) {
                        if (!v.name().equals(name) && v.declStart() < s && types.isSameType(v.type(), el.asType())) {
                            add(s, u.end(id), v.name(), 2, "variable " + name + "->" + v.name(), s);
                        }
                    }
                }
                return super.visitIdentifier(id, p);
            }

            @Override
            public Void visitParenthesized(ParenthesizedTree t, Void p) {
                return super.visitParenthesized(t, p);
            }
        }.scan(u.tree, null);
        // dedupe identical edits, keeping the cheapest
        Map<String, Cand> uniq = new java.util.LinkedHashMap<>();
        for (Cand c : out) {
            String key = c.edit().start() + ":" + c.edit().end() + ":" + c.edit().text();
            Cand prev = uniq.get(key);
            if (prev == null || c.cost() < prev.cost()) {
                uniq.put(key, c);
            }
        }
        return new ArrayList<>(uniq.values());
    }
}
