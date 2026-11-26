#!/usr/bin/env python3
"""Stress test for the syntax stage: random token-level and lexical corruptions of the reference programs.

Stage A (all trials, compile stages only, in-process batch): does the repaired file parse, compile, and equal the
reference token-for-token? Stage B (sample, full pipeline with visible tests): exit code and held-out test pass rate.

Usage:
  python3 scripts/stress_syntax.py generate --trials 600
  python3 scripts/stress_syntax.py bench --part 0 --parts 2   (run each part, possibly in parallel)
  python3 scripts/stress_syntax.py full --n 60 --max-k 2 [--offset N]   (full pipeline with tests, held-out scoring)
  python3 scripts/stress_syntax.py report
"""
import argparse
import json
import random
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "cases"
OUT = ROOT / "benchmarks" / "syntax_stress"
JAR = ROOT / "build" / "jfixpass.jar"

TOKEN = re.compile(r'''\s+|//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|[A-Za-z_$][\w$]*|\d[\w.]*|>>>=|<<=|>>=|->|::|\+\+|--|&&|\|\||==|!=|<=|>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|.''', re.S)
VOCAB = list(";,.()[]{}=<>!?:+-*/%&|^~") + ["==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=", "->", "new", "return",
                                            "else", "if", "for", "int", "void", "static", "class", "this", "null", "while"]
CONFUSE = {'"': ["“", "”"], "-": ["−", "–"], ";": ["；"], "*": ["×"], "(": ["（"], ")": ["）"]}


def tokens(src):
    """(text, start, end) for significant tokens."""
    out = []
    for m in TOKEN.finditer(src):
        t = m.group(0)
        if t.isspace() or t.startswith("//") or t.startswith("/*"):
            continue
        out.append((t, m.start(), m.end()))
    return out


def sig(src):
    return [t for t, _, _ in tokens(src)]


def corrupt(src, r):
    toks = tokens(src)
    # skip the import block so corruptions land in code
    first = next(i for i, t in enumerate(toks) if t[0] in ("public", "class"))
    i = r.randrange(first, len(toks) - 1)
    t, s, e = toks[i]
    op = r.choices(["delete", "insert", "replace", "swap", "duplicate", "delete2", "confuse", "unclose_str", "char_lit",
                    "stray", "open_comment", "drop_line_end"],
                   [22, 18, 16, 8, 6, 6, 6, 5, 3, 4, 2, 4])[0]
    if op == "delete":
        return src[:s] + src[e:], f"delete '{t}'"
    if op == "insert":
        x = r.choice(VOCAB)
        return src[:s] + x + " " + src[s:], f"insert '{x}' before '{t}'"
    if op == "replace":
        x = r.choice([v for v in VOCAB if v != t])
        return src[:s] + x + src[e:], f"replace '{t}' with '{x}'"
    if op == "swap":
        t2, s2, e2 = toks[i + 1]
        return src[:s] + t2 + src[e:s2] + t + src[e2:], f"swap '{t}' '{t2}'"
    if op == "duplicate":
        return src[:e] + t + src[e:], f"duplicate '{t}'"
    if op == "delete2":
        t2, s2, e2 = toks[i + 1]
        return src[:s] + src[e2:], f"delete '{t} {t2}'"
    if op == "confuse":
        cands = [k for k, (tt, _, _) in enumerate(toks[first:], first) if tt in CONFUSE or tt.startswith('"')]
        k = r.choice(cands)
        tt, ss, ee = toks[k]
        if tt.startswith('"') and len(tt) >= 2:
            return src[:ss] + "“" + tt[1:-1] + "”" + src[ee:], f"smart quotes on {tt}"
        return src[:ss] + r.choice(CONFUSE[tt]) + src[ee:], f"confusable for '{tt}'"
    if op == "unclose_str":
        cands = [k for k, (tt, _, _) in enumerate(toks[first:], first) if tt.startswith('"') and len(tt) >= 2]
        k = r.choice(cands)
        tt, ss, ee = toks[k]
        return src[:ee - 1] + src[ee:], f"drop closing quote of {tt}"
    if op == "char_lit":
        cands = [k for k, (tt, _, _) in enumerate(toks[first:], first) if tt.startswith('"') and 3 <= len(tt) <= 8]
        if not cands:
            return src[:s] + src[e:], f"delete '{t}'"
        k = r.choice(cands)
        tt, ss, ee = toks[k]
        return src[:ss] + "'" + tt[1:-1] + "'" + src[ee:], f"string {tt} written with single quotes"
    if op == "stray":
        x = r.choice(["#", "`", "\\", " "])
        return src[:s] + x + src[s:], f"stray character U+{ord(x):04X}"
    if op == "open_comment":
        return src[:s] + "/* " + src[s:], "unterminated /* comment"
    # drop_line_end: delete the last token of a line (typically ';', '{' or ')')
    line_end = src.find("\n", s)
    ends = [k for k, (tt, ss, ee) in enumerate(toks) if k >= first and ee <= line_end and src.find("\n", ee) == line_end]
    k = ends[-1] if ends else i
    tt, ss, ee = toks[k]
    return src[:ss] + src[ee:], f"drop line-final '{tt}'"


