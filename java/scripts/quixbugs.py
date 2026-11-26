#!/usr/bin/env python3
"""Evaluate JFixPass on QuixBugs (Java): 40 classic algorithms, each with a one-line bug, specified by JUnit tests.

Adapter (no change to the compiler). Each program becomes one source file:
  * the buggy class plus the helper classes its tests use (Node, WeightedEdge, QuixFixOracleHelper), packages removed;
  * the JUnit test class itself becomes the driver `public class Main` (fields, static blocks and helper methods kept,
    @Test annotations removed); `main` reads a test number from stdin and runs that test on a fresh instance;
    every `assertEquals(expected, actual[, delta])` becomes a print of `actual` (rounded to the delta), so expected
    values never appear in the file. Expected stdout comes from running the same driver on QuixBugs' correct
    version of the program. Test inputs still live in the driver, so every patch is also re-run with the repaired
    program class put back into the original, untouched harness.
Metrics: compiler-reported success; plausible = the repaired program class, put back into the ORIGINAL untouched
harness, passes every test (so edits to test inputs or helpers do not count); identical to the reference fix
(token-equal program class).

Usage:
  python3 scripts/quixbugs.py prepare [--dataset DIR]
  python3 scripts/quixbugs.py run [--jobs 2] [--budget 120]
  python3 scripts/quixbugs.py report
  python3 scripts/quixbugs.py difftest [NAME ...]            # patches vs correct versions on random inputs
"""
import argparse
import json
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks" / "quixbugs"
WORK = Path(tempfile.gettempdir()) / "jfixpass-quixbugs"
JAR = ROOT / "build" / "jfixpass.jar"
TOKEN = re.compile(r'"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|[A-Za-z_$][\w$]*|\d[\w.]*|\S')


def strip_comments(s):
    return re.sub(r"//[^\n]*|/\*.*?\*/", "", s, flags=re.S)


def tokens(s):
    return TOKEN.findall(strip_comments(s))


def unpublic(src):
    src = re.sub(r"^\s*package\s+[\w.]+\s*;\s*\n", "", src, flags=re.M)
    return re.sub(r"^public\s+((?:final|abstract)\s+)?(class|interface|enum)\b", r"\1\2", src, flags=re.M)


def split_imports(src):
    imps = re.findall(r"^\s*import\s+(?:static\s+)?[\w.*]+\s*;", src, flags=re.M)
    body = re.sub(r"^\s*import\s+(?:static\s+)?[\w.*]+\s*;\s*\n", "", src, flags=re.M)
    return [i.strip() for i in imps], body


# The driver prints the values the program computes; it neither compares them nor contains the expected values.
# Expected stdout comes from the correct program, so a run can only match by computing the right values. (Earlier
# versions of this adapter kept the comparison or the expected value in the file, and the search exploited both:
# `equals(e, a)` -> `equals(a, a)`, and printing `e` instead of `a`.)
HELPERS = """
    static void __out(Object a) {
        System.out.println(String.valueOf(a));
    }

    static void __outd(double a, double d) {
        int digits = d > 0 ? Math.max(0, (int) Math.ceil(-Math.log10(d))) : 6;
        System.out.println(String.format("%." + digits + "f", a));
    }
"""


def strip_expected(code):
    """Rewrite every __assertEquals(expected, actual[, delta]) as a print of the actual value only, so the expected
    values never appear in the file the compiler may edit."""
    out, i, key = [], 0, "__assertEquals("
    while True:
        j = code.find(key, i)
        if j < 0:
            out.append(code[i:])
            return "".join(out)
        out.append(code[i:j])
        k, depth, args, cur = j + len(key), 1, [], ""
        while depth:
            c = code[k]
            if c == '"':
                e = k + 1
                while code[e] != '"':
                    e += 2 if code[e] == "\\" else 1
                cur += code[k:e + 1]
                k = e + 1
                continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    break
            if c == "," and depth == 1:
                args.append(cur)
                cur = ""
            else:
                cur += c
            k += 1
        args.append(cur)
        if len(args) == 3:
            out.append(f"__outd({args[1].strip()}, {args[2].strip()})")
        else:
            out.append(f"__out({args[-1].strip()})")
        i = k + 1


NOOP_IMPORTS = {"import java.lang.*;", "import java.lang.Math.*;"}

