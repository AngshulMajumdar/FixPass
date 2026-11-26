#!/usr/bin/env python3
"""Evaluate JFixPass on IntroClassJava (Durieux & Monperrus, 2016): 297 buggy student programs, two test suites.

Protocol (as in the IntroClass literature): repair with one suite, validate on the other, which is never shown to
the compiler. Default: repair with the blackbox suite, hold out the whitebox suite.

Adapter (no change to the compiler):
  * the `package introclassJava;` line is dropped (JFixPass takes default-package single files);
  * the benchmark's oracle compares output with every space removed after turning newlines into spaces, so the
    generated test driver in `main` (written by the IntroClassJava transpiler, not by the student) prints
    `output.replace("\\n", " ").trim().replace(" ", "")`, and expected outputs are normalized the same way;
  * each JUnit test (Scanner input string, expected string) becomes one stdin/stdout test.

Usage:
  python3 scripts/introclassjava.py prepare [--dataset DIR]   # clones the dataset if DIR is missing
  python3 scripts/introclassjava.py run [--jobs 2] [--budget 120] [--repair blackbox|whitebox]
  python3 scripts/introclassjava.py report
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks" / "introclassjava"
WORK = Path(tempfile.gettempdir()) / "jfixpass-introclassjava"
JAR = ROOT / "build" / "jfixpass.jar"

STR = r'"(?:\\.|[^"\\])*"'


def unescape(lit):
    body = lit[1:-1]
    return bytes(body, "utf-8").decode("unicode_escape") if "\\" in body else body


def concat(expr):
    return "".join(unescape(m) for m in re.findall(STR, expr))


def norm(s):
    return s.replace("\n", " ").strip().replace(" ", "")


def parse_tests(path):
    text = path.read_text(errors="replace")
    tests = []
    for m in re.finditer(r"@Test[^{]*\{(.*?)\n    \}", text, re.S):
        body = m.group(1)
        e = re.search(r"String expected\s*=\s*(.*?);", body, re.S)
        s = re.search(r"new java\.util\.Scanner\s*\((.*?)\)\s*;", body, re.S)
        if not e or not s:
            continue
        tests.append({"input": concat(s.group(1)) + "\n", "output": norm(concat(e.group(1)))})
    return tests


def adapt(src, cls):
    src = re.sub(r"^\s*package\s+introclassJava\s*;\s*\n", "", src, count=1, flags=re.M)
    driver = re.compile(r"System\.out\.println\s*\(\s*mainClass\.output\s*\)\s*;")
    if not driver.search(src):
        return None
    return driver.sub('System.out.println (mainClass.output.replace ("\\\\n", " ").trim ().replace (" ", ""));', src, count=1)


def prepare(dataset):
    if not dataset.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/Spirals-Team/IntroClassJava.git",
                        str(dataset.parent / "IntroClassJava")], check=True)
    ds = dataset / "dataset"
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    rows = []
    for mainf in sorted(ds.glob("*/*/*/src/main/java/introclassJava/*.java")):
        rel = mainf.relative_to(ds)
        assignment, user, version = rel.parts[0], rel.parts[1], rel.parts[2]
        cls = mainf.stem
        pid = f"{assignment}_{user[:8]}_{version}"
        tdir = mainf.parents[3] / "test" / "java" / "introclassJava"
        bb = parse_tests(tdir / f"{cls}BlackboxTest.java") if (tdir / f"{cls}BlackboxTest.java").exists() else []
        wb = parse_tests(tdir / f"{cls}WhiteboxTest.java") if (tdir / f"{cls}WhiteboxTest.java").exists() else []
        src = adapt(mainf.read_text(errors="replace"), cls)
        d = WORK / "programs" / pid
        d.mkdir(parents=True)
        row = {"id": pid, "assignment": assignment, "class": cls, "blackbox": len(bb), "whitebox": len(wb), "adapted": src is not None}
        if src is not None:
            (d / f"{cls}.java").write_text(src)
            (d / "blackbox.json").write_text(json.dumps(bb, indent=1))
            (d / "whitebox.json").write_text(json.dumps(wb, indent=1))
        rows.append(row)
    (WORK / "programs.json").write_text(json.dumps(rows, indent=1))
    print(f"prepared {len(rows)} programs ({sum(r['adapted'] for r in rows)} adapted) in {WORK}")


def run_tests(java_file, cls, tests):
    """Compile and run a program against tests; returns number passed."""
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(java_file, tmp / f"{cls}.java")
    c = subprocess.run(["javac", "-nowarn", "-d", str(tmp), str(tmp / f"{cls}.java")], capture_output=True, text=True)
    if c.returncode != 0:
        shutil.rmtree(tmp)
        return 0
    ok = 0
    for t in tests:
        try:
            p = subprocess.run(["java", "-XX:TieredStopAtLevel=1", "-cp", str(tmp), cls], input=t["input"], capture_output=True,
                               text=True, timeout=5)
            ok += p.returncode == 0 and p.stdout.strip() == t["output"].strip()
        except subprocess.TimeoutExpired:
            pass
    shutil.rmtree(tmp)
    return ok


def run_one(row, repair, budget):
    d = WORK / "programs" / row["id"]
    res_file = WORK / "results" / f"{row['id']}.json"
    if res_file.exists():
        return json.loads(res_file.read_text())
    held = "whitebox" if repair == "blackbox" else "blackbox"
    src = d / f"{row['class']}.java"
    rt = json.loads((d / f"{repair}.json").read_text())
    ht = json.loads((d / f"{held}.json").read_text())
    before_r = run_tests(src, row["class"], rt)
    before_h = run_tests(src, row["class"], ht)
    wd = WORK / "runs" / row["id"]
    if wd.exists():
        shutil.rmtree(wd)
    t0 = time.time()
    p = subprocess.run(["java", "-Xss64m", "-XX:+UseSerialGC", f"-Djfixpass.timeBudgetSec={budget}", "-jar", str(JAR), str(src),
                        "--tests", str(d / f"{repair}.json"), "--work-dir", str(wd)], capture_output=True, text=True)
    secs = time.time() - t0
    rep = json.loads((wd / "report.json").read_text()) if (wd / "report.json").exists() else {}
    out = {"id": row["id"], "assignment": row["assignment"], "repair_suite": repair, "repair_tests": len(rt), "heldout_tests": len(ht),
           "before_repair_pass": before_r, "before_heldout_pass": before_h, "exit": p.returncode, "status": rep.get("status"),
           "seconds": round(secs, 1), "changed": rep.get("changed"), "unchanged_original_passes": bool(rep.get("original_passes"))}
    if p.returncode == 0:
        out["after_heldout_pass"] = run_tests(wd / "corrected.java", row["class"], ht)
        edits = []
        for r in rep.get("search") or []:
            for e in r.get("edits", []):
                edits.append(f"L{int(e['line'])} {e['operator']}: {e['before']} => {e['after']}")
        for c in rep.get("compile_repairs") or []:
            edits.append(f"L{int(c['line'])} compile: {c['rule']}")
        for s in rep.get("static_rules") or []:
            if s.get("applied"):
                edits.append(f"L{int(s['line'])} static: {s['rule']}")
        out["edits"] = edits
    (WORK / "results").mkdir(exist_ok=True)
    res_file.write_text(json.dumps(out, indent=1))
    shutil.rmtree(wd / "classes", ignore_errors=True)
    return out


def run(jobs, budget, repair):
    rows = [r for r in json.loads((WORK / "programs.json").read_text()) if r["adapted"] and r["blackbox"] and r["whitebox"]]
    done = 0
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(lambda row: run_one(row, repair, budget), rows):
            done += 1
            print(f"[{done}/{len(rows)}] {r['id']} exit={r['exit']} repair={r['before_repair_pass']}/{r['repair_tests']} "
                  f"heldout {r['before_heldout_pass']}->{r.get('after_heldout_pass', '-')}/{r['heldout_tests']} {r['seconds']}s", flush=True)


def report():
    rows = json.loads((WORK / "programs.json").read_text())
    res = [json.loads(f.read_text()) for f in sorted((WORK / "results").glob("*.json"))]
    buggy = [r for r in res if r["before_repair_pass"] < r["repair_tests"]]
    already = [r for r in res if r["before_repair_pass"] == r["repair_tests"]]
    plaus = [r for r in buggy if r["exit"] == 0]
    gen = [r for r in plaus if r["after_heldout_pass"] == r["heldout_tests"]]
    summary = {
        "programs": len(rows), "adapted": sum(r["adapted"] for r in rows), "evaluated": len(res),
        "repair_suite": res[0]["repair_suite"] if res else None,
        "already_pass_repair_suite": len(already),
        "fail_repair_suite": len(buggy),
        "plausible_patch": len(plaus),
        "plausible_and_pass_all_heldout": len(gen),
        "heldout_tests_passed_before": sum(r["before_heldout_pass"] for r in plaus),
        "heldout_tests_passed_after": sum(r["after_heldout_pass"] for r in plaus),
        "heldout_tests_total_for_plausible": sum(r["heldout_tests"] for r in plaus),
        "median_seconds": sorted(r["seconds"] for r in buggy)[len(buggy) // 2] if buggy else 0,
        "by_assignment": {},
    }
    for a in sorted({r["assignment"] for r in res}):
        g = [r for r in buggy if r["assignment"] == a]
        p = [r for r in g if r["exit"] == 0]
        summary["by_assignment"][a] = {"fail_repair_suite": len(g), "plausible": len(p),
                                       "pass_all_heldout": sum(r["after_heldout_pass"] == r["heldout_tests"] for r in p)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(res, indent=1) + "\n")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--dataset", type=Path, default=Path(tempfile.gettempdir()) / "IntroClassJava")
    r = sub.add_parser("run")
    r.add_argument("--jobs", type=int, default=2)
    r.add_argument("--budget", type=int, default=120)
    r.add_argument("--repair", choices=["blackbox", "whitebox"], default="blackbox")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "prepare":
        prepare(a.dataset)
    elif a.cmd == "run":
        run(a.jobs, a.budget, a.repair)
    else:
        report()


if __name__ == "__main__":
    main()