def generate(n_trials, seed):
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "in").mkdir(parents=True)
    (OUT / "out").mkdir()
    (OUT / "parts").mkdir()
    r = random.Random(seed)
    cases = sorted(p.name for p in CASES.iterdir() if p.is_dir())
    refs = {c: (CASES / c / "reference.java").read_text() for c in cases}
    trials = []
    for n in range(n_trials):
        c = cases[n % len(cases)]
        k = r.choice([1, 1, 2, 2, 3, 4, 5])
        src = refs[c]
        ops = []
        for _ in range(k):
            src, d = corrupt(src, r)
            ops.append(d)
        name = f"t{n:04d}_{c}"
        (OUT / "in" / f"{name}.java").write_text(src)
        trials.append({"id": name, "case": c, "k": k, "corruptions": ops})
    (OUT / "trials.json").write_text(json.dumps(trials, indent=1) + "\n")
    print(f"generated {len(trials)} trials")


def bench(part, parts):
    trials = json.loads((OUT / "trials.json").read_text())
    mine = trials[part::parts]
    manifest = "\n".join(f"{OUT / 'in' / (t['id'] + '.java')}\t{OUT / 'out' / (t['id'] + '.java')}" for t in mine) + "\n"
    with open(OUT / "parts" / f"part{part}.jsonl", "w") as fh:
        p = subprocess.Popen(["java", "-Xss64m", "-cp", str(JAR), "jfixpass.SyntaxBench"], stdin=subprocess.PIPE,
                             stdout=fh, stderr=subprocess.DEVNULL, text=True)
        p.communicate(manifest)


def full(n_full, max_k, offset):
    trials = json.loads((OUT / "trials.json").read_text())
    res = load_results()
    refs = {t["case"]: (CASES / t["case"] / "reference.java").read_text() for t in trials}
    sample = [t for t in trials if t["id"] in res and not res[t["id"]]["parsed_before"] and t["k"] <= max_k][offset:offset + n_full]
    out = []
    for t in sample:
        case = CASES / t["case"]
        wd = OUT / "full" / t["id"]
        if wd.exists():
            shutil.rmtree(wd)
        q = subprocess.run([str(ROOT / "bin" / "jfixpass"), str(OUT / "in" / f"{t['id']}.java"), "--tests",
                            str(case / "visible_tests.json"), "--work-dir", str(wd)], capture_output=True, text=True)
        ok = q.returncode == 0
        held = 0
        val = json.loads((case / "validation_tests.json").read_text())
        if ok:
            tmp = Path(tempfile.mkdtemp())
            shutil.copy(wd / "corrected.java", tmp / "Main.java")
            subprocess.run(["javac", "-d", str(tmp), str(tmp / "Main.java")], capture_output=True)
            for v in val:
                o = subprocess.run(["java", "-XX:TieredStopAtLevel=1", "-cp", str(tmp), "Main"], input=v["input"],
                                   capture_output=True, text=True, timeout=10)
                held += o.returncode == 0 and o.stdout.strip() == v["output"].strip()
            shutil.rmtree(tmp)
        corrected = (wd / "corrected.java").read_text() if ok else ""
        rep = json.loads((wd / "report.json").read_text())
        out.append({"id": t["id"], "k": t["k"], "corruptions": t["corruptions"], "exit": q.returncode, "heldout": f"{held}/{len(val)}",
                    "token_exact": ok and sig(corrected) == sig(refs[t["case"]]), "lossy": bool(rep.get("lossy_syntax_repair")),
                    "seconds": rep.get("seconds"), "status": rep.get("status")})
        shutil.rmtree(wd / "classes", ignore_errors=True)
        (wd / "tests_all.json").unlink(missing_ok=True)
        print(json.dumps(out[-1]), flush=True)
    target = OUT / "full_pipeline.json"
    prev = json.loads(target.read_text()) if target.exists() else []
    have = {x["id"] for x in out}
    target.write_text(json.dumps([x for x in prev if x["id"] not in have] + out, indent=1) + "\n")