TEST_ANN = r"@(?:org\.junit\.)?Test\b(?:\s*\([^)]*\))?"


def build(program_src, helpers, test_src):
    """One source file: program class, helper classes, and the JUnit class turned into the driver `Main`."""
    imports = set()
    parts = []
    for s in [program_src] + helpers:
        imps, body = split_imports(unpublic(s))
        imports.update(i for i in imps if not i.startswith("import java_programs") and not i.startswith("import org.junit")
                       and i not in NOOP_IMPORTS)
        parts.append(body.strip() + "\n")
    timps, tbody = split_imports(re.sub(r"^\s*package\s+[\w.]+\s*;\s*\n", "", test_src, flags=re.M))
    imports.update(i for i in timps if i.startswith("import java.") and "static" not in i)
    # JUnit skips @Ignore'd tests; so does the driver
    ignored = {m.group(1) for m in re.finditer(r"@(?:org\.junit\.)?Ignore\b[^\n]*\n(?:\s*@[^\n]*\n)*\s*public\s+void\s+(\w+)", tbody)}
    names = [m.group(1) for m in re.finditer(TEST_ANN + r"\s*\n\s*public\s+void\s+(\w+)\s*\(", tbody) if m.group(1) not in ignored]
    drv = re.sub(TEST_ANN, "", tbody)
    drv = re.sub(r"@(?:org\.junit\.)?Ignore\b(?:\s*\([^)]*\))?", "", drv)
    drv = drv.replace("org.junit.Assert.assertEquals(", "__assertEquals(")
    drv = re.sub(r"(?<![\w.])assertEquals\(", "__assertEquals(", drv)
    drv = strip_expected(drv)
    drv = drv.replace("java_programs.", "").replace("java_testcases.junit.", "")
    cls = re.search(r"public\s+class\s+(\w+)\s*\{", drv)
    mainm = ["\n    public static void main(String[] args) throws Exception {\n",
             "        int k = Integer.parseInt(new java.util.Scanner(System.in).next());\n",
             "        Main m = new Main();\n        switch (k) {\n"]
    for k, n in enumerate(names):
        mainm.append(f"            case {k}: m.{n}(); break;\n")
    mainm.append("            default: break;\n        }\n    }\n")
    end = drv.rstrip().rfind("}")
    drv = drv[:cls.start()] + "public class Main {\n" + HELPERS + drv[cls.end():end] + "".join(mainm) + "}\n"
    head = "".join(i + "\n" for i in sorted(imports))
    program_part = parts[0]
    pre = (head + "\n" if head else "")
    text = pre + program_part + "\n" + "\n".join(parts[1:]) + "\n" + drv.strip() + "\n"
    start_line = pre.count("\n") + 1
    end_line = start_line + program_part.count("\n")
    return text, len(names), (start_line, end_line)


def run_driver(src, n):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "Main.java").write_text(src)
    c = subprocess.run(["javac", "-nowarn", "-d", str(tmp), str(tmp / "Main.java")], capture_output=True, text=True)
    outs = []
    if c.returncode != 0:
        shutil.rmtree(tmp)
        return None, c.stderr
    for k in range(n):
        try:
            p = subprocess.run(["java", "-Xss64m", "-cp", str(tmp), "Main"], input=f"{k}\n", capture_output=True, text=True, timeout=10)
            outs.append((p.returncode, p.stdout))
        except subprocess.TimeoutExpired:
            outs.append((-1, ""))
    shutil.rmtree(tmp)
    return outs, ""


