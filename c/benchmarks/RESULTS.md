# Benchmark results

Standard C program-repair benchmarks. No model is used: every repair comes from the compiler-guided stages and the test-driven search. Budget 120 s per bug on 2 vCPU (Xeon 2.1 GHz), gcc 13, libclang 18.

## IntroClass

`scripts/introclass.py`. IntroClass (Le Goues et al., TSE 2015): student solutions to six assignments, each with a black-box and a white-box (KLEE-generated) test suite. A defect is a deterministic submission that fails at least one and passes at least one test of the suite used for repair; the other suite is held out. The benchmark's fuzzy checkers are converted to regular expressions; on the deterministic submissions they reproduce 14027 of 14,058 recorded verdicts (`oracle_validation.json`: the 31 others are cases where the recorded output itself passes, or fails, the published checker differently from the recorded verdict, or where the program behaves differently in this environment). Defects whose recorded failure does not reproduce here (the original already passes) are excluded. Identical submissions are repaired once and counted for each copy, as the tool is deterministic.

### Repair with the black-box suite, white-box suite held out

| Assignment | Defects | Plausible | Pass all held-out tests | Mean held-out pass rate |
|---|---|---|---|---|
| digits | 72 | 21 | 11 | 95.2% |
| grade | 159 | 7 | 7 | 100.0% |
| median | 138 | 88 | 18 | 86.0% |
| smallest | 142 | 86 | 72 | 94.2% |
| syllables | 21 | 2 | 0 | 55.0% |
| **all** | 532 | 204 (38.3%) | 108 (20.3%) | 90.6% |

228 distinct sources; median 17.2 s per source; 9 defects excluded because the original passes here.

### Repair with the white-box suite, black-box suite held out

| Assignment | Defects | Plausible | Pass all held-out tests | Mean held-out pass rate |
|---|---|---|---|---|
| checksum | 22 | 5 | 5 | 100.0% |
| digits | 153 | 40 | 32 | 96.3% |
| grade | 159 | 7 | 7 | 100.0% |
| median | 129 | 129 | 95 | 95.3% |
| smallest | 114 | 114 | 0 | 80.8% |
| syllables | 29 | 16 | 16 | 100.0% |
| **all** | 606 | 311 (51.3%) | 155 (25.6%) | 90.6% |

264 distinct sources; median 7.7 s per source; 15 defects excluded because the original passes here.

### Published baselines (IntroClass)

| Tool | Black-box: plausible | White-box: plausible | Held-out pass rate of patches |
|---|---|---|---|
| GenProg (Le Goues et al. 2015) | 287 / 778 (36.9%) | 292 / 845 (34.6%) | 68.7% (Smith et al. 2015) |
| TrpAutoRepair | 247 / 778 (31.7%) | 212 / 845 (25.1%) | 72.1% |
| AE | 159 / 778 (20.4%) | 166 / 845 (19.6%) | 64.2% |
| SearchRepair (Ke et al. 2015) | 150 / 778 (19.3%) | - | 97.3% |
| **CFixPass** | 204 / 532 (38.3%) | 311 / 606 (51.3%) | 90.6% (black-box repair) |

GenProg and TrpAutoRepair ran 20 random seeds per defect, AE one deterministic run (25 min limit). The defect sets differ: the published counts include submissions this study excludes as nondeterministic, and the current IntroClass release has more submissions than the 2015 one. Compare rates, not counts.

## ITSP

`scripts/itsp.py`. ITSP (Yi et al., ESEC/FSE 2017): incorrect CS-101 C programs from IIT Kanpur, each with the student's later correct version. As in the ITSP study, all of a problem's tests are given to the repair tool and outputs are compared as `diff -ZB` does. Beyond the tests, every repaired program is run against the student's own correct version on up to 40 perturbed copies of the test inputs (differential testing).

| Lab | Programs | Plausible | Agrees with the correct version |
|---|---|---|---|
| Lab-3 | 63 | 9 | 6 |
| Lab-4 | 116 | 31 | 10 |
| Lab-5 | 82 | 27 | 14 |
| Lab-6 | 78 | 31 | 22 |
| Lab-7 | 69 | 15 | 4 |
| Lab-8 | 33 | 16 | 4 |
| Lab-9 | 46 | 14 | 8 |
| Lab-10 | 51 | 21 | 11 |
| Lab-11 | 55 | 18 | 14 |
| Lab-12 | 60 | 25 | 12 |
| **all** | 653 | 207 (31.7%) | 105 (16.1%) |

8 programs whose recorded failure does not reproduce here are excluded; median 15.9 s per program.

Of the plausible patches, 8 fail some tests when the program is rebuilt with gcc's default flags: the programs have stack memory errors, and with the default stack protector (the tool builds with -fno-stack-protector) 5 of them abort and 3 print different output. For 13 plausible patches the student's correct version itself fails on every perturbed input, so differential testing has nothing to compare; they are counted as not agreeing.

Published baseline (ITSP study, Table 2): GenProg, AE, Prophet and Angelix together, each with 15 minutes per program, repaired **208 of 661 (31%)**.
