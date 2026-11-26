# FixPass for C (`cfixpass`)

The C half of [FixPass](../README.md): a repairing compiler for C.

It sits between the editor and the C compiler. Give it an imperfect single-file C program, plus behavioural tests if you have them. It returns corrected, reviewable source and a compiled program. Nothing is released unless the program compiles and every supplied test passes.

There is no language model and no GPU. Every repair comes from the compiler (clang, used in-process through libclang) and a test-driven search, and it runs on any laptop.

```
source.c ─► gate ─► clang parse ─► syntax repair ─► clang analysis ─► semantic repair ─► warning rules
                                                                                            │
     corrected.c + program ◄─ final compile + all tests (uninstrumented) ◄─ coverage-ranked edit search ◄┘
```

## Install

From the repository root (see [the main README](../README.md)): `bash install.sh --only c`. You need Python 3.9 or newer and gcc or clang; the installer fetches one Python package, `libclang`. Linux and macOS (Windows: use WSL).

## Use

```bash
cfixpass broken.c                                # repair until it compiles
cfixpass broken.c --tests tests.json -o prog     # also repair against tests; the program is copied to ./prog
```

Try it on `examples/broken.c` and `examples/broken_tests.json`.

Results go to `./cfixpass-out/<source>/`, which is replaced on each run, unless you give `--work-dir DIR`. Options:

| Option | Meaning |
|---|---|
| `--tests FILE` | Behavioural tests, `[{"input": "...", "output": "..."}]` |
| `--extra-tests FILE` | More tests, appended |
| `--spec FILE` | Intent text, recorded in the report |
| `-o, --out FILE` | Also copy the compiled program to FILE |
| `--work-dir DIR` | Where `original.c`, `candidate.c`, `corrected.c`, `program` and `report.json` go |
| `--time-budget SEC` | Time for the whole repair (default 1800) |

**Tests.** Outputs are compared as `diff -ZB` compares them: trailing whitespace and blank lines are ignored. A test may give `output_regex` instead of `output` (the regex is searched in the output), and may require an `exit_code`. A crash, a timeout or running out of fuel fails the test.

**Without `--tests`.** Only the compile stages run, plus warning rules that remove undefined behaviour without choosing between meanings (a missing `&` in `scanf`, a format specifier that does not match its argument, an uninitialized local). The result is reported as compiling but behaviourally unvalidated.

**The report.** `report.json` lists every diagnostic and the edit made for it (before and after), the tied alternatives, and any deleted code.

| Exit code | Meaning |
|---|---|
| 0 | Compiled; every supplied test passes. `corrected.c` and `program` are written. |
| 1 | Repair failed. Only `candidate.c` is written, for inspection. |
| 2 | Invalid input, or source outside the supported subset. |

## Engine

The design is the same as the Java half's, with clang in place of javac.

1. **Gate.** A single file, standard headers, console I/O only. See [docs/SUPPORTED_C.md](docs/SUPPORTED_C.md).
2. **Unchanged check.** If the input compiles and passes every test, it is returned byte-for-byte.
3. **Syntax repair: general, not pattern-based.** A least-cost token search with clang as the oracle runs at each parse error.
   - **Candidates.** Inserting any vocabulary token, deleting, replacing, swapping, splitting and joining tokens. Lexical repairs for unclosed strings, chars and comments, numbers and confusable Unicode characters. Indentation-guided brace repair.
   - **Acceptance.** An edit is kept if the file parses, or the first error moves at least 8 tokens further on. Edits may not re-lex code outside their window. An edit that creates a new error before the original error site is rejected, because clang hides errors behind a semantic error in the same statement.
   - **Ranking.** Cost plus the remaining errors. Once the file parses, semantic errors count as well.
   - **Escalation.** Pairs of edits, then reported lossy excision as a last resort.
   - **Guarantee.** Every input ends parseable.
4. **Semantic repair** from clang's diagnostics and fix-its:
   - missing `#include`s, from an index of the standard headers;
   - misspelled names, checked against what is declared and the standard library;
   - prototypes for functions defined later;
   - declarations for undeclared variables;
   - `return;` in non-void functions;
   - missing or extra `&`/`*`.

   The same token search is the fallback for any other error. When several repairs remove an error equally well, the choice is recorded as a *hole* that the search can revisit with test evidence.
