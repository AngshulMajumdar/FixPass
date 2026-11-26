#!/usr/bin/env python3
"""Run JFixPass on every benchmark case and score the result.

Repair sees only visible_tests.json. Scoring uses validation_tests.json (never shown to the engine), a per-fault
comparison against the documented injected faults, and a control run on the correct reference program.
Usage: python3 scripts/run_benchmark.py [case ...]
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "cases"
RUNS = ROOT / "benchmarks" / "runs"
CLI = ROOT / "bin" / "jfixpass"


def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def run_engine(src, tests, work):
    if work.exists():
        shutil.rmtree(work)
    t = time.time()
    p = subprocess.run([str(CLI), str(src), "--tests", str(tests), "--work-dir", str(work)], capture_output=True, text=True)
    return p.returncode, time.time() - t, json.loads((work / "report.json").read_text())


def heldout(java_src, tests):
    tmp = Path(tempfile.mkdtemp())
    (tmp / "Main.java").write_text(java_src)
    c = subprocess.run(["javac", "-d", str(tmp), str(tmp / "Main.java")], capture_output=True, text=True)
    if c.returncode != 0:
        shutil.rmtree(tmp)
        return 0
    ok = 0
    for t in tests:
        try:
            p = subprocess.run(["java", "-XX:TieredStopAtLevel=1", "-cp", str(tmp), "Main"], input=t["input"],
                               capture_output=True, text=True, timeout=10)
            ok += p.returncode == 0 and p.stdout.strip() == t["output"].strip()
        except subprocess.TimeoutExpired:
            pass
    shutil.rmtree(tmp)
    return ok


def classify(fault, corrected):
    c = norm(corrected)
    orig, inj = norm(fault["original"]), norm(fault["injected"])
    if fault["original"].startswith("import "):
        return "restored" if orig in c else "not restored"
    if inj and inj in c and orig not in c:
        return "untouched"
    if orig in c:
        return "restored"
    return "changed differently"


def main():
    names = sys.argv[1:] or sorted(p.name for p in CASES.iterdir() if p.is_dir())
    RUNS.mkdir(parents=True, exist_ok=True)
    summary = []
    for name in names:
        case = CASES / name
        val = json.loads((case / "validation_tests.json").read_text())
        faults = json.loads((case / "faults.json").read_text())
        code, secs, rep = run_engine(case / "source.java", case / "visible_tests.json", RUNS / name)
        out = RUNS / name / ("corrected.java" if code == 0 else "candidate.java")
        corrected = out.read_text() if out.exists() else ""
        per_fault = [{"id": f["id"], "category": f["category"], "description": f["description"],
                      "outcome": classify(f, corrected)} for f in faults]
        val_ok = heldout(corrected, val) if corrected else 0
        ccode, csecs, crep = run_engine(case / "reference.java", case / "visible_tests.json", RUNS / (name + "_control"))
        control_unchanged = ccode == 0 and (RUNS / (name + "_control") / "corrected.java").read_text() == (case / "reference.java").read_text()
        row = {
            "case": name,
            "lines": (case / "source.java").read_text().count("\n"),
            "exit_code": code,
            "status": rep.get("status"),
            "seconds": round(secs, 1),
            "visible_passing": rep.get("final_passing"),
            "validation_passing": f"{val_ok}/{len(val)}",
            "faults": per_fault,
            "compile_repairs": len(rep.get("compile_repairs") or []),
            "search_edits": sum(len(r.get("edits", [])) for r in (rep.get("search") or []) if "edits" in r),
            "candidates_evaluated": rep.get("candidates_evaluated"),
            "javac_invocations": rep.get("javac_invocations"),
            "control_unchanged": control_unchanged,
            "control_seconds": round(csecs, 1),
        }
        summary.append(row)
        restored = sum(f["outcome"] == "restored" for f in per_fault)
        print(f"{name:15s} exit={code} visible={row['visible_passing']} heldout={row['validation_passing']} "
              f"restored={restored}/{len(per_fault)} {secs:.0f}s control_unchanged={control_unchanged}", flush=True)
        for f in per_fault:
            if f["outcome"] != "restored":
                print(f"    {f['id']}: {f['outcome']} ({f['description']})", flush=True)
    target = RUNS / "summary.json"
    merged = {r["case"]: r for r in (json.loads(target.read_text()) if target.exists() else [])}
    merged.update({r["case"]: r for r in summary})
    target.write_text(json.dumps([merged[k] for k in sorted(merged)], indent=1) + "\n")


if __name__ == "__main__":
    main()
