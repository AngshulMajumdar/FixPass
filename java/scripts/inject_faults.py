#!/usr/bin/env python3
"""Build each case's faulty source.java from reference.java and a documented fault list (faults.json).

Every fault is an exact, single-occurrence text patch on the reference, so each injected fault can be
located and checked after repair.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "benchmarks" / "cases"

# (category, description, old, new)
FAULTS = {
    "inventory": [
        ("compile", "missing semicolon", "it.qty -= qty;", "it.qty -= qty"),
        ("compile", "misspelled variable items -> item", "items.put(name, new Item(name, qty, price));", "item.put(name, new Item(name, qty, price));"),
        ("compile", "missing import java.util.Collections", "import java.util.Collections;\n", ""),
        ("compile", "assignment instead of comparison on a String (C habit)", 'if (cmd.equals("END")) {', 'if (cmd = "END") {'),
        ("semantic", "boundary: selling the whole stock rejected", "if (qty > it.qty) {", "if (qty >= it.qty) {"),
        ("semantic", "boundary: cents == 10 padded as 010", 'String f = frac < 10 ? "0" + frac', 'String f = frac <= 10 ? "0" + frac'),
        ("semantic", "comparator arguments swapped (ascending instead of descending)", "int c = Long.compare(b.value(), a.value());", "int c = Long.compare(a.value(), b.value());"),
        ("semantic", "wrong compound operator on net change", "it.netChange -= qty;", "it.netChange += qty;"),
    ],
    "gradebook": [
        ("compile", "missing final return", '        return "F";\n', ""),
        ("compile", "misspelled JDK method Math.min -> Math.mim", "lowest = Math.min(lowest, s.scores[idx]);", "lowest = Math.mim(lowest, s.scores[idx]);"),
        ("compile", "missing closing brace of inner loop", "                s.scores[i] = Math.min(100, s.scores[i] + points);\n            }\n", "                s.scores[i] = Math.min(100, s.scores[i] + points);\n"),
        ("semantic", "integer division in average", "return (double) total() / scores.length;", "return total() / scores.length;"),
        ("semantic", "grade boundary 80 excluded", "} else if (score >= 80.0) {", "} else if (score > 80.0) {"),
        ("semantic", "median parity test inverted", "if (copy.length % 2 == 1) {", "if (copy.length % 2 == 0) {"),
        ("semantic", "population instead of sample standard deviation", "return Math.sqrt(ss / (values.length - 1));", "return Math.sqrt(ss / values.length);"),
        ("semantic", "tie handling in ranking inverted", "if (i == 0 || w != prev) {", "if (i == 0 || w == prev) {"),
    ],
    "graph_engine": [
        ("compile", "missing semicolon", "Deque<Integer> q = new ArrayDeque<>();", "Deque<Integer> q = new ArrayDeque<>()"),
        ("compile", "lossy long -> int without cast", "int u = (int) top[1];", "int u = top[1];"),
        ("compile", "misspelled class name PriorityQueue", "PriorityQueue<Integer> ready = new PriorityQueue<>();", "PriortyQueue<Integer> ready = new PriorityQueue<>();"),
        ("semantic", "BFS visited test inverted", "if (dist[v] == -1) {", "if (dist[v] != -1) {"),
        ("semantic", "relaxation uses wrong endpoint", "long nd = dist[u] + e[1];", "long nd = dist[v] + e[1];"),
        ("semantic", "max-degree tie picks last node", "if (adj[u].size() > adj[best].size()) {", "if (adj[u].size() >= adj[best].size()) {"),
        ("semantic", "ternary branches swapped for directed/undirected weight", "return directed ? sum : sum / 2;", "return directed ? sum / 2 : sum;"),
        ("semantic", "off-by-one in cycle detection", "if (order.size() < n) {", "if (order.size() < n - 1) {"),
    ],
    "matrix_lab": [
        ("compile", "missing import java.util.HashMap", "import java.util.HashMap;\n", ""),
        ("compile", "array .length() called as a method", "for (int i = 0; i < Math.min(a.length, a[0].length); i++) {", "for (int i = 0; i < Math.min(a.length, a[0].length()); i++) {"),
        ("compile", "missing closing parenthesis", "if (a.length != b.length || a[0].length != b[0].length) {", "if (a.length != b.length || a[0].length != b[0].length {"),
        ("semantic", "inner dimension taken from wrong matrix", "int k = b.length;", "int k = a.length;"),
        ("semantic", "cofactor sign parity inverted", "long sign = (j % 2 == 0) ? 1 : -1;", "long sign = (j % 2 == 1) ? 1 : -1;"),
        ("semantic", "squaring step multiplies the wrong operand", "base = multiply(base, base);", "base = multiply(result, base);"),
        ("semantic", "accumulation replaced by assignment", "out[i] += a[i][j];", "out[i] = a[i][j];"),
        ("semantic", "String compared with == (reference equality)", 'if (cmd.equals("MUL")) {', 'if (cmd == "MUL") {'),
    ],
    "text_tools": [
        ("compile", "String length used as a field", "int j = s.length() - 1;", "int j = s.length - 1;"),
        ("compile", "misspelled method reverseWords -> reverseWord", "reverseWords(arg)", "reverseWord(arg)"),
        ("compile", "missing import java.io.IOException", "import java.io.IOException;\n", ""),
        ("semantic", "palindrome scan moves the right index the wrong way", "            i++;\n            j--;\n", "            i++;\n            j++;\n"),
        ("semantic", "upper-case Caesar offset uses 'a'", "('A' + (ch - 'A' + k) % 26)", "('A' + (ch - 'a' + k) % 26)"),
        ("semantic", "run-length counter starts at 0", "int run = 1;", "int run = 0;"),
        ("semantic", "lexicographic tie-break inverted", "w.compareTo(best) < 0", "w.compareTo(best) > 0"),
        ("semantic", "single-letter words counted as palindromes", "if (w.length() > 1 && isPalindrome(w)) {", "if (w.length() >= 1 && isPalindrome(w)) {"),
    ],
    "rpn_calculator": [
        ("compile", "operator typo => instead of >=", '"+-*/%^".indexOf(t.charAt(0)) >= 0;', '"+-*/%^".indexOf(t.charAt(0)) => 0;'),
        ("compile", "misspelled local variable", "evaluated++;", "evaluted++;"),
        ("compile", "missing new in throw", 'throw new IllegalArgumentException("malformed");', 'throw IllegalArgumentException("malformed");'),
        ("semantic", "precedence comparison inverted", "if (pt > pc || (pt == pc", "if (pt < pc || (pt == pc"),
        ("semantic", "operands passed in wrong order", "st.push(apply(t, a, b));", "st.push(apply(t, b, a));"),
        ("semantic", "power accumulator starts at 0", "long result = 1;", "long result = 0;"),
        ("semantic", "balance result negated", "return st.isEmpty();", "return !st.isEmpty();"),
        ("semantic", "digit scan reads wrong index", "Character.isDigit(expr.charAt(j))", "Character.isDigit(expr.charAt(i))"),
    ],
}


def main():
    total = 0
    for name, faults in FAULTS.items():
        case = CASES / name
        ref = (case / "reference.java").read_text()
        src = ref
        records = []
        for i, (cat, desc, old, new) in enumerate(faults, 1):
            if ref.count(old) != 1:
                raise SystemExit(f"{name} fault {i}: pattern occurs {ref.count(old)} times in reference")
            if src.count(old) != 1:
                raise SystemExit(f"{name} fault {i}: pattern no longer unique after earlier faults")
            line = ref[: ref.index(old)].count("\n") + 1
            src = src.replace(old, new)
            records.append({"id": f"{name}-{i}", "category": cat, "description": desc, "reference_line": line,
                            "original": old, "injected": new})
        (case / "source.java").write_text(src)
        (case / "faults.json").write_text(json.dumps(records, indent=1) + "\n")
        total += len(records)
        print(f"{name}: {len(records)} faults, {src.count(chr(10))} lines")
    print(f"total faults: {total}")


if __name__ == "__main__":
    main()