def prepare(dataset):
    if not dataset.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/jkoppel/QuixBugs.git", str(dataset)], check=True)
    if WORK.exists():
        shutil.rmtree(WORK)
    (WORK / "programs").mkdir(parents=True)
    helpers_all = {"Node": dataset / "java_programs" / "Node.java", "WeightedEdge": dataset / "java_programs" / "WeightedEdge.java",
                   "QuixFixOracleHelper": dataset / "java_testcases" / "junit" / "QuixFixOracleHelper.java"}
    rows = []
    for tf in sorted((dataset / "java_testcases" / "junit").glob("*_TEST.java")):
        name = tf.stem[:-5]
        bug = dataset / "java_programs" / f"{name}.java"
        cor = dataset / "correct_java_programs" / f"{name}.java"
        row = {"id": name}
        if not bug.exists() or not cor.exists():
            row["skip"] = "missing buggy or correct program"
            rows.append(row)
            continue
        test_src = tf.read_text()
        prog_b = bug.read_text()
        prog_c = cor.read_text().replace("correct_java_programs", "java_programs")
        used = [h for h in helpers_all if re.search(r"\b" + h + r"\b", test_src + prog_b) and h != name]
        helpers = [helpers_all[h].read_text() for h in used]
        src_b, n, region = build(prog_b, helpers, test_src)
        src_c, _, _ = build(prog_c, helpers, test_src)
        exp, err = run_driver(src_c, n)
        if exp is None:
            row["skip"] = "correct version does not compile in the driver: " + err[:200]
            rows.append(row)
            continue
        tests = [{"input": f"{k}\n", "output": o} for k, (rc, o) in enumerate(exp) if rc == 0]
        bad = [k for k, (rc, o) in enumerate(exp) if rc != 0 or "FAIL" in o]
        d = WORK / "programs" / name
        d.mkdir()
        (d / "Main.java").write_text(src_b)
        (d / "correct.java").write_text(src_c)
        (d / "tests.json").write_text(json.dumps(tests, indent=1))
        row.update({"tests": len(tests), "junit_tests": n, "correct_version_failing_tests": bad, "helpers": used,
                    "program_lines": region})
        rows.append(row)
    (WORK / "programs.json").write_text(json.dumps(rows, indent=1))
    print(f"prepared {sum('skip' not in r for r in rows)} of {len(rows)} programs in {WORK}")
    for r in rows:
        if "skip" in r or r.get("correct_version_failing_tests"):
            print("  ", r["id"], r.get("skip", f"correct version fails tests {r.get('correct_version_failing_tests')}"))


def class_span(src, name):
    """[start, end) of top-level `class name { ... }` by brace matching (string/char literals and comments skipped)."""
    m = re.search(r"(?m)^(?:(?:final|abstract)\s+)?class\s+" + re.escape(name) + r"\b[^{]*\{", src)
    if not m:
        return None
    i, depth = m.end(), 1
    while depth and i < len(src):
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < len(src) and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i = j + 1
            continue
        if src.startswith("//", i):
            i = src.find("\n", i)
            continue
        if src.startswith("/*", i):
            i = src.find("*/", i) + 2
            continue
        depth += {"{": 1, "}": -1}.get(c, 0)
        i += 1
    return m.start(), i


def program_class(src, region):
    lines = src.split("\n")
    return "\n".join(lines[region[0] - 1:region[1]])


def run_one(row, budget):
    res_file = WORK / "results" / f"{row['id']}.json"
    if res_file.exists():
        return json.loads(res_file.read_text())
    d = WORK / "programs" / row["id"]
    wd = WORK / "runs" / row["id"]
    if wd.exists():
        shutil.rmtree(wd)
    tests = json.loads((d / "tests.json").read_text())
    before, _ = run_driver((d / "Main.java").read_text(), len(tests))
    before_pass = 0 if before is None else sum(rc == 0 and o.strip() == t["output"].strip() for (rc, o), t in zip(before, tests))
    t0 = time.time()
    p = subprocess.run(["java", "-Xss64m", "-XX:+UseSerialGC", f"-Djfixpass.timeBudgetSec={budget}", "-jar", str(JAR),
                        str(d / "Main.java"), "--tests", str(d / "tests.json"), "--work-dir", str(wd)], capture_output=True, text=True)
    secs = time.time() - t0
    rep = json.loads((wd / "report.json").read_text()) if (wd / "report.json").exists() else {}
    out = {"id": row["id"], "tests": len(tests), "before_pass": before_pass, "exit": p.returncode, "status": rep.get("status"),
           "seconds": round(secs, 1)}
    if p.returncode == 0:
        fixed = (wd / "corrected.java").read_text()
        orig = (d / "Main.java").read_text()
        correct = (d / "correct.java").read_text()
        # post-hoc validation: the repaired program class inside the ORIGINAL, untouched harness
        fs, os_, cs = class_span(fixed, row["id"]), class_span(orig, row["id"]), class_span(correct, row["id"])
        if fs and os_:
            recombined = orig[:os_[0]] + fixed[fs[0]:fs[1]] + orig[os_[1]:]
            got, _ = run_driver(recombined, len(tests))
            out["passes_with_untouched_harness"] = got is not None and all(rc == 0 and o.strip() == t["output"].strip()
                                                                           for (rc, o), t in zip(got, tests))
            out["program_class_identical_to_reference"] = bool(cs) and tokens(fixed[fs[0]:fs[1]]) == tokens(correct[cs[0]:cs[1]])
        else:
            out["passes_with_untouched_harness"] = False
            out["program_class_identical_to_reference"] = False
        out["identical_to_reference_fix"] = tokens(fixed) == tokens(correct)
        # which lines changed, and are they all inside the program class?
        a, b = orig.split("\n"), fixed.split("\n")
        changed = [i + 1 for i in range(min(len(a), len(b))) if a[i] != b[i]] + ([min(len(a), len(b)) + 1] if len(a) != len(b) else [])
        lo, hi = row["program_lines"]
        out["edits_only_in_program_class"] = all(lo <= x <= hi for x in changed)
        out["diff"] = [f"L{i}: {a[i - 1].strip()} => {b[i - 1].strip()}" for i in changed if i <= min(len(a), len(b))]
        out["reference_fix"] = [f"{x.strip()} => {y.strip()}" for x, y in zip(program_class(orig, row["program_lines"]).split("\n"),
                                                                             program_class(correct, row["program_lines"]).split("\n")) if x != y]
    (WORK / "results").mkdir(exist_ok=True)
    res_file.write_text(json.dumps(out, indent=1))
    shutil.rmtree(wd / "classes", ignore_errors=True)
    return out


