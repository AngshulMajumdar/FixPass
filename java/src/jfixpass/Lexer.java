package jfixpass;

import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/** Error-tolerant Java tokenizer: never fails; malformed input becomes ERROR or unterminated tokens. */
final class Lexer {
    enum K { IDENT, KEYWORD, NUMBER, STRING, CHAR, OP, ERROR }

    record Tok(K kind, String text, int start, int end, boolean unterminated) {
    }

    static final Set<String> KEYWORDS = Set.of("abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
            "class", "const", "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float",
            "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native", "new", "package",
            "private", "protected", "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized",
            "this", "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false", "null", "var",
            "record", "yield");

    static final String[] OPS = {">>>=", "<<=", ">>=", "...", "->", "::", "++", "--", "&&", "||", "==", "!=", "<=", ">=",
        "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", "(", ")", "{", "}", "[", "]", ";", ",", ".", "@", "=", ">",
        "<", "!", "~", "?", ":", "+", "-", "*", "/", "&", "|", "^", "%"};

    private Lexer() {
    }

    static List<Tok> lex(String s) {
        List<Tok> out = new ArrayList<>();
        int i = 0;
        int n = s.length();
        while (i < n) {
            char c = s.charAt(i);
            if (Character.isWhitespace(c)) {
                i++;
                continue;
            }
            if (c == '/' && i + 1 < n && s.charAt(i + 1) == '/') {
                while (i < n && s.charAt(i) != '\n') {
                    i++;
                }
                continue;
            }
            if (c == '/' && i + 1 < n && s.charAt(i + 1) == '*') {
                int e = s.indexOf("*/", i + 2);
                if (e < 0) {
                    // unterminated comment: report the opener and keep lexing, so the rest of the file stays visible
                    out.add(new Tok(K.ERROR, "/*", i, i + 2, true));
                    i += 2;
                } else {
                    i = e + 2;
                }
                continue;
            }
            int st = i;
            if (c == '"' && s.startsWith("\"\"\"", i)) {
                int e = s.indexOf("\"\"\"", i + 3);
                if (e < 0) {
                    out.add(new Tok(K.ERROR, "\"\"\"", i, i + 3, true));
                    i += 3;
                    continue;
                }
                i = e + 3;
                out.add(new Tok(K.STRING, s.substring(st, i), st, i, false));
                continue;
            }
            if (c == '"' || c == '\'') {
                int j = i + 1;
                boolean closed = false;
                while (j < n && s.charAt(j) != '\n') {
                    char d = s.charAt(j);
                    if (d == '\\') {
                        j += 2;
                        continue;
                    }
                    if (d == c) {
                        closed = true;
                        j++;
                        break;
                    }
                    j++;
                }
                j = Math.min(j, n);
                out.add(new Tok(c == '"' ? K.STRING : K.CHAR, s.substring(st, j), st, j, !closed));
                i = j;
                continue;
            }
            if (Character.isJavaIdentifierStart(c)) {
                int j = i + 1;
                while (j < n && Character.isJavaIdentifierPart(s.charAt(j))) {
                    j++;
                }
                String w = s.substring(i, j);
                out.add(new Tok(KEYWORDS.contains(w) ? K.KEYWORD : K.IDENT, w, i, j, false));
                i = j;
                continue;
            }
            if (Character.isDigit(c) || (c == '.' && i + 1 < n && Character.isDigit(s.charAt(i + 1)))) {
                int j = i + 1;
                while (j < n) {
                    char d = s.charAt(j);
                    if (Character.isLetterOrDigit(d) || d == '_' || d == '.') {
                        j++;
                    } else if ((d == '+' || d == '-') && (s.charAt(j - 1) == 'e' || s.charAt(j - 1) == 'E')
                            && !s.substring(i, j).startsWith("0x")) {
                        j++;
                    } else {
                        break;
                    }
                }
                out.add(new Tok(K.NUMBER, s.substring(i, j), i, j, false));
                i = j;
                continue;
            }
            String op = null;
            for (String o : OPS) {
                if (s.startsWith(o, i)) {
                    op = o;
                    break;
                }
            }
            if (op != null) {
                out.add(new Tok(K.OP, op, i, i + op.length(), false));
                i += op.length();
                continue;
            }
            out.add(new Tok(K.ERROR, String.valueOf(c), i, i + 1, false));
            i++;
        }
        return out;
    }

    /** Index of the first token whose end is after pos (the token at or after pos); size() if none. */
    static int indexAt(List<Tok> toks, int pos) {
        int lo = 0;
        int hi = toks.size();
        while (lo < hi) {
            int mid = (lo + hi) >>> 1;
            if (toks.get(mid).end() <= pos) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
        return lo;
    }

    static String joined(List<Tok> toks) {
        StringBuilder sb = new StringBuilder();
        for (Tok t : toks) {
            sb.append(t.text()).append('\u0001');
        }
        return sb.toString();
    }
}
