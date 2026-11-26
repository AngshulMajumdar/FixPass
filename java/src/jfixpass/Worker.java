package jfixpass;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintStream;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Long-lived test JVM. Each job names a class directory; every test runs in a fresh class loader (fresh static
 * state) with stdin/stdout redirected. On a timeout the worker reports and halts, and the parent respawns it.
 *
 * Job line: classDir \t mainClass \t cov(0|1) \t idx,idx,... \t mustPass,idx,...
 * Reply: one JSON object per test, then END (or DEAD after a timeout).
 */
public final class Worker {
    private static final int OUTPUT_CAP = 1 << 20;

    static final class Capped extends OutputStream {
        final ByteArrayOutputStream buf = new ByteArrayOutputStream();

        @Override
        public void write(int b) {
            if (buf.size() < OUTPUT_CAP) {
                buf.write(b);
            }
        }

        @Override
        public void write(byte[] b, int off, int len) {
            int room = OUTPUT_CAP - buf.size();
            if (room > 0) {
                buf.write(b, off, Math.min(room, len));
            }
        }
    }

    public static void main(String[] args) throws Exception {
        @SuppressWarnings("unchecked")
        List<Object> tests = (List<Object>) Json.parse(Files.readString(Path.of(args[0])));
        long timeoutMs = Long.parseLong(args[1]);
        PrintStream proto = new PrintStream(new FileOutputStream(FileDescriptor.out), true, StandardCharsets.UTF_8);
        BufferedReader jobs = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        System.setErr(new PrintStream(OutputStream.nullOutputStream()));
        String line;
        while ((line = jobs.readLine()) != null) {
            String[] f = line.split("\t", -1);
            Path dir = Path.of(f[0]);
            String mainClass = f[1];
            boolean cov = f[2].equals("1");
            Set<Integer> mustPass = new HashSet<>();
            if (!f[4].isEmpty()) {
                for (String s : f[4].split(",")) {
                    mustPass.add(Integer.parseInt(s));
                }
            }
            for (String s : f[3].split(",")) {
                if (s.isEmpty()) {
                    continue;
                }
                int idx = Integer.parseInt(s);
                @SuppressWarnings("unchecked")
                Map<String, Object> t = (Map<String, Object>) tests.get(idx);
                String result = runOne(dir, mainClass, cov, idx, (String) t.get("input"), (String) t.get("output"), timeoutMs);
                proto.println(result);
                if (result.contains("\"timeout\":true")) {
                    proto.println("DEAD");
                    proto.flush();
                    Runtime.getRuntime().halt(0);
                }
                if (mustPass.contains(idx) && !result.contains("\"pass\":true")) {
                    break;
                }
            }
            proto.println("END");
        }
    }

    private static String runOne(Path dir, String mainClass, boolean cov, int idx, String input, String expected, long timeoutMs) {
        Capped out = new Capped();
        String[] exc = new String[1];
        List<Integer> trace = new java.util.ArrayList<>();
        URLClassLoader cl;
        long t0 = System.nanoTime();
        boolean timeout = false;
        try {
            cl = new URLClassLoader(new URL[] {dir.toUri().toURL()}, ClassLoader.getPlatformClassLoader());
        } catch (Exception e) {
            return "{\"i\":" + idx + ",\"pass\":false,\"timeout\":false,\"exc\":" + Json.quote("loader: " + e) + ",\"out\":\"\",\"ms\":0}";
        }
        PrintStream ps = new PrintStream(out, true, StandardCharsets.UTF_8);
        System.setIn(new ByteArrayInputStream(input.getBytes(StandardCharsets.UTF_8)));
        System.setOut(ps);
        Thread th = new Thread(null, () -> {
            try {
                Method m = cl.loadClass(mainClass).getMethod("main", String[].class);
                m.invoke(null, (Object) new String[0]);
            } catch (InvocationTargetException e) {
                exc[0] = String.valueOf(e.getCause());
                for (StackTraceElement f : e.getCause().getStackTrace()) {
                    String c = f.getClassName();
                    if (f.getLineNumber() > 0 && !c.startsWith("java.") && !c.startsWith("jdk.") && !c.startsWith("sun.")
                            && !c.startsWith("jfixpass.") && !c.equals("__Fuel") && !c.equals("__JCov") && trace.size() < 16) {
                        trace.add(f.getLineNumber());
                    }
                }
            } catch (Throwable e) {
                exc[0] = String.valueOf(e);
            }
        }, "jfixpass-test", 256L << 20);
        th.setDaemon(true);
        th.start();
        try {
            th.join(timeoutMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        if (th.isAlive()) {
            timeout = true;
        }
        ps.flush();
        long ms = (System.nanoTime() - t0) / 1_000_000;
        String got = out.buf.toString(StandardCharsets.UTF_8);
        boolean pass = !timeout && exc[0] == null && got.strip().equals(expected.strip());
        StringBuilder sb = new StringBuilder();
        sb.append("{\"i\":").append(idx).append(",\"pass\":").append(pass).append(",\"timeout\":").append(timeout)
                .append(",\"exc\":").append(exc[0] == null ? "null" : Json.quote(exc[0])).append(",\"ms\":").append(ms)
                .append(",\"out\":").append(Json.quote(got));
        synchronized (trace) {
            sb.append(",\"trace\":").append(trace.toString().replace(" ", ""));
        }
        try {
            java.lang.reflect.Field ff = cl.loadClass("__Fuel").getDeclaredField("n");
            ff.setAccessible(true);
            sb.append(",\"fuel\":").append(ff.getLong(null));
        } catch (Throwable e) {
            // not fuel-instrumented
        }
        if (cov) {
            sb.append(",\"hits\":[");
            try {
                java.lang.reflect.Field hf = cl.loadClass("__JCov").getDeclaredField("H");
                hf.setAccessible(true);
                boolean[] h = (boolean[]) hf.get(null);
                boolean first = true;
                for (int k = 0; k < h.length; k++) {
                    if (h[k]) {
                        sb.append(first ? "" : ",").append(k);
                        first = false;
                    }
                }
            } catch (Throwable e) {
                // no coverage class: report no hits
            }
            sb.append(']');
        }
        sb.append('}');
        if (!timeout) {
            try {
                cl.close();
            } catch (Exception ignored) {
                // best effort
            }
        }
        return sb.toString();
    }
}
