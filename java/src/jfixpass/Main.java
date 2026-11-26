package jfixpass;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/** CLI: jfixpass SOURCE.java [--tests T.json] [--extra-tests E.json] [--spec S.txt] --work-dir DIR [--out CLASSDIR] */
public final class Main {
    private Main() {
    }

    public static void main(String[] args) {
        System.exit(run(args));
    }

    static int run(String[] args) {
        String source = null;
        String testsPath = null;
        String extraPath = null;
        String specPath = null;
        String workDir = null;
        String out = null;
        for (int i = 0; i < args.length; i++) {
            String a = args[i];
            switch (a) {
                case "--tests" -> testsPath = args[++i];
                case "--extra-tests" -> extraPath = args[++i];
                case "--spec" -> specPath = args[++i];
                case "--work-dir" -> workDir = args[++i];
                case "--out", "-o" -> out = args[++i];
                case "-h", "--help" -> {
                    System.out.println("usage: jfixpass SOURCE.java [--tests T.json] [--extra-tests E.json] [--spec S.txt]"
                            + " --work-dir DIR [--out CLASSDIR]");
                    return 0;
                }
                default -> {
                    if (a.startsWith("-") || source != null) {
                        System.err.println("unexpected argument: " + a);
                        return 2;
                    }
                    source = a;
                }
            }
        }
        if (source == null || workDir == null) {
            System.err.println("usage: jfixpass SOURCE.java [--tests T.json] --work-dir DIR");
            return 2;
        }
        try {
            Path wd = Path.of(workDir);
            if (Files.exists(wd) && Files.list(wd).findAny().isPresent()) {
                System.err.println("work directory is not empty: " + wd);
                return 2;
            }
            Files.createDirectories(wd);
            String src = Files.readString(Path.of(source));
            List<Runner.Test> tests = new ArrayList<>();
            for (String p : new String[] {testsPath, extraPath}) {
                if (p != null) {
                    tests.addAll(readTests(Path.of(p)));
                }
            }
            if (tests.size() > 500) {
                System.err.println("at most 500 tests are supported");
                return 2;
            }
            Engine e = new Engine(wd, tests);
            if (specPath != null) {
                e.report.put("spec", Files.readString(Path.of(specPath)));
            }
            int code = e.run(src, out == null ? null : Path.of(out));
            System.out.println(e.report.get("status"));
            return code;
        } catch (Exception ex) {
            System.err.println("error: " + ex);
            return 2;
        }
    }

    static List<Runner.Test> readTests(Path p) throws java.io.IOException {
        Object v = Json.parse(Files.readString(p));
        if (!(v instanceof List<?> l)) {
            throw new IllegalArgumentException("tests must be a JSON array");
        }
        List<Runner.Test> out = new ArrayList<>();
        for (Object o : l) {
            if (!(o instanceof Map<?, ?> m) || !(m.get("input") instanceof String in) || !(m.get("output") instanceof String exp)) {
                throw new IllegalArgumentException("each test needs string fields input and output");
            }
            out.add(new Runner.Test(in, exp));
        }
        return out;
    }
}
