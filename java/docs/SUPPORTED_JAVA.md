# Supported Java and guardrails

## Input subset

- One source file in the default package containing a public class with `public static void main(String[])`. The file can hold other top-level and nested classes.
- Console programs: input from `System.in`, output to `System.out`. Tests compare stdout after stripping leading and trailing whitespace, and require the program to end without an uncaught exception.
- Allowed imports: `java.util.*`, `java.util.function.*`, `java.util.stream.*`, `java.util.regex.*`, `java.math.*`, `java.text.*`, `java.time.*`, and the stream/reader classes in `java.io` (`BufferedReader`, `InputStreamReader`, `IOException`, `PrintWriter`, ...).
- Rejected before any work starts: package declarations; files, network, processes, threads, reflection and class loading; `System.exit`/`setIn`/`setOut`/`setProperty`; `native`.
- Limits: 200 KB source, 500 tests.

## What each stage can repair

| Stage | Signal | Repairs |
|---|---|---|
| Syntax | javac parse diagnostics | Any parse error, through a general least-cost token search (below) |
| Types | javac attribution diagnostics (`compiler.err.*` codes) | missing imports (from an index of `java.base`); misspelled variables, methods and classes (edit distance ≤ 2, checked against what is in scope and against JDK members found by reflection); `s.length` ↔ `s.length()`; `arr.length()` → `arr.length`; missing `new`; lossy conversions (cast); `=` used as a condition; String ↔ number conversions; missing `return` (candidate values: other returns in the method, locals of the right type, literals in the file, the default); uninitialized locals; `throws` on `main` |
| Static rules | attributed AST | `String` compared with `==`/`!=` becomes a null-safe `equals` (kept only if no test regresses) |
| Search | test outcomes + statement coverage + crash stack traces | relational, arithmetic and logical operator swaps; integer/char/boolean literal changes; `expr ± 1`; dropping a constant offset; same-type variable replacement; swapping same-type arguments; swapping ternary branches; removing `!`; `++` ↔ `--`; compound-assignment changes; `(double)` before an integer division; `Math.min` ↔ `max`, `floor` ↔ `ceil`; revisiting a tied choice from the compile stage |

### Syntax stage (general)

The syntax stage does not use a list of known mistakes. For the first javac parse error it searches token edits near the error, the approach of Burke–Fisher and least-cost parser error recovery, with javac as the oracle:

- **Confusables.** Before the search, characters that are always illegal in Java code and have an unambiguous meaning (smart quotes, Unicode dashes and minus, NBSP, zero-width spaces, full-width punctuation) are normalized wherever they appear outside literals and comments.
- **Candidates.** Insert any token from the Java vocabulary (operators, separators, keywords, identifiers occurring near the error, literals), delete, replace, swap adjacent tokens, delete two tokens, split a glued identifier, join split tokens. There are also lexical repairs: unclosed string, char and comment; illegal escape; malformed number; illegal or confusable characters such as smart quotes, Unicode minus, NBSP and full-width punctuation. Brace repairs use the indentation.
- **Acceptance.** The file must parse, or the first error must move at least 8 tokens further on. Token edits must not re-lex anything outside their own window, so an edit can never open a comment or string that swallows the rest of the file.
- **Costs.** Deleting a name or literal costs more than deleting punctuation. Replacing or deleting a brace costs extra. A `;` that would create an empty statement, or that javac flags as an empty `if` body, is penalized.
- **Ranking.** Edit cost plus the remaining parse errors. Once the file parses, type errors count too (0.7 each), so a syntactically valid but ill-typed choice loses to one that type-checks.
- **Escalation.** Lexical and structural repairs, then priority tokens, then the full vocabulary with a wider window, then pairs of edits, then lossy excision of lines around the error (windows up to 32 lines each side), then truncation with the open blocks closed. Each accepted step strictly reduces the number of tokens after the first error, so the loop terminates: every input ends parseable. Lossy steps are reported (`lossy_syntax_repair`, with the deleted text).
- **Ties.** Equally cheap alternatives at one site are first ranked by type checking, then kept as holes that the test-driven search settles.

Compile errors that remain after the rule-based type repairs go through the same token search, driven by attribution errors: the file must still parse and the error count must fall.

Out of scope for this layer: inserting or deleting whole statements, new control flow, or algorithm-level rewrites.

## Guardrails

- Every edit is checked by javac, and every behavioural edit by the supplied tests. An edit is accepted only if it raises the test score and no previously passing test fails. Backtracking may give up a passing test, but only if the number of passing tests does not fall.
- Method signatures (owner, name, parameter types, return type) must match the first parseable version of the source.
- Candidates run in a separate worker JVM (`-Xmx384m`). Each test gets a fresh class loader (fresh static state), stdin/stdout are redirected, and output is capped at 1 MiB.
- Fuel: during the search every loop iteration and method entry costs one unit. Past the limit (50× the largest use among baseline tests that terminate, between 1M and 50M) the run ends with an `Error`. Non-terminating candidates therefore fail deterministically without a wall-clock kill. A wall-clock timeout (20× the slowest test, 0.5–2 s) remains as a backstop and restarts the worker.
- Final validation runs the corrected program without fuel instrumentation, in a fresh worker. Every supplied test must pass. Otherwise `corrected.java` and the class files are not written, and only `candidate.java` is left for diagnosis.
- The worker JVM is not a security sandbox. Use the subset gate plus an OS-level sandbox (bubblewrap/seccomp, a container) for untrusted code.
- Passing tests does not prove correctness. When several edits tie, the report lists them (`equal_score_alternatives`), groups them by behaviour on amplified inputs, and marks the round `ambiguous` if the groups disagree.
