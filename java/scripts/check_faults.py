#!/usr/bin/env python3
"""Benchmark sanity check: inject each fault alone and report whether it breaks compilation or is
observable by the visible tests and by the held-out validation tests."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "cases"


def run_tests(classdir, tests):
    failed = 0
    for t in tests:
        try:
            p = subprocess.run(["java", "-Xshare:auto", "-XX:TieredStopAtLevel=1", "-cp", str(classdir), "Main"],
                               input=t["input"], capture_output=True, text=True, timeout=5)
            ok = p.returncode == 0 and p.stdout.strip() == t["output"].strip()
        except subprocess.TimeoutExpired:
            ok = False
        failed += not ok
    return failed


def main():
    rows = []
    for case in sorted(CASES.iterdir()):
        ref = (case / "reference.java").read_text()
        vis = json.loads((case / "visible_tests.json").read_text())
        val = json.loads((case / "validation_tests.json").read_text())
        for f in json.loads((case / "faults.json").read_text()):
            tmp = Path(tempfile.mkdtemp())
            (tmp / "Main.java").write_text(ref.replace(f["original"], f["injected"]))
            c = subprocess.run(["javac", "-d", str(tmp), str(tmp / "Main.java")], capture_output=True, text=True)
            if c.returncode != 0:
                rows.append((f["id"], f["category"], "compile error", "-", "-"))
            else:
                rows.append((f["id"], f["category"], "compiles", f"{run_tests(tmp, vis)}/{len(vis)}", f"{run_tests(tmp, val)}/{len(val)}"))
            shutil.rmtree(tmp)
            print(*rows[-1], sep="\t", flush=True)
    (ROOT / "benchmarks" / "fault_observability.json").write_text(json.dumps(
        [dict(zip(["id", "category", "alone", "visible_failed", "validation_failed"], r)) for r in rows], indent=1) + "\n")


if __name__ == "__main__":
    main()
