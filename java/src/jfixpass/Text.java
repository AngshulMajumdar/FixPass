package jfixpass;

import java.util.List;

/** Lexical helpers that respect string/char literals and comments. */
final class Text {
    private Text() {
    }

    /** One edit: replace src[start,end) by text. */
    record Edit(int start, int end, String text) {
        String apply(String src) {
            return src.substring(0, start) + text + src.substring(end);
        }

        int delta() {
            return text.length() - (end - start);
        }
    }

    /** Apply non-overlapping edits (any order). */
    static String apply(String src, List<Edit> edits) {
        List<Edit> sorted = new java.util.ArrayList<>(edits);
        sorted.sort((a, b) -> Integer.compare(b.start(), a.start()));
        String out = src;
        int lastStart = Integer.MAX_VALUE;
        for (Edit e : sorted) {
            if (e.end() > lastStart) {
                throw new IllegalArgumentException("overlapping edits");
            }
            out = e.apply(out);
            lastStart = e.start();
        }
        return out;
    }

    /** true for characters that are code (not inside comments or string/char literals). */
    static boolean[] codeMask(String s) {
        boolean[] code = new boolean[s.length()];
        int i = 0;
        while (i < s.length()) {
            char c = s.charAt(i);
            if (c == '/' && i + 1 < s.length() && s.charAt(i + 1) == '/') {
                while (i < s.length() && s.charAt(i) != '\n') {
                    i++;
                }
            } else if (c == '/' && i + 1 < s.length() && s.charAt(i + 1) == '*') {
                int e = s.indexOf("*/", i + 2);
                i = e < 0 ? s.length() : e + 2;
            } else if (c == '"' || c == '\'') {
                if (c == '"' && s.startsWith("\"\"\"", i)) {
                    int e = s.indexOf("\"\"\"", i + 3);
                    i = e < 0 ? s.length() : e + 3;
                    continue;
                }
                int j = i + 1;
                while (j < s.length() && s.charAt(j) != c && s.charAt(j) != '\n') {
                    if (s.charAt(j) == '\\') {
                        j++;
                    }
                    j++;
                }
                i = Math.min(s.length(), j + 1);
            } else {
                code[i] = true;
                i++;
            }
        }
        return code;
    }

    /** End offset of the last non-whitespace character strictly before pos. */
    static int prevTokenEnd(String s, int pos) {
        int i = Math.min(pos, s.length()) - 1;
        while (i >= 0 && Character.isWhitespace(s.charAt(i))) {
            i--;
        }
        return i + 1;
    }

    /** [start, end) of the token at pos (identifier/number run, or a single symbol char). */
    static int[] tokenAt(String s, int pos) {
        if (pos < 0 || pos >= s.length()) {
            return null;
        }
        char c = s.charAt(pos);
        if (Character.isWhitespace(c)) {
            return null;
        }
        if (Character.isJavaIdentifierPart(c)) {
            int a = pos;
            int b = pos;
            while (a > 0 && Character.isJavaIdentifierPart(s.charAt(a - 1))) {
                a--;
            }
            while (b < s.length() && Character.isJavaIdentifierPart(s.charAt(b))) {
                b++;
            }
            return new int[] {a, b};
        }
        return new int[] {pos, pos + 1};
    }

    static int lineOf(String s, int pos) {
        int line = 1;
        for (int i = 0; i < Math.min(pos, s.length()); i++) {
            if (s.charAt(i) == '\n') {
                line++;
            }
        }
        return line;
    }

    static int lineStart(String s, int pos) {
        int i = Math.min(pos, s.length());
        while (i > 0 && s.charAt(i - 1) != '\n') {
            i--;
        }
        return i;
    }

    static String indentOf(String s, int pos) {
        int a = lineStart(s, pos);
        int b = a;
        while (b < s.length() && (s.charAt(b) == ' ' || s.charAt(b) == '\t')) {
            b++;
        }
        return s.substring(a, b);
    }

    /** Optimal string alignment (Damerau-Levenshtein with adjacent transpositions). */
    static int distance(String a, String b) {
        int n = a.length();
        int m = b.length();
        int[][] d = new int[n + 1][m + 1];
        for (int i = 0; i <= n; i++) {
            d[i][0] = i;
        }
        for (int j = 0; j <= m; j++) {
            d[0][j] = j;
        }
        for (int i = 1; i <= n; i++) {
            for (int j = 1; j <= m; j++) {
                int cost = a.charAt(i - 1) == b.charAt(j - 1) ? 0 : 1;
                d[i][j] = Math.min(Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1), d[i - 1][j - 1] + cost);
                if (i > 1 && j > 1 && a.charAt(i - 1) == b.charAt(j - 2) && a.charAt(i - 2) == b.charAt(j - 1)) {
                    d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
                }
            }
        }
        return d[n][m];
    }

    /** Line-level longest-common-subsequence similarity in [0,1]. */
    static double lineSimilarity(String got, String want) {
        String[] a = got.strip().split("\n", -1);
        String[] b = want.strip().split("\n", -1);
        if (got.strip().isEmpty()) {
            a = new String[0];
        }
        if (want.strip().isEmpty()) {
            b = new String[0];
        }
        if (a.length == 0 && b.length == 0) {
            return 1.0;
        }
        int[][] d = new int[a.length + 1][b.length + 1];
        for (int i = 1; i <= a.length; i++) {
            for (int j = 1; j <= b.length; j++) {
                d[i][j] = a[i - 1].strip().equals(b[j - 1].strip()) ? d[i - 1][j - 1] + 1 : Math.max(d[i - 1][j], d[i][j - 1]);
            }
        }
        return (double) d[a.length][b.length] / Math.max(a.length, b.length);
    }
}