5. **Warning rules.** In C, many real bugs compile with only a warning:
   - a `scanf` argument without `&`;
   - a mismatched format specifier;
   - `=` in a condition;
   - a `;` that empties an `if` or loop body;
   - an uninitialized local;
   - a missing return value.

   Each warning proposes edits (mostly clang's own fix-its). With tests, an edit is kept only if nothing regresses.
6. **Search** on clang's typed AST:
   - **Ranking.** Edits are ranked by Ochiai suspiciousness from source-level statement coverage, plus a boost for the statement where a failing test crashed or ran out of fuel.
   - **Typed operators.** Each edit type is applied only where the types allow it.
   - **Scoring.** A passing test counts 2. A failing test counts its line-level LCS similarity, which gives partial credit when several faults interact.
   - **Acceptance.** An edit is kept only if it raises the score and no previously passing test fails.
   - **Backtracking.** When stuck, one earlier edit is undone, candidates are re-ranked on the undone program, and one new edit is tried on top.
   - **Ties.** When several edits reach the same best score, they are run on amplified inputs, grouped by behaviour, and listed in the report.

   Candidates are compiled and run in parallel, one per CPU core.
7. **Validation.** A fresh build of the uninstrumented program, all tests, and signatures checked against the version after the compile stages.

**Test execution.** Each test runs as a separate process with CPU, memory and output limits. Termination is guaranteed by fuel counting (loop iterations and function entries), with a wall clock as backstop. Uninitialized locals are zero-initialized (`-ftrivial-auto-var-init=zero`), so programs that read them still behave deterministically.

## Benchmarks

Two standard C program-repair benchmarks, both with published baselines. The budget is 120 s per bug on 2 vCPU, with no model. Full tables are in [benchmarks/RESULTS.md](benchmarks/RESULTS.md).

**IntroClass** (Le Goues et al., TSE 2015): student solutions to six assignments, each with a black-box and a white-box test suite. One suite is used for repair and the other is held out.

| | FixPass | GenProg | TrpAutoRepair | AE | SearchRepair |
|---|---|---|---|---|---|
| Black-box suite: plausible patches | **204 / 532 (38.3%)** | 287 / 778 (36.9%) | 247 / 778 (31.7%) | 159 / 778 (20.4%) | 150 / 778 (19.3%) |
| White-box suite: plausible patches | **311 / 606 (51.3%)** | 292 / 845 (34.6%) | 212 / 845 (25.1%) | 166 / 845 (19.6%) | – |
| Held-out tests passed by the patches (mean) | **90.6%** | 68.7% | 72.1% | 64.2% | 97.3% |
| Patches passing every held-out test | 108 (black-box), 155 (white-box) | not reported | not reported | not reported | not reported |
| Time | median 17 s, one run | 20 seeds per defect | 20 seeds | 25 min limit | |

How the IntroClass run was set up:
- **Oracles.** The benchmark's fuzzy output checkers were converted to regular expressions. On the deterministic submissions they reproduce 14,027 of 14,058 recorded verdicts (`benchmarks/introclass/oracle_validation.json`).
- **Defects.** Only deterministic submissions that fail at least one test of the repair suite and pass at least one were attempted.
- **Baselines.** Published counts come from Le Goues et al. 2015; the held-out rates come from Smith et al. (FSE 2015) and Ke et al. (ASE 2015). Those counts use a slightly different defect set (the 2015 release, nondeterministic submissions included), so compare rates.

**ITSP** (Yi et al., ESEC/FSE 2017): 661 incorrect CS-101 programs from IIT Kanpur. All tests are given to the repair tool, as in the study. Each repaired program is then compared with the student's own correct version on perturbed inputs.

| | FixPass (alone) | GenProg + AE + Prophet + Angelix (together) |
|---|---|---|
| Plausible repairs | **207 / 653 (31.7%)** | 208 / 661 (31%) |
| Budget | 120 s, median 16 s | 15 min per tool |
| Agrees with the student's correct version on perturbed inputs | 105 | not reported |

The baseline is Table 2 of the ITSP study. Eight programs whose recorded failure does not reproduce here are excluded. Eight of the 207 patched programs contain stack memory errors: rebuilt with gcc's default stack protector, they fail some tests.

Reproduce: `python3 scripts/introclass.py prepare|run|report` and `python3 scripts/itsp.py prepare|run|report` (each takes `--data` pointing at the dataset), then `python3 scripts/write_results.py`.

## Scope

- **Local faults only.** The layer repairs faults that a single operator, literal, identifier, argument-order or similar edit can fix, plus compile errors of any kind. It does not invent missing statements, rewrite output strings or restructure algorithms. These are the most common reasons for failure on both benchmarks.
- **Tests are evidence, not proof.** Weak tests give overfitted repairs, and the report shows where the tests left the choice open.
- **Not a security sandbox.** The test runner limits resources but does not isolate the program; run untrusted code inside an OS-level sandbox.

License: Apache-2.0.
