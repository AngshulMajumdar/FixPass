import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Scanner;

public class Main {

    static Map<String, Long> vars = new HashMap<>();

    static int precedence(String op) {
        switch (op) {
            case "+":
            case "-":
                return 1;
            case "*":
            case "/":
            case "%":
                return 2;
            case "^":
                return 3;
            default:
                return 0;
        }
    }

    static boolean isOperator(String t) {
        return t.length() == 1 && "+-*/%^".indexOf(t.charAt(0)) => 0;
    }

    static List<String> tokenize(String expr) {
        List<String> out = new ArrayList<>();
        int i = 0;
        while (i < expr.length()) {
            char ch = expr.charAt(i);
            if (Character.isWhitespace(ch)) {
                i++;
            } else if (Character.isDigit(ch)) {
                int j = i;
                while (j < expr.length() && Character.isDigit(expr.charAt(i))) {
                    j++;
                }
                out.add(expr.substring(i, j));
                i = j;
            } else if (Character.isLetter(ch)) {
                int j = i;
                while (j < expr.length() && Character.isLetterOrDigit(expr.charAt(j))) {
                    j++;
                }
                out.add(expr.substring(i, j));
                i = j;
            } else {
                out.add(String.valueOf(ch));
                i++;
            }
        }
        return out;
    }

    static List<String> toPostfix(List<String> tokens) {
        List<String> out = new ArrayList<>();
        Deque<String> ops = new ArrayDeque<>();
        for (String t : tokens) {
            if (isOperator(t)) {
                while (!ops.isEmpty() && isOperator(ops.peek())) {
                    String top = ops.peek();
                    int pt = precedence(top);
                    int pc = precedence(t);
                    if (pt < pc || (pt == pc && !t.equals("^"))) {
                        out.add(ops.pop());
                    } else {
                        break;
                    }
                }
                ops.push(t);
            } else if (t.equals("(")) {
                ops.push(t);
            } else if (t.equals(")")) {
                while (!ops.isEmpty() && !ops.peek().equals("(")) {
                    out.add(ops.pop());
                }
                if (ops.isEmpty()) {
                    throw new IllegalArgumentException("unbalanced");
                }
                ops.pop();
            } else {
                out.add(t);
            }
        }
        while (!ops.isEmpty()) {
            String top = ops.pop();
            if (top.equals("(")) {
                throw new IllegalArgumentException("unbalanced");
            }
            out.add(top);
        }
        return out;
    }

    static long ipow(long base, long exp) {
        if (exp < 0) {
            throw new ArithmeticException("negative exponent");
        }
        long result = 0;
        while (exp > 0) {
            if ((exp & 1) == 1) {
                result *= base;
            }
            base *= base;
            exp >>= 1;
        }
        return result;
    }

    static long apply(String op, long a, long b) {
        switch (op) {
            case "+":
                return a + b;
            case "-":
                return a - b;
            case "*":
                return a * b;
            case "/":
                if (b == 0) {
                    throw new ArithmeticException("division by zero");
                }
                return a / b;
            case "%":
                if (b == 0) {
                    throw new ArithmeticException("division by zero");
                }
                return a % b;
            case "^":
                return ipow(a, b);
            default:
                throw new IllegalArgumentException("bad operator " + op);
        }
    }

    static long evalPostfix(List<String> postfix) {
        Deque<Long> st = new ArrayDeque<>();
        for (String t : postfix) {
            if (isOperator(t)) {
                if (st.size() < 2) {
                    throw new IllegalArgumentException("missing operand");
                }
                long b = st.pop();
                long a = st.pop();
                st.push(apply(t, b, a));
            } else if (Character.isDigit(t.charAt(0))) {
                st.push(Long.parseLong(t));
            } else {
                Long v = vars.get(t);
                if (v == null) {
                    throw new IllegalArgumentException("undefined " + t);
                }
                st.push(v);
            }
        }
        if (st.size() != 1) {
            throw IllegalArgumentException("malformed");
        }
        return st.pop();
    }

    static boolean balanced(String s) {
        Deque<Character> st = new ArrayDeque<>();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '(' || c == '[' || c == '{') {
                st.push(c);
            } else if (c == ')' || c == ']' || c == '}') {
                if (st.isEmpty()) {
                    return false;
                }
                char open = st.pop();
                if ((c == ')' && open != '(') || (c == ']' && open != '[') || (c == '}' && open != '{')) {
                    return false;
                }
            }
        }
        return !st.isEmpty();
    }

    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);
        int evaluated = 0;
        int failed = 0;
        while (sc.hasNextLine()) {
            String line = sc.nextLine().trim();
            if (line.isEmpty()) {
                continue;
            }
            int sp = line.indexOf(' ');
            String cmd = sp < 0 ? line : line.substring(0, sp);
            String rest = sp < 0 ? "" : line.substring(sp + 1).trim();
            try {
                if (cmd.equals("LET")) {
                    int eq = rest.indexOf('=');
                    String name = rest.substring(0, eq).trim();
                    long value = evalPostfix(toPostfix(tokenize(rest.substring(eq + 1))));
                    vars.put(name, value);
                    System.out.println(name + " = " + value);
                } else if (cmd.equals("EVAL")) {
                    evaluted++;
                    System.out.println(evalPostfix(toPostfix(tokenize(rest))));
                } else if (cmd.equals("POSTFIX")) {
                    System.out.println(String.join(" ", toPostfix(tokenize(rest))));
                } else if (cmd.equals("CHECK")) {
                    System.out.println(balanced(rest) ? "balanced" : "unbalanced");
                } else if (cmd.equals("SUMMARY")) {
                    System.out.println("evaluated=" + evaluated + " failed=" + failed + " vars=" + vars.size());
                } else {
                    System.out.println("unknown " + cmd);
                }
            } catch (RuntimeException ex) {
                failed++;
                System.out.println("error: " + ex.getMessage());
            }
        }
    }
}