def load_results():
    res = {}
    for f in sorted((OUT / "parts").glob("part*.jsonl")):
        for line in f.read_text().splitlines():
            if line.startswith("{"):
                x = json.loads(line)
                res[Path(x["input"]).stem] = x
    return res


def report():
    trials = json.loads((OUT / "trials.json").read_text())
    res = load_results()
    refs = {t["case"]: (CASES / t["case"] / "reference.java").read_text() for t in trials}
    done = []
    for t in trials:
        if t["id"] not in res:
            continue
        t.update(res[t["id"]])
        t.pop("input", None)
        fixed = (OUT / "out" / f"{t['id']}.java").read_text()
        t["token_exact"] = sig(fixed) == sig(refs[t["case"]])
        done.append(t)
    errs = [t for t in done if not t["parsed_before"]]

    def agg(g):
        return {"n": len(g), "parse": sum(t["parses"] for t in g), "compiles": sum(t["compiles"] for t in g),
                "token_exact": sum(t["token_exact"] for t in g), "lossy": sum(t["lossy"] for t in g)}

    summary = {"trials_run": len(done), "with_syntax_error": len(errs), "corrupted_but_still_parsing": len(done) - len(errs),
               "all": agg(errs),
               "mean_seconds": round(sum(t["seconds"] for t in errs) / max(1, len(errs)), 2),
               "median_seconds": sorted(t["seconds"] for t in errs)[len(errs) // 2] if errs else 0,
               "max_seconds": max((t["seconds"] for t in errs), default=0),
               "by_corruption_count": {str(k): agg([t for t in errs if t["k"] == k]) for k in sorted({t["k"] for t in errs})},
               "by_case": {c: agg([t for t in errs if t["case"] == c]) for c in sorted({t["case"] for t in errs})}}
    if (OUT / "full_pipeline.json").exists():
        f = json.loads((OUT / "full_pipeline.json").read_text())
        summary["full_pipeline"] = {"n": len(f), "exit_0_all_visible_pass": sum(x["exit"] == 0 for x in f),
                                    "all_heldout_pass": sum(x["heldout"].split("/")[0] == x["heldout"].split("/")[1] for x in f),
                                    "token_exact": sum(x["token_exact"] for x in f), "lossy": sum(x["lossy"] for x in f)}
    (OUT / "results.json").write_text(json.dumps(done, indent=1) + "\n")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--trials", type=int, default=600)
    g.add_argument("--seed", type=int, default=2026)
    b = sub.add_parser("bench")
    b.add_argument("--part", type=int, default=0)
    b.add_argument("--parts", type=int, default=1)
    f = sub.add_parser("full")
    f.add_argument("--n", type=int, default=60)
    f.add_argument("--max-k", type=int, default=2)
    f.add_argument("--offset", type=int, default=0)
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "generate":
        generate(a.trials, a.seed)
    elif a.cmd == "bench":
        bench(a.part, a.parts)
    elif a.cmd == "full":
        full(a.n, a.max_k, a.offset)
    else:
        report()


if __name__ == "__main__":
    main()
