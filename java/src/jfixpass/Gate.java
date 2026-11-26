package jfixpass;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Supported-subset gate: single-file console Java using java.base data/IO APIs only. */
final class Gate {
    private static final Pattern FORBIDDEN = Pattern.compile("(?<![\\w$])(System\\s*\\.\\s*(exit|set(In|Out|Err|Property|SecurityManager)|load(Library)?)"
            + "|Runtime|ProcessBuilder|Thread|ClassLoader|Unsafe|reflect|Class\\s*\\.\\s*forName|Socket|URL|Files|Paths|File"
            + "|FileInputStream|FileOutputStream|FileReader|FileWriter|RandomAccessFile|native|__JCov)(?![\\w$])");
    private static final Pattern IMPORT = Pattern.compile("(?m)^\\s*import\\s+(static\\s+)?([\\w.*]+)\\s*;");
    private static final Pattern ALLOWED_IMPORT = Pattern.compile("java\\.(util(\\.(function|stream|regex))?|math|text|time)\\.[\\w*]+"
            + "|java\\.io\\.(BufferedReader|InputStreamReader|IOException|UncheckedIOException|PrintWriter|PrintStream|BufferedWriter"
            + "|OutputStreamWriter|StringReader|StringWriter|Reader|Writer|InputStream|OutputStream|StreamTokenizer)");

    private Gate() {
    }

    static String check(String src) {
        if (src.length() > 200_000) {
            return "source larger than 200 KB";
        }
        if (src.matches("(?s).*^\\s*package\\s.*")) {
            return "package declarations are not supported (single-file default package)";
        }
        boolean[] code = Text.codeMask(src);
        StringBuilder masked = new StringBuilder(src.length());
        for (int i = 0; i < src.length(); i++) {
            masked.append(code[i] || src.charAt(i) == '\n' ? src.charAt(i) : ' ');
        }
        String m = masked.toString();
        Matcher im = IMPORT.matcher(m);
        while (im.find()) {
            if (im.group(1) != null || !ALLOWED_IMPORT.matcher(im.group(2)).matches()) {
                return "unsupported import " + im.group(2);
            }
        }
        Matcher f = FORBIDDEN.matcher(m);
        if (f.find()) {
            return "unsupported API or construct '" + f.group(1).replaceAll("\\s+", "") + "' at line " + Text.lineOf(src, f.start());
        }
        return null;
    }
}
