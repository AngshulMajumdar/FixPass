package jfixpass;

import com.sun.source.tree.CompilationUnitTree;
import com.sun.source.tree.Tree;
import com.sun.source.util.JavacTask;
import com.sun.source.util.SourcePositions;
import com.sun.source.util.Trees;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Locale;
import javax.tools.Diagnostic;
import javax.tools.DiagnosticCollector;
import javax.tools.JavaCompiler;
import javax.tools.JavaFileObject;
import javax.tools.SimpleJavaFileObject;
import javax.tools.StandardJavaFileManager;
import javax.tools.ToolProvider;

/** In-process javac: parse, attribute and compile a single in-memory source file. */
final class Javac {
    static final JavaCompiler COMPILER = ToolProvider.getSystemJavaCompiler();
    static final StandardJavaFileManager FILES = COMPILER.getStandardFileManager(null, Locale.ROOT, StandardCharsets.UTF_8);
    private static Path emptyPath;
    static String className = "Main";
    static long calls;

    record Diag(Diagnostic.Kind kind, String code, int pos, int start, int end, int line, String message) {
        boolean error() {
            return kind == Diagnostic.Kind.ERROR;
        }

        String brief() {
            return "L" + line + " [" + code + "] " + message.split("\n")[0];
        }
    }

    /** Result of a parse or analyze call; the tree is available for parse-clean sources. */
    static final class Unit {
        final String src;
        final List<Diag> diags;
        final JavacTask task;
        final CompilationUnitTree tree;
        final Trees trees;

        Unit(String src, List<Diag> diags, JavacTask task, CompilationUnitTree tree) {
            this.src = src;
            this.diags = diags;
            this.task = task;
            this.tree = tree;
            this.trees = Trees.instance(task);
        }

        List<Diag> errors() {
            List<Diag> out = new ArrayList<>();
            for (Diag d : diags) {
                if (d.error()) {
                    out.add(d);
                }
            }
            out.sort((a, b) -> Integer.compare(a.pos(), b.pos()));
            return out;
        }

        SourcePositions positions() {
            return trees.getSourcePositions();
        }

        int start(Tree t) {
            return (int) positions().getStartPosition(tree, t);
        }

        int end(Tree t) {
            return (int) positions().getEndPosition(tree, t);
        }

        String text(Tree t) {
            int s = start(t);
            int e = end(t);
            return s < 0 || e < s ? "" : src.substring(s, e);
        }
    }

    private static synchronized List<String> options(Path out) {
        try {
            if (emptyPath == null) {
                emptyPath = Files.createTempDirectory("jfixpass-empty");
                emptyPath.toFile().deleteOnExit();
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        List<String> o = new ArrayList<>(List.of("-Xlint:all", "-proc:none", "-implicit:none", "-encoding", "UTF-8",
                "-Xmaxerrs", "500", "-classpath", emptyPath.toString(), "-sourcepath", emptyPath.toString()));
        if (out != null) {
            o.add("-d");
            o.add(out.toString());
        }
        return o;
    }

    static JavaFileObject source(String src) {
        return new SimpleJavaFileObject(URI.create("string:///" + className + ".java"), JavaFileObject.Kind.SOURCE) {
            @Override
            public CharSequence getCharContent(boolean ignoreEncodingErrors) {
                return src;
            }
        };
    }

    private static List<Diag> convert(DiagnosticCollector<JavaFileObject> dc) {
        List<Diag> out = new ArrayList<>();
        for (Diagnostic<? extends JavaFileObject> d : dc.getDiagnostics()) {
            out.add(new Diag(d.getKind(), d.getCode(), (int) d.getPosition(), (int) d.getStartPosition(), (int) d.getEndPosition(),
                    (int) d.getLineNumber(), d.getMessage(Locale.ROOT)));
        }
        return out;
    }

    static Unit parse(String src) {
        return run(src, false);
    }

    static Unit analyze(String src) {
        return run(src, true);
    }

    private static Unit run(String src, boolean analyze) {
        calls++;
        DiagnosticCollector<JavaFileObject> dc = new DiagnosticCollector<>();
        JavacTask task = (JavacTask) COMPILER.getTask(null, FILES, dc, options(null), null, List.of(source(src)));
        CompilationUnitTree cu = null;
        List<Diag> extra = new ArrayList<>();
        try {
            Iterator<? extends CompilationUnitTree> it = task.parse().iterator();
            cu = it.hasNext() ? it.next() : null;
            boolean parseErrors = dc.getDiagnostics().stream().anyMatch(d -> d.getKind() == Diagnostic.Kind.ERROR);
            if (analyze && !parseErrors) {
                task.analyze();
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } catch (RuntimeException e) {
            extra.add(new Diag(Diagnostic.Kind.ERROR, "jfixpass.javac.crash", 0, 0, 0, 1, String.valueOf(e)));
        }
        List<Diag> diags = convert(dc);
        diags.addAll(extra);
        return new Unit(src, diags, task, cu);
    }

    /** Full compilation to class files in {@code out}. Returns all diagnostics. */
    static List<Diag> compileTo(String src, Path out) {
        calls++;
        DiagnosticCollector<JavaFileObject> dc = new DiagnosticCollector<>();
        try {
            Files.createDirectories(out);
            COMPILER.getTask(null, FILES, dc, options(out), null, List.of(source(src))).call();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } catch (RuntimeException e) {
            List<Diag> l = convert(dc);
            l.add(new Diag(Diagnostic.Kind.ERROR, "jfixpass.javac.crash", 0, 0, 0, 1, String.valueOf(e)));
            return l;
        }
        return convert(dc);
    }

    static boolean hasErrors(List<Diag> diags) {
        for (Diag d : diags) {
            if (d.error()) {
                return true;
            }
        }
        return false;
    }
}
