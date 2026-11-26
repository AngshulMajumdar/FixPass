# FixPass for Java (`jfixpass`)

The Java half of [FixPass](../README.md): a repairing compiler for Java. It sits between the editor and `javac`/the JVM, takes an imperfect single-file Java program plus behavioural tests, and returns corrected, reviewable source and class files. Nothing is released unless every supplied test passes. There is no language model: every repair comes from the compiler and a test-driven search, and tied alternatives the tests cannot separate are listed in the report.

```
source.java ─► gate ─► javac parse ─► syntax repair ─► javac attribute ─► type/resolution repair ─► static rules
                                                                                                     │
       corrected.java + classes ◄─ final javac + all tests (unmetered) ◄─ coverage-ranked edit search ◄┘
```

## Install

From the repository root (see [the main README](../README.md)): `bash install.sh --only java` on Linux/macOS, `install.cmd` on Windows. You need JDK 17 or newer with `javac` on PATH; nothing else.

**Without installing:** `bash scripts/build.sh` builds `build/jfixpass.jar` (compiled with `-Xlint:all -Werror`), and `bin/jfixpass` runs it from this folder.

## Use

```bash
jfixpass Broken.java                                # repair until it compiles
jfixpass Broken.java --tests tests.json --out bin   # also repair against tests; classes into bin/
java -cp bin Broken
```

Try it on `examples/Broken.java` and `examples/broken_tests.json`.

The installed `jfixpass` command writes its results to `./jfixpass-out/<Source>/`, which is replaced on each run, unless you give `--work-dir DIR`. It prints where the corrected source, classes and report are. Options:

| Option | Meaning |
|---|---|
| `--tests FILE` | Behavioural tests, `[{"input": "...", "output": "..."}]` |
| `--extra-tests FILE` | More tests, appended |
| `--spec FILE` | Intent text, recorded in the report |
| `--out DIR` | Also copy the class files to DIR |
| `--work-dir DIR` | Where `original.java`, `candidate.java`, `corrected.java`, `classes/` and `report.json` go |

Stdout is compared after stripping leading and trailing whitespace, and the program must end without an uncaught exception. Without `--tests`, only the compiler stages run, and the result is reported as compiling but behaviourally unvalidated. `report.json` lists every diagnostic and the edit made for it (before and after), the tied alternatives, and any deleted code.

| Exit code | Meaning |
|---|---|
| 0 | Compiled; every supplied test passes. `corrected.java` and `classes/` are written. |
| 1 | Repair failed. Only `candidate.java` is written, for inspection. |
| 2 | Invalid input, or source outside the supported subset. |

## Engine

1. **Gate.** Single file, default package, console I/O, `java.base` data APIs only. See [docs/SUPPORTED_JAVA.md](docs/SUPPORTED_JAVA.md).
2. **Unchanged check.** If the input compiles and passes every test, it is returned byte-for-byte.
3. **Syntax repair: general, not pattern-based.** A least-cost token search with javac as the oracle runs at each parse error. It tries inserting any vocabulary token, deleting, replacing, swapping, splitting and joining tokens, plus lexical repairs for unclosed strings, chars and comments, escapes, numbers and confusable Unicode characters, and indentation-guided brace repair.
   - **Acceptance.** An edit is kept if the file parses, or the first error moves at least 8 tokens further on. Edits may not re-lex code outside their window.
   - **Ranking.** Cost plus the remaining errors; once the file parses, type errors count as well.
   - **Escalation.** Pairs of edits, then reported lossy excision as a last resort.
   - **Guarantee.** Every input ends parseable. Equally cheap alternatives are settled by type checking, then by the tests.
4. **Type and resolution repair** on `compiler.err.cant.resolve*`, `prob.found.req` and `missing.ret.stmt`, with the same token search as a fallback for any other compile error. It covers imports from an index of `java.base`, names repaired by edit distance against the scope and JDK members found by reflection, casts, `=`/`==`, field/method confusion, missing `new`, and missing returns. When several repairs remove an error equally well, the choice is recorded as a *hole* that the search can revisit with test evidence. Example: the missing `return "F"` in gradebook.
5. **Static rules.** `String` compared with `==` becomes a null-safe `equals`. With tests present, the edit is kept only if nothing regresses.
6. **Search** on the attributed javac AST:
   - **Ranking.** Edits are ranked by Ochiai suspiciousness from source-level statement coverage, plus a boost for statements on crash stack traces (an infinite loop shows up as a fuel-exhaustion trace).
   - **Typed operators.** Each edit type is applied only where types allow it (no `+` swap on String concatenation, variable swaps only between same-type locals).
   - **Scoring.** A passing test counts 2; a failing test counts its line-level LCS similarity, which gives partial credit when several faults interact.
   - **Acceptance.** An edit is kept only if it raises the score and no previously passing test fails.
   - **Backtracking.** When stuck, one earlier edit is undone, candidates are re-ranked on the undone program, and one new edit is tried on top. This undoes "disable the crashing feature" moves.
   - **Ties.** When several edits reach the same best score, they are run on amplified inputs, grouped by behaviour, and listed in the report.