def run(jobs, budget):
    rows = [r for r in json.loads((WORK / "programs.json").read_text()) if "skip" not in r]
    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(lambda row: run_one(row, budget), rows):
            done += 1
            print(f"[{done}/{len(rows)}] {r['id']} exit={r['exit']} before={r['before_pass']}/{r['tests']} "
                  f"identical={r.get('identical_to_reference_fix', '-')} {r['seconds']}s", flush=True)


def report():
    rows = json.loads((WORK / "programs.json").read_text())
    res = [json.loads(f.read_text()) for f in sorted((WORK / "results").glob("*.json"))]
    buggy = [r for r in res if r["before_pass"] < r["tests"]]
    plaus = [r for r in buggy if r["exit"] == 0]
    valid = [r for r in plaus if r.get("passes_with_untouched_harness")]
    summary = {"programs": len(rows), "skipped": [r["id"] + ": " + r["skip"] for r in rows if "skip" in r], "evaluated": len(res),
               "failing_before": len(buggy), "compiler_reported_success": len(plaus),
               "plausible_validated_with_untouched_harness": len(valid),
               "validated_and_identical_to_reference_fix": sum(r.get("program_class_identical_to_reference", False) for r in valid),
               "validated_ids": [r["id"] for r in valid],
               "rejected_by_gate": [r["id"] + ": " + str(r["status"]) for r in res if r["exit"] == 2],
               "edits_only_in_program_class": sum(r["edits_only_in_program_class"] for r in plaus),
               "median_seconds": sorted(r["seconds"] for r in buggy)[len(buggy) // 2] if buggy else 0}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=1) + "\n")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


DIFF_DRIVER = r'''public class Diff {
    static Random r = new Random(1);
    static String run(java.util.concurrent.Callable<Object> f) {
        try { return String.valueOf(f.call()); } catch (Throwable t) { return "EXC " + t.getClass().getSimpleName(); }
    }
    static ArrayList<Integer> list(int n, int max) { ArrayList<Integer> l = new ArrayList<>(); for (int i = 0; i < n; i++) l.add(r.nextInt(max)); return l; }
    public static void main(String[] a) throws Exception {
        String[] names = a.length > 0 ? a : new String[] {"MERGESORT","PASCAL","QUICKSORT"};
        for (String n : names) {
            int diff = 0, total = 0; String ex = null;
            for (int t = 0; t < (n.equals("LEVENSHTEIN") ? 300 : 2000); t++) {
                String p, c, in;
                switch (n) {
                    case "FIND_IN_SORTED": { int len = r.nextInt(12); int[] arr = new int[len]; for (int i = 0; i < len; i++) arr[i] = r.nextInt(20); Arrays.sort(arr); int x = r.nextInt(22) - 1; in = Arrays.toString(arr) + " x=" + x;
                        p = run(() -> FIND_IN_SORTED_P.find_in_sorted(arr, x)); c = run(() -> FIND_IN_SORTED_C.find_in_sorted(arr, x));
                        // any index holding x is acceptable when duplicates exist
                        if (!p.equals(c) && !p.startsWith("EXC") && !c.startsWith("EXC")) { int pi = Integer.parseInt(p), ci = Integer.parseInt(c); if (pi >= 0 && ci >= 0 && arr[pi] == arr[ci]) p = c; }
                        break; }
                    case "GET_FACTORS": { int x = 1 + r.nextInt(5000); in = "n=" + x; p = run(() -> GET_FACTORS_P.get_factors(x)); c = run(() -> GET_FACTORS_C.get_factors(x)); break; }
                    case "LEVENSHTEIN": { String s1 = rs(), s2 = rs(); in = s1 + "," + s2; p = run(() -> LEVENSHTEIN_P.levenshtein(s1, s2)); c = run(() -> LEVENSHTEIN_C.levenshtein(s1, s2)); break; }
                    case "MERGESORT": { ArrayList<Integer> l = list(r.nextInt(12), 30); in = l.toString(); p = run(() -> MERGESORT_P.mergesort(new ArrayList<>(l))); c = run(() -> MERGESORT_C.mergesort(new ArrayList<>(l))); break; }
                    case "PASCAL": { int x = 1 + r.nextInt(12); in = "n=" + x; p = run(() -> PASCAL_P.pascal(x)); c = run(() -> PASCAL_C.pascal(x)); break; }
                    default: { ArrayList<Integer> l = list(r.nextInt(14), 10); in = l.toString(); p = run(() -> QUICKSORT_P.quicksort(new ArrayList<>(l))); c = run(() -> QUICKSORT_C.quicksort(new ArrayList<>(l))); }
                }
                total++;
                if (!p.equals(c)) { diff++; if (ex == null) ex = in + " -> patch " + p + " vs correct " + c; }
            }
            System.out.println(n + ": " + diff + "/" + total + " inputs differ" + (ex == null ? "" : "; e.g. " + ex));
        }
    }
    static String rs() { int n = r.nextInt(6); StringBuilder b = new StringBuilder(); for (int i = 0; i < n; i++) b.append((char) ('a' + r.nextInt(3))); return b.toString(); }
}
'''


DIFF_PROGRAMS = ["FIND_IN_SORTED", "GET_FACTORS", "LEVENSHTEIN", "MERGESORT", "PASCAL", "QUICKSORT"]


def difftest(names):
    """Differential test of repaired program classes against QuixBugs' correct versions on random inputs."""
    parts = []
    for n in DIFF_PROGRAMS:  # the driver references all of them
        fixed = (WORK / "runs" / n / "corrected.java").read_text()
        cor = (WORK / "programs" / n / "correct.java").read_text()
        a, b = class_span(fixed, n), class_span(cor, n)
        parts.append(re.sub(r"\b" + n + r"\b", n + "_P", fixed[a[0]:a[1]]))
        parts.append(re.sub(r"\b" + n + r"\b", n + "_C", cor[b[0]:b[1]]))
    tmp = Path(tempfile.mkdtemp())
    (tmp / "Diff.java").write_text("import java.util.*;\n\n" + "\n\n".join(parts) + "\n\n" + DIFF_DRIVER)
    subprocess.run(["javac", "-nowarn", "-d", str(tmp), str(tmp / "Diff.java")], check=True, capture_output=True)
    for n in names:
        p = subprocess.run(["java", "-Xss8m", "-cp", str(tmp), "Diff", n], capture_output=True, text=True, timeout=600)
        print(p.stdout.strip())
    shutil.rmtree(tmp)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dataset", type=Path, default=Path(tempfile.gettempdir()) / "QuixBugs")
    r = sub.add_parser("run")
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--budget", type=int, default=120)
    sub.add_parser("report")
    dt = sub.add_parser("difftest")
    dt.add_argument("names", nargs="*", default=DIFF_PROGRAMS)
    a = ap.parse_args()
    {"prepare": lambda: prepare(a.dataset), "run": lambda: run(a.jobs, a.budget), "report": report,
     "difftest": lambda: difftest(a.names)}[a.cmd]()


if __name__ == "__main__":
    main()
