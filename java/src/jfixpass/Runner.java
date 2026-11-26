package jfixpass;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.StringJoiner;

/** Parent side of the worker JVM: sends jobs, collects per-test outcomes, respawns after timeouts. */
final class Runner implements AutoCloseable {
    record Test(String input, String output) {
    }

    record Outcome(int index, boolean pass, boolean timeout, String exc, String out, long ms, double similarity, int[] hits,
                   long fuel, int[] trace) {
        double score() {
            return pass ? 2.0 : similarity;
        }
    }

    final List<Test> tests;
    private final Path testFile;
    private final long timeoutMs;
    private Process proc;
    private BufferedWriter toWorker;
    private BufferedReader fromWorker;
    int spawns;
    long testRuns;

    Runner(List<Test> tests, Path workDir, long timeoutMs) {
        this.tests = tests;
        this.timeoutMs = timeoutMs;
        this.testFile = workDir.resolve("tests_all.json");
        List<Object> raw = new ArrayList<>();
        for (Test t : tests) {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("input", t.input());
            m.put("output", t.output());
            raw.add(m);
        }
        try {
            Files.writeString(testFile, Json.write(raw));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private void ensure() throws IOException {
        if (proc != null && proc.isAlive()) {
            return;
        }
        String java = Path.of(System.getProperty("java.home"), "bin", "java").toString();
        ProcessBuilder pb = new ProcessBuilder(java, "-Xmx384m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
                "-Xshare:auto", "-cp", System.getProperty("java.class.path"), "jfixpass.Worker", testFile.toString(),
                String.valueOf(timeoutMs));
        pb.redirectError(ProcessBuilder.Redirect.DISCARD);
        pb.environment().remove("JAVA_TOOL_OPTIONS");
        pb.environment().remove("_JAVA_OPTIONS");
        proc = pb.start();
        spawns++;
        toWorker = new BufferedWriter(new OutputStreamWriter(proc.getOutputStream(), StandardCharsets.UTF_8));
        fromWorker = new BufferedReader(new InputStreamReader(proc.getInputStream(), StandardCharsets.UTF_8));
    }

    /**
     * Run tests in the given order. Stops early when a test in {@code mustPass} fails, or when a test outside
     * {@code mayTimeout} times out. Unrun tests are null in the result; {@code aborted} reports early stops.
     */
    Outcome[] run(Path classDir, String mainClass, boolean cov, List<Integer> order, Set<Integer> mustPass,
                  Set<Integer> mayTimeout, boolean[] aborted) {
        return run(classDir, mainClass, cov, order, mustPass, mayTimeout, aborted, false);
    }

    /**
     * As above; with {@code stopOnRepeatTimeout}, a test that times out again (it is in {@code mayTimeout}) ends the
     * run: the remaining tests are left unrun so the caller can carry their previous outcomes forward.
     */
    Outcome[] run(Path classDir, String mainClass, boolean cov, List<Integer> order, Set<Integer> mustPass,
                  Set<Integer> mayTimeout, boolean[] aborted, boolean stopOnRepeatTimeout) {
        Outcome[] res = new Outcome[tests.size()];
        List<Integer> todo = new ArrayList<>(order);
        aborted[0] = false;
        boolean stopped = false;
        try {
            while (!todo.isEmpty()) {
                ensure();
                StringJoiner idx = new StringJoiner(",");
                todo.forEach(i -> idx.add(String.valueOf(i)));
                StringJoiner mp = new StringJoiner(",");
                mustPass.forEach(i -> mp.add(String.valueOf(i)));
                toWorker.write(classDir + "\t" + mainClass + "\t" + (cov ? "1" : "0") + "\t" + idx + "\t" + mp + "\n");
                toWorker.flush();
                boolean dead = false;
                String line;
                while (true) {
                    line = fromWorker.readLine();
                    if (line == null) {
                        dead = true;
                        break;
                    }
                    if (line.equals("END")) {
                        break;
                    }
                    if (line.equals("DEAD")) {
                        dead = true;
                        continue;
                    }
                    Outcome o = parse(line);
                    res[o.index()] = o;
                    testRuns++;
                    todo.remove(Integer.valueOf(o.index()));
                    if (o.timeout() && !mayTimeout.contains(o.index())) {
                        aborted[0] = true;
                    }
                    if (o.timeout() && mayTimeout.contains(o.index()) && stopOnRepeatTimeout) {
                        stopped = true;
                    }
                    if (mustPass.contains(o.index()) && !o.pass()) {
                        aborted[0] = true;
                    }
                }
                if (dead) {
                    proc.waitFor();
                    proc = null;
                    if (!todo.isEmpty() && res[todo.get(0)] == null && !aborted[0] && line == null) {
                        // worker crashed without reporting: record the next test as a crash and continue
                        int i = todo.remove(0);
                        res[i] = new Outcome(i, false, true, "worker crashed", "", 0, 0.0, new int[0], 0, new int[0]);
                        if (!mayTimeout.contains(i)) {
                            aborted[0] = true;
                        }
                    }
                }
                if (aborted[0] || stopped) {
                    break;
                }
                if (!dead) {
                    break;
                }
            }
        } catch (IOException | InterruptedException e) {
            throw new IllegalStateException("worker failure: " + e, e);
        }
        return res;
    }

    private Outcome parse(String line) {
        @SuppressWarnings("unchecked")
        Map<String, Object> m = (Map<String, Object>) Json.parse(line);
        int i = ((Double) m.get("i")).intValue();
        String out = (String) m.get("out");
        int[] hits = new int[0];
        if (m.get("hits") instanceof List<?> h) {
            hits = new int[h.size()];
            for (int k = 0; k < h.size(); k++) {
                hits[k] = ((Double) h.get(k)).intValue();
            }
        }
        double sim = Text.lineSimilarity(out == null ? "" : out, tests.get(i).output());
        long fuel = m.get("fuel") instanceof Double f ? f.longValue() : 0;
        int[] trace = new int[0];
        if (m.get("trace") instanceof List<?> tl) {
            trace = new int[tl.size()];
            for (int k = 0; k < tl.size(); k++) {
                trace[k] = ((Double) tl.get(k)).intValue();
            }
        }
        return new Outcome(i, (Boolean) m.get("pass"), (Boolean) m.get("timeout"), (String) m.get("exc"), out,
                ((Double) m.get("ms")).longValue(), sim, hits, fuel, trace);
    }

    @Override
    public void close() {
        if (proc != null) {
            proc.destroyForcibly();
        }
    }
}