7. **Validation.** A fresh compile of the unmetered program; all tests run in a fresh worker; signatures are checked against the first parseable version.

Test execution uses one long-lived worker JVM. Each test gets a fresh class loader, so static state is fresh. Termination is guaranteed by fuel counting (loop iterations and method entries) rather than by killing threads.

## Benchmark

Six console programs of 197–243 lines (inventory, gradebook, graph algorithms, matrix algebra, text tools, RPN calculator). The 48 documented faults are 19 compile errors and 29 behavioural faults (`benchmarks/cases/*/faults.json`). The engine sees 10 visible tests per case; 40 held-out tests per case are used only for scoring. Full tables are in [benchmarks/RESULTS.md](benchmarks/RESULTS.md).

| | Result |
|---|---|
| Visible tests after repair | 60/60 (6/6 programs) |
| Held-out tests | 227/240; 5 programs at 40/40, inventory at 27/40 |
| Faults | 40 restored exactly, 7 repaired equivalently, 1 not fixed |
| Compile faults (19) | 19/19 fixed by the compiler stages, no tests needed |
| Correct reference programs (controls) | 6/6 returned unchanged |
| Time | 34–125 s per program on 2 vCPU (375 s total) |

**Syntax stress test** (`scripts/stress_syntax.py`). 600 copies of the six programs with 1–5 random corruptions each: token deletion, insertion, replacement, swap and duplication, smart quotes and other confusable characters, unclosed strings and comments, single-quoted strings, stray characters, dropped line-final tokens. 570 of them fail to parse.

| Corruptions | Files | Parse after repair | Compile | Token-exact | Lossy |
|---|---|---|---|---|---|
| 1 | 150 | 150 | 150 | 128 | 4 |
| 2 | 153 | 153 | 148 | 72 | 26 |
| 3–5 | 267 | 267 | 248 | 63 | 107 |
| all | 570 | 570 (100%) | 546 (96%) | 263 | 137 |

Median 7 s per file, compile stages only. Full pipeline with the visible tests on 60 of the 1–2-corruption files: 48 pass all 40 held-out tests (27/29 with a single corruption). The failures are corruptions that delete content, such as a literal or an operand. The token sequence does not show what was there, so tests can only reject wrong guesses.

The fault that was not fixed (inventory-7, swapped comparator arguments) is exercised by only 1 of the 10 visible tests. A wrong edit (`if (c != 0)` → `if (c > 0)`) passes them too. The report shows 7 equal-score alternatives for that round, including the correct one. Stronger tests would separate them.

Reproduce (from the repository; the scripts use `bin/jfixpass` and `build/jfixpass.jar`, so run `bash scripts/build.sh` first):

```bash
python3 scripts/generate_tests.py      # tests from reference programs (deterministic seeds)
python3 scripts/inject_faults.py       # source.java + faults.json from reference.java
python3 scripts/check_faults.py        # each fault alone: compile error, or visible/held-out tests failing
python3 scripts/run_benchmark.py       # repair, held-out scoring, controls -> benchmarks/runs/
python3 scripts/write_results.py       # benchmarks/RESULTS.md
```

### Standard benchmarks

| Benchmark | FixPass | Best of 11 published Java repair tools (2 h per bug) | Union of all 11 |
|---|---|---|---|
| IntroClassJava, 297 student programs | 50 plausible of 258 failing; 31 also pass every held-out test | 32 | 62 |
| QuixBugs (Java), 40 algorithms | 13 plausible; 11 correct (7 identical to the reference fix, 4 equivalent on 2,000 random inputs) | 4 | 12 |

How these were run:
- **IntroClassJava.** Repair uses the blackbox suite; the whitebox suite is held out. The converted tests reproduce the dataset's published pass/fail table exactly.
- **QuixBugs.** Patches are validated by putting the repaired program back into the original, untouched test harness, and checked against the correct versions on random inputs.
- **Budget.** 120 s per bug for FixPass. Published numbers are from RepairThemAll (Durieux et al., ESEC/FSE 2019); its protocol differs, so treat the comparison as indicative.

Details: [benchmarks/RESULTS.md](benchmarks/RESULTS.md). Reproduce with `scripts/introclassjava.py` and `scripts/quixbugs.py`, which download the datasets.

## Scope

The layer repairs local faults, those a single operator, literal, identifier, argument-order or similar edit can fix (see the operator list in [docs/SUPPORTED_JAVA.md](docs/SUPPORTED_JAVA.md)). It does not invent missing statements or restructure algorithms. Passing tests is evidence, not proof: weak tests give overfitted repairs, and the report shows where the tests left the choice open. The worker JVM is resource-limited but is not a security sandbox; run untrusted code inside an OS-level sandbox.

License: Apache-2.0.
