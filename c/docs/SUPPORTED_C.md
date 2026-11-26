# Supported C and guardrails

## Input subset

- One C source file with `main`, reading `stdin` and writing `stdout` (console programs).
- Standard headers only, written `#include <name.h>`: `assert complex ctype errno fenv float inttypes iso646 limits locale math setjmp stdalign stdarg stdbool stddef stdint stdio stdlib stdnoreturn string tgmath time uchar wchar wctype`.
- Rejected before any work starts:
  - `#pragma` and inline assembly;
  - calls that reach outside stdin/stdout: `system`, `popen`, `fork`, the `exec` family, `fopen`/`freopen`, `remove`/`rename`, `signal`/`raise`, sockets, threads and dynamic loading.
- Limits: 200 KB of source, 500 tests.
- Language: GNU C11 (`-std=gnu11`). The front end is clang (libclang). Programs are built with gcc when it is present, otherwise clang.

## Tests

```json
[{"input": "2 6 8\n", "output": "6 is the median"},
 {"input": "8 6 2\n", "output_regex": "\\b6 is the median"}]
```

- **Output comparison.** Outputs are compared as `diff -ZB` compares them: trailing whitespace on a line and blank lines are ignored.
- **`output_regex`** (optional) is searched in the output instead of `output`, for checkers that accept any wording around the answer.
- **`exit_code`** (optional) also requires a specific exit status.
- **Abnormal ends.** A crash (signal), running out of fuel or time, or more than 1 MiB of output fails the test.

## What each stage can repair

| Stage | Signal | Repairs |
|---|---|---|
| Directives | the `#include` lines | misspelled `#include` and header names (`#inlcude <stdoi.h>`) |
| Syntax | clang parse diagnostics | any parse error, through a general least-cost token search (below) |
| Semantic | clang diagnostics and fix-its | missing `#include` (from an index of the standard headers); misspelled names (edit distance ≤ 2, checked against what is declared and the standard library); a call to a function defined later (prototype added); an undeclared variable (declared at the top of its function, type left open); `return;` in a non-void function; missing or extra `&`/`*` in a conversion; clang's own fix-its |
| Warning rules | clang warnings | `scanf` argument without `&`; `printf`/`scanf` specifier that does not match its argument; `=` in a condition (`==`); `==` as a statement (`=`); `;` that makes an empty `if`/`for`/`while` body; uninitialized local; missing return value |
| Search | test outcomes, statement coverage, crash sites | relational, arithmetic and logical operator swaps; integer and character literal changes; `expr ± 1`; dropping a constant offset; same-type variable replacement; swapping same-type arguments; swapping ternary branches; removing `!`; `++` ↔ `--`; compound-assignment changes; `(double)` before an integer division; `fmin` ↔ `fmax`, `floor` ↔ `ceil`; revisiting a tied choice from the compile stages |

### Warning rules

In C, many real bugs compile with only a warning. Each rule proposes edits, mostly clang's own fix-its.

- **With tests:** an edit is kept only if no passing test fails and the test score does not fall. When several edits tie, the choice stays open as a hole that the search can revisit.
- **Without tests:** only edits that remove undefined behaviour without choosing between meanings are applied:
  - a missing `&` in `scanf`;
  - a format specifier that does not match its argument;
  - an uninitialized local set to zero.

### Syntax stage (general)

The syntax stage does not use a list of known mistakes. At the first parse error it searches token edits near the error, in the manner of Burke–Fisher and least-cost error recovery, with clang as the oracle.

- **Confusables.** Before the search, characters that are illegal in C code and have an unambiguous meaning are normalized wherever they appear outside literals and comments: smart quotes, Unicode dashes and minus, NBSP, zero-width spaces, full-width punctuation.
- **Candidates.**
  - Insert any token from the C vocabulary near the error: operators, separators, keywords, identifiers that occur nearby, literals, and the tokens clang says it expected.
  - Delete a token, replace a token, swap adjacent tokens, or delete two tokens.
  - Split a glued identifier or join split tokens.
  - Lexical repairs: unclosed string, char or comment; malformed number; stray character.
  - Brace repairs guided by the indentation.
- **Acceptance.** The file must parse, or the first parse error must move at least 8 tokens further on.
  - Token edits must not re-lex anything outside their own window, so an edit can never open a comment or string that swallows the rest of the file.
  - An edit that creates a new error *before* the original error site is rejected. clang suppresses errors after a semantic error in the same statement, so without this rule such an edit could look like progress.
- **Costs.**
  - Deleting a name or literal costs more than deleting punctuation.
  - Replacing or deleting a brace costs extra.
  - A `;` that makes an empty statement, or comes straight after a type name, costs extra.
- **Ranking.** Edit cost plus the remaining parse errors. Once the file parses, semantic errors count too (0.7 each), so a choice that parses but does not compile loses to one that compiles.
- **Escalation.** The levels are tried in this order, and each accepted step strictly reduces the number of tokens after the first error. The loop therefore terminates and every input ends parseable.
  1. Lexical and structural repairs.
  2. Priority tokens.
  3. The full vocabulary with a wider window.
  4. Pairs of edits.
  5. Lossy excision of lines around the error (windows up to 32 lines each side).
  6. Truncation, with the open blocks closed.

  Lossy steps are reported (`lossy_syntax_repair`, with the deleted text).
- **Ties.** Equally cheap alternatives at one site are first ranked by compile errors, then kept as holes that the test-driven search settles.

Compile errors that the semantic rules cannot fix go through the same token search: the file must still parse and the error count must fall.

Out of scope for this layer: inserting or deleting whole statements, new control flow, string-literal rewrites, and algorithm-level changes.

## Guardrails

- **Acceptance.** Every edit is checked by clang, and every behavioural edit by the supplied tests. An edit is accepted only if it raises the test score and no previously passing test fails. Backtracking may give up a passing test, but only if the number of passing tests does not fall.
- **Signatures.** Function signatures (name, return type, parameter types) must match those after the compile stages.
- **Test runs.** Candidates run as separate processes with limits on CPU time, memory (1 GiB of address space) and output (1 MiB). Stdin and stdout are redirected, and address-space randomization is turned off on Linux.
- **Determinism.** Locals are zero-initialized (`-ftrivial-auto-var-init=zero` when the compiler supports it), so a program that reads an uninitialized variable behaves the same on every run. The released program is built the same way.
- **Fuel.**
  - During the search, every loop iteration and function entry costs one unit.
  - The limit is 50× the largest use among the baseline tests that terminate, between 1M and 50M.
  - Past the limit the run ends with status 97 and records where it was. Non-terminating candidates therefore fail deterministically.
  - A wall-clock timeout (20× the slowest test, 0.5–2 s) remains as a backstop.
- **Final validation.** The corrected program is compiled again without instrumentation and every supplied test is run. Otherwise `corrected.c` and the program are not written, and only `candidate.c` is left for diagnosis.
- **Not a sandbox.** The test runner limits resources but does not isolate the program. Use an OS-level sandbox (a container, bubblewrap/seccomp) for untrusted code.
- **Tests are evidence, not proof.** When several edits tie, the report lists them (`equal_score_alternatives`), groups them by behaviour on amplified inputs, and marks the round `ambiguous` if the groups disagree.
