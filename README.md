# FixPass

A repairing compiler pass for C and Java. It sits between the editor and the compiler. Give it an imperfect single-file program, plus behavioural tests if you have them. It returns corrected, reviewable source and a built program, and nothing is released unless the program compiles and every supplied test passes.

There is no language model and no GPU. Every repair comes from the language's own compiler and a test-driven search, so it runs on any laptop.

| Command | Language | Compiler used as the oracle | Folder |
|---|---|---|---|
| `jfixpass` | Java (JDK 17+) | `javac`, in-process (`javax.tools`) | [java/](java/README.md) |
| `cfixpass` | C (GNU C11) | clang, in-process (libclang); programs built with gcc or clang | [c/](c/README.md) |

## Install

```bash
git clone https://github.com/AngshulMajumdar/FixPass.git
cd FixPass
bash install.sh                       # both commands, to ~/.local/bin
bash install.sh --only java           # or only one: --only java | --only c
bash install.sh --prefix /usr/local   # system-wide (may need sudo)
jfixpass --version; cfixpass --version
```

Requirements:
- `jfixpass` needs a JDK 17 or newer.
- `cfixpass` needs Python 3.9 or newer and gcc or clang. Its installer fetches one Python package, `libclang`.

The installer skips a command whose toolchain is missing. To remove: `bash uninstall.sh` (same `--prefix`).

On **Windows**, `install.cmd` installs `jfixpass`. For `cfixpass`, use WSL.

Prebuilt `jfixpass-0.1.0.jar` and the `cfixpass` wheel are attached to the [release](https://github.com/AngshulMajumdar/FixPass/releases). Run the jar with `java -Xss64m -jar jfixpass-0.1.0.jar`, and install the wheel with `pip install cfixpass-0.1.0-py3-none-any.whl`.

## Use

```bash
jfixpass Broken.java --tests tests.json      # results in ./jfixpass-out/Broken/
cfixpass broken.c    --tests tests.json      # results in ./cfixpass-out/broken/
```

Tests are `[{"input": "...", "output": "..."}]`. Without tests, only the compile stages run. Each run writes:
- `original` and `candidate` sources;
- `corrected` source and a built program, only on success;
- `report.json`, with every diagnostic, the edit made for it and the tied alternatives.

| Exit code | Meaning |
|---|---|
| 0 | Repaired and validated |
| 1 | Repair failed: only the candidate is written |
| 2 | Invalid input, or input outside the supported subset |

All options are in [java/README.md](java/README.md) and [c/README.md](c/README.md).

## How it works

The method is the same for both languages; only the compiler front end differs.

1. **Gate.** The supported subset is a single file with console I/O.
2. **Unchanged check.** A program that compiles and passes every test is returned byte-for-byte.
3. **General syntax repair.** A least-cost token search runs at each parse error, with the compiler as the oracle. It can insert, delete, replace, swap, split and join tokens. It also makes lexical repairs (unclosed literals and comments, confusable Unicode) and indentation-guided brace repairs.
   - **Acceptance.** An edit is kept only if the file parses or the first error moves at least 8 tokens further on.
   - **Ranking.** Edit cost plus the errors that remain.
   - **Guarantee.** The search escalates to pairs of edits and then reported lossy excision, so every input ends parseable.
4. **Semantic repair** from the compiler's diagnostics: imports and headers, misspelled names, declarations, return values, casts and references. The same token search is the fallback.
   - **Ties.** When several repairs remove an error equally well, the choice is kept as a *hole* that test evidence can revisit.
   - **C warning rules.** C adds rules driven by warnings, because many C bugs only produce one: `scanf` without `&`, format mismatches, `=` in a condition, a stray `;`, uninitialized locals.
5. **Test-driven search** over typed AST edits (operators, literals, ±1, same-type variables and arguments, and more).
   - **Ranking.** Ochiai suspiciousness from statement coverage, plus a boost at crash sites.
   - **Scoring.** A passing test counts 2; a failing test counts its output similarity.
   - **Acceptance.** An edit is kept only if the score rises and no passing test regresses.
   - **Backtracking and ties.** When stuck, the search backtracks and tries pairs of edits. Equal-score alternatives are grouped by their behaviour on amplified inputs.
6. **Termination and validation.**
   - Termination is deterministic: loops and calls cost fuel, with a wall clock as backstop.
   - The final program is rebuilt without instrumentation and validated against all tests.

## Results

Standard program-repair benchmarks, 120 s per bug on 2 vCPU, no model. Per-benchmark tables and the exact protocols are in [java/benchmarks/RESULTS.md](java/benchmarks/RESULTS.md) and [c/benchmarks/RESULTS.md](c/benchmarks/RESULTS.md).

| Benchmark | FixPass | Published baselines |
|---|---|---|
| IntroClassJava (297 programs) | 50 plausible of 258 failing, 31 pass all held-out tests | best of 11 Java tools 32, all 11 together 62 (2 h per bug) |
| QuixBugs, Java (40 programs) | 13 plausible, 11 correct | best of 11 tools 4, all 11 together 12 |
| IntroClass, C, black-box suite | 204 / 532 plausible (38.3%); patches pass 90.6% of held-out tests | GenProg 36.9%, TrpAutoRepair 31.7%, AE 20.4%, SearchRepair 19.3%; GenProg patches pass 68.7% of held-out tests |
| IntroClass, C, white-box suite | 311 / 606 plausible (51.3%) | GenProg 34.6%, TrpAutoRepair 25.1%, AE 19.6% |
| ITSP, C (661 student programs) | 207 / 653 plausible (31.7%), 105 agree with the student's correct version | GenProg + AE + Prophet + Angelix together: 208 / 661 (31%), 15 min per tool |

## Layout

```
install.sh, uninstall.sh       both commands (Linux, macOS)
install.cmd, uninstall.cmd     jfixpass on Windows
java/   src/jfixpass/*.java    the Java pass (no dependencies beyond the JDK)
        scripts/, benchmarks/  own benchmark, syntax stress test, IntroClassJava, QuixBugs
c/      src/cfixpass/*.py      the C pass (libclang)
        scripts/, benchmarks/  IntroClass, ITSP
```

## Scope

FixPass repairs compile errors of any kind and local behavioural faults: those a single operator, literal, identifier, argument or similar edit can fix. It does not invent missing statements, rewrite output strings or restructure algorithms.

Passing tests are evidence, not proof. Weak tests give overfitted repairs, and the report shows where the tests left the choice open.

The test runners limit resources but are not a security sandbox. Run untrusted code inside an OS-level sandbox.

License: Apache-2.0. Cite with [CITATION.cff](CITATION.cff).
