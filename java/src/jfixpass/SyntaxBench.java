package jfixpass;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Batch driver for the compile stages (syntax + types), used by scripts/stress_syntax.py.
 * stdin: lines "input.java\toutput.java"; stdout: one JSON object per input.
 */
public final class SyntaxBench {
    private SyntaxBench() {
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        String line;
        while ((line = in.readLine()) != null) {
            String[] f = line.split("\t");
            String src = Files.readString(Path.of(f[0]));
            Javac.className = Engine.mainClassName(src);
            long t0 = System.nanoTime();
            boolean parsedBefore = Javac.parse(src).errors().isEmpty();
            CompileRepair cr = new CompileRepair();
            String out = cr.run(src);
            double secs = (System.nanoTime() - t0) / 1e9;
            boolean parses = Javac.parse(out).errors().isEmpty();
            boolean compiles = parses && Javac.analyze(out).errors().isEmpty();
            Files.writeString(Path.of(f[1]), out);
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("input", f[0]);
            m.put("parsed_before", parsedBefore);
            m.put("parses", parses);
            m.put("compiles", compiles);
            m.put("lossy", cr.lossy);
            m.put("syntax_steps", (double) cr.log.stream().filter(x -> "syntax".equals(x.get("phase"))).count());
            m.put("syntax_parses", (double) cr.syntaxParses);
            m.put("holes", (double) cr.holes.size());
            m.put("failure", cr.failure);
            m.put("seconds", Math.round(secs * 1000) / 1000.0);
            System.out.println(Json.write(m).replace("\n", ""));
            System.out.flush();
        }
    }
}
