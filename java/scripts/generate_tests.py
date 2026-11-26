#!/usr/bin/env python3
"""Generate visible and held-out tests for each benchmark case from its reference program.

Expected outputs come from running the reference Java program; the faulty program is never run here.
Usage: python3 scripts/generate_tests.py [--visible N] [--validation N]
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "cases"

NAMES = ["apple", "bolt", "cable", "drill", "epoxy", "fuse", "gear", "hinge"]


def gen_inventory(r):
    lines = []
    stock = {}
    for _ in range(r.randint(18, 34)):
        op = r.choices(["ADD", "SELL", "RESTOCK", "PRICE", "REPORT", "TOTAL", "TOP", "LOW", "HISTORY", "ERRORS", "BAD"],
                       [6, 6, 3, 3, 2, 2, 2, 2, 3, 1, 0.3])[0]
        known = [n for n in stock]
        nm = r.choice(known) if known and r.random() < 0.75 else r.choice(NAMES)
        if op == "ADD":
            q = r.choice([0, -1, 5, 10, 20, r.randint(1, 40)])
            lines.append(f"ADD {nm} {q} {r.choice([0, 5, 110, 1210, 2410, r.randint(1, 2500)])}")
            if q > 0 and nm not in stock:
                stock[nm] = q
        elif op == "SELL":
            have = stock.get(nm, 0)
            q = r.choice([have, have, max(1, have // 2), have + 1, r.randint(1, 45)]) if have else r.randint(1, 45)
            q = max(q, 1)
            lines.append(f"SELL {nm} {q}")
            if nm in stock and q <= have:
                stock[nm] = have - q
        elif op == "RESTOCK":
            q = r.randint(1, 30)
            lines.append(f"RESTOCK {nm} {q}")
            if nm in stock:
                stock[nm] += q
        elif op == "PRICE":
            lines.append(f"PRICE {nm} {r.choice([310, r.randint(0, 2500)])}")
        elif op == "TOP":
            lines.append(f"TOP {r.randint(1, 6)}")
        elif op == "LOW":
            lines.append(f"LOW {r.randint(1, 35)}")
        elif op == "HISTORY":
            lines.append(f"HISTORY {nm}")
        elif op == "BAD":
            lines.append("FROB")
        else:
            lines.append(op)
    lines += ["REPORT", "TOTAL", "TOP 3", "ERRORS"] + [f"HISTORY {n}" for n in list(stock)[:2]]
    if r.random() < 0.5:
        lines += ["END", "REPORT"]
    return "\n".join(lines) + "\n"


def gen_gradebook(r):
    m = r.randint(2, 5)
    weights = [r.randint(0, 5) for _ in range(m)]
    if sum(weights) == 0:
        weights[0] = 1
    n = r.randint(1, 8)
    out = [str(m), " ".join(map(str, weights)), str(n)]
    pool = [100, 90, 89, 80, 79, 70, 60, 59, 0]
    prev = None
    for i in range(n):
        if prev is not None and r.random() < 0.2:
            scores = list(prev)
        elif r.random() < 0.25:
            scores = [r.choice([90, 80, 70, 60])] * m
        else:
            scores = [r.choice(pool) if r.random() < 0.3 else r.randint(35, 100) for _ in range(m)]
        prev = scores
        out.append(f"S{i + 1:02d} {r.choice(['ana', 'bo', 'chen', 'dev', 'eli', 'fay', 'gus', 'hal'])} " + " ".join(map(str, scores)))
    cmds = []
    for _ in range(r.randint(6, 12)):
        op = r.choice(["SHOW", "ALL", "RANK", "DIST", "STATS", "CURVE", "SHOW", "STATS"])
        if op == "SHOW":
            cmds.append(f"SHOW S{r.randint(1, n + 1):02d}")
        elif op == "STATS":
            cmds.append(f"STATS {r.randint(-1, m)}")
        elif op == "CURVE":
            cmds.append(f"CURVE {r.randint(0, 12)}")
        else:
            cmds.append(op)
    cmds += ["ALL", "RANK", "DIST", f"STATS {r.randint(0, m - 1)}"]
    return "\n".join(out + cmds) + "\n"


def gen_graph(r):
    n = r.randint(1, 9)
    m = r.randint(0, 16)
    d = r.choice(["D", "U"])
    edges = []
    for _ in range(m):
        if r.random() < 0.08:
            edges.append(f"{r.randint(0, n + 2)} {n + r.randint(0, 2)} 3")
        else:
            u = r.randrange(n)
            v = r.randrange(n)
            if d == "D" and r.random() < 0.7:
                u, v = min(u, v), max(u, v)
                if u == v:
                    continue
            edges.append(f"{u} {v} {r.randint(1, 20)}")
    cmds = []
    for _ in range(r.randint(5, 10)):
        op = r.choice(["BFS", "DIJ", "COMP", "TOPO", "MAXDEG", "WEIGHT", "BFS", "DIJ"])
        cmds.append(f"{op} {r.randrange(n)}" if op in ("BFS", "DIJ") else op)
    cmds += ["COMP", "TOPO", "MAXDEG", "WEIGHT", f"DIJ {r.randrange(n)}"]
    return "\n".join([f"{n} {len(edges)} {d}"] + edges + cmds) + "\n"


def gen_matrix(r):
    names = []
    lines = []
    shapes = {}

    def define(name, rows, cols):
        vals = [[r.randint(-9, 9) for _ in range(cols)] for _ in range(rows)]
        lines.append(f"DEF {name} {rows} {cols} " + " ".join(str(v) for row in vals for v in row))
        shapes[name] = (rows, cols)
        names.append(name)

    for i in range(r.randint(3, 5)):
        k = r.randint(1, 4)
        if r.random() < 0.6:
            define(f"M{i}", k, k)
        else:
            define(f"M{i}", k, r.randint(1, 4))
    if r.random() < 0.6:
        a = r.choice(names)
        rr, cc = shapes[a]
        b = f"S{len(names)}"
        vals = [[0] * rr for _ in range(rr)]
        for x in range(rr):
            for y in range(x, rr):
                vals[x][y] = vals[y][x] = r.randint(-5, 5)
        lines.append(f"DEF {b} {rr} {rr} " + " ".join(str(v) for row in vals for v in row))
        shapes[b] = (rr, rr)
        names.append(b)
    for j in range(r.randint(5, 10)):
        op = r.choice(["MUL", "ADD", "SUB", "T", "POW", "INFO", "INFO", "MUL"])
        dst = f"R{j}"
        a = r.choice(names + (["nope"] if r.random() < 0.05 else []))
        if op in ("MUL", "ADD", "SUB"):
            if a != "nope" and r.random() < 0.75:
                ra, ca = shapes[a]
                want = [x for x in names if (shapes[x][0] == ca if op == "MUL" else shapes[x] == (ra, ca))]
                b = r.choice(want) if want else r.choice(names)
            else:
                b = r.choice(names)
            lines.append(f"{op} {dst} {a} {b}")
            if a != "nope" and b in shapes:
                ra, ca = shapes[a]
                rb, cb = shapes[b]
                ok = (ca == rb) if op == "MUL" else ((ra, ca) == (rb, cb))
                if ok:
                    shapes[dst] = (ra, cb) if op == "MUL" else (ra, ca)
                    names.append(dst)
        elif op == "T":
            lines.append(f"T {dst} {a}")
            if a in shapes:
                shapes[dst] = (shapes[a][1], shapes[a][0])
                names.append(dst)
        elif op == "POW":
            lines.append(f"POW {dst} {a} {r.randint(0, 5)}")
            if a in shapes and shapes[a][0] == shapes[a][1]:
                shapes[dst] = shapes[a]
                names.append(dst)
        else:
            lines.append(f"INFO {a}")
    for x in names[:4]:
        lines.append(f"INFO {x}")
    return "\n".join(lines) + "\n"


WORDS = ["level", "noon", "data", "Racecar", "kayak", "java", "stack", "a", "I", "madam", "sort", "tree", "tree", "x1x",
         "abba", "Graph", "node", "deed", "loop", "civic", "array", "queue", "map", "map", "list"]


def sentence(r, k):
    out = []
    for _ in range(k):
        w = r.choice(WORDS)
        if r.random() < 0.15:
            w = w.upper()
        out.append(w + (r.choice([",", ".", "!", "", "", ""])))
    return " ".join(out)


def gen_text(r):
    lines = []
    for _ in range(r.randint(8, 16)):
        op = r.choice(["TEXT", "TEXT", "PAL", "CAESAR", "RLE", "REV", "ANA", "LONGEST", "FREQ", "TEXT"])
        if op == "TEXT":
            lines.append("TEXT " + sentence(r, r.randint(1, 9)))
        elif op == "PAL":
            lines.append("PAL " + sentence(r, r.randint(1, 8)))
        elif op == "CAESAR":
            lines.append(f"CAESAR {r.randint(-30, 30)} " + sentence(r, r.randint(1, 5)))
        elif op == "RLE":
            s = "".join(r.choice("aabbbcz") * r.randint(1, 4) for _ in range(r.randint(1, 6)))
            lines.append("RLE " + s)
        elif op == "REV":
            lines.append("REV " + sentence(r, r.randint(1, 6)))
        elif op == "ANA":
            a = r.choice(["listen", "Dormitory", "binary", "evil", "state"])
            b = r.choice(["silent", "dirtyroom", "brainy", "vile", "taste", "tastes", "listens"])
            lines.append(f"ANA {a} {b}")
        elif op == "LONGEST":
            lines.append("LONGEST")
        else:
            lines.append(f"FREQ {r.randint(1, 5)}")
        if r.random() < 0.1:
            lines.append("")
    lines += ["LONGEST", f"FREQ {r.randint(2, 6)}"]
    return "\n".join(lines) + "\n"


def expr(r, depth, varnames):
    if depth <= 0 or r.random() < 0.3:
        if varnames and r.random() < 0.3:
            return r.choice(varnames)
        return str(r.randint(0, 20))
    op = r.choice(["+", "-", "*", "/", "%", "^", "+", "-", "*"])
    a = expr(r, depth - 1, varnames)
    b = expr(r, depth - 1, varnames)
    if op == "^":
        b = str(r.randint(0, 4))
    e = f"{a} {op} {b}"
    return f"({e})" if r.random() < 0.4 else e


def gen_rpn(r):
    lines = []
    varnames = []
    for _ in range(r.randint(8, 16)):
        op = r.choice(["LET", "EVAL", "EVAL", "EVAL", "POSTFIX", "CHECK", "SUMMARY"])
        if op == "LET":
            nm = r.choice(["x", "y", "rate", "n2"])
            lines.append(f"LET {nm} = {expr(r, 2, varnames)}")
            if nm not in varnames:
                varnames.append(nm)
        elif op == "EVAL":
            e = expr(r, 3, varnames)
            roll = r.random()
            if roll < 0.06:
                e = e + " )"
            elif roll < 0.12:
                e = "( " + e
            elif roll < 0.16:
                e = e + " + zz"
            elif roll < 0.2:
                e = e + " / 0"
            lines.append(f"EVAL {e}")
        elif op == "POSTFIX":
            lines.append(f"POSTFIX {expr(r, 3, varnames)}")
        elif op == "CHECK":
            s = "".join(r.choice("()[]{}a") for _ in range(r.randint(0, 10)))
            lines.append(f"CHECK {s}")
        else:
            lines.append("SUMMARY")
    lines += ["EVAL 2 ^ 3 ^ 2", "POSTFIX 2 ^ 3 ^ 2", "SUMMARY"]
    return "\n".join(lines) + "\n"


GENERATORS = {
    "inventory": gen_inventory,
    "gradebook": gen_gradebook,
    "graph_engine": gen_graph,
    "matrix_lab": gen_matrix,
    "text_tools": gen_text,
    "rpn_calculator": gen_rpn,
}


def run_reference(classdir, inp):
    p = subprocess.run(["java", "-Xshare:auto", "-cp", str(classdir), "Main"], input=inp, capture_output=True, text=True, timeout=20)
    if p.returncode != 0:
        raise RuntimeError(f"reference failed: {p.stderr}\ninput:\n{inp}")
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--visible", type=int, default=10)
    ap.add_argument("--validation", type=int, default=40)
    args = ap.parse_args()
    for name, gen in GENERATORS.items():
        case = CASES / name
        tmp = Path(tempfile.mkdtemp(prefix="jref_"))
        shutil.copy(case / "reference.java", tmp / "Main.java")
        subprocess.run(["javac", "-d", str(tmp), str(tmp / "Main.java")], check=True, capture_output=True)
        for kind, count, seed in (("visible_tests", args.visible, 1000), ("validation_tests", args.validation, 9000)):
            r = random.Random(f"{name}-{seed}")
            tests = []
            seen = set()
            while len(tests) < count:
                inp = gen(r)
                if inp in seen:
                    continue
                seen.add(inp)
                tests.append({"input": inp, "output": run_reference(tmp, inp)})
            (case / f"{kind}.json").write_text(json.dumps(tests, indent=1) + "\n")
        shutil.rmtree(tmp)
        print(f"{name}: {args.visible} visible, {args.validation} validation", file=sys.stderr)


if __name__ == "__main__":
    main()
