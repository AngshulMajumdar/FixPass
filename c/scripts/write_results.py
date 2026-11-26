#!/usr/bin/env python3
"""benchmarks/RESULTS.md from benchmarks/introclass/summary.json and benchmarks/itsp/summary.json."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.path.join(ROOT, 'benchmarks')


def pct(a, b):
    return f'{100 * a / b:.1f}%' if b else '-'


def main():
    out = ['# Benchmark results', '',
           'Standard C program-repair benchmarks. No model is used: every repair comes from the compiler-guided stages and the '
           'test-driven search. Budget 120 s per bug on 2 vCPU (Xeon 2.1 GHz), gcc 13, libclang 18.', '']
    ic = json.load(open(os.path.join(B, 'introclass', 'summary.json')))
    val = json.load(open(os.path.join(B, 'introclass', 'oracle_validation.json')))
    out += ['## IntroClass', '',
            '`scripts/introclass.py`. IntroClass (Le Goues et al., TSE 2015): student solutions to six assignments, each with a '
            'black-box and a white-box (KLEE-generated) test suite. A defect is a deterministic submission that fails at least '
            'one and passes at least one test of the suite used for repair; the other suite is held out. The benchmark\'s fuzzy '
            'checkers are converted to regular expressions; on the deterministic submissions they reproduce '
            f'{14058 - len(val["mismatches"])} of 14,058 recorded verdicts (`oracle_validation.json`: the {len(val["mismatches"])} '
            'others are cases where the recorded output itself passes, or fails, the published checker differently from the '
            'recorded verdict, or where the program behaves differently in this environment). Defects whose recorded failure '
            'does not reproduce here (the original already passes) are excluded. Identical submissions are repaired once and '
            'counted for each copy, as the tool is deterministic.', '']
    for suite, held in (('blackbox', 'white-box'), ('whitebox', 'black-box')):
        s = ic.get(suite)
        if not s:
            continue
        out += [f'### Repair with the {suite.replace("box", "-box")} suite, {held} suite held out', '',
                '| Assignment | Defects | Plausible | Pass all held-out tests | Mean held-out pass rate |', '|---|---|---|---|---|']
        for p, r in s['by_program'].items():
            if r['defects']:
                m = '-' if r['mean_heldout_pass_rate'] is None else f'{100 * r["mean_heldout_pass_rate"]:.1f}%'
                out.append(f'| {p} | {r["defects"]} | {r["plausible"]} | {r["pass_all_heldout"]} | {m} |')
        out.append(f'| **all** | {s["defects"]} | {s["plausible"]} ({pct(s["plausible"], s["defects"])}) | '
                   f'{s["pass_all_heldout"]} ({pct(s["pass_all_heldout"], s["defects"])}) | '
                   f'{100 * s["mean_heldout_pass_rate"]:.1f}% |')
        out += ['', f'{s["distinct_sources"]} distinct sources; median {s["median_seconds_per_distinct_source"]} s per source; '
                f'{s["not_reproduced"]} defects excluded because the original passes here.', '']
    out += ['### Published baselines (IntroClass)', '',
            '| Tool | Black-box: plausible | White-box: plausible | Held-out pass rate of patches |', '|---|---|---|---|',
            '| GenProg (Le Goues et al. 2015) | 287 / 778 (36.9%) | 292 / 845 (34.6%) | 68.7% (Smith et al. 2015) |',
            '| TrpAutoRepair | 247 / 778 (31.7%) | 212 / 845 (25.1%) | 72.1% |',
            '| AE | 159 / 778 (20.4%) | 166 / 845 (19.6%) | 64.2% |',
            '| SearchRepair (Ke et al. 2015) | 150 / 778 (19.3%) | - | 97.3% |']
    bb, wb = ic.get('blackbox', {}), ic.get('whitebox', {})
    if bb and wb:
        out.append(f'| **CFixPass** | {bb["plausible"]} / {bb["defects"]} ({pct(bb["plausible"], bb["defects"])}) | '
                   f'{wb["plausible"]} / {wb["defects"]} ({pct(wb["plausible"], wb["defects"])}) | '
                   f'{100 * bb["mean_heldout_pass_rate"]:.1f}% (black-box repair) |')
    out += ['', 'GenProg and TrpAutoRepair ran 20 random seeds per defect, AE one deterministic run (25 min limit). The defect sets '
            'differ: the published counts include submissions this study excludes as nondeterministic, and the current '
            'IntroClass release has more submissions than the 2015 one. Compare rates, not counts.', '']
    path = os.path.join(B, 'itsp', 'summary.json')
    if os.path.exists(path):
        it = json.load(open(path))
        out += ['## ITSP', '',
                '`scripts/itsp.py`. ITSP (Yi et al., ESEC/FSE 2017): incorrect CS-101 C programs from IIT Kanpur, each with the '
                'student\'s later correct version. As in the ITSP study, all of a problem\'s tests are given to the repair tool '
                'and outputs are compared as `diff -ZB` does. Beyond the tests, every repaired program is run against the '
                'student\'s own correct version on up to 40 perturbed copies of the test inputs (differential testing).', '',
                '| Lab | Programs | Plausible | Agrees with the correct version |', '|---|---|---|---|']
        for lab, r in it['by_lab'].items():
            out.append(f'| {lab} | {r["programs"]} | {r["plausible"]} | {r["agree_with_correct_version"]} |')
        out.append(f'| **all** | {it["programs"]} | {it["plausible"]} ({pct(it["plausible"], it["programs"])}) | '
                   f'{it["agree_with_correct_version"]} ({pct(it["agree_with_correct_version"], it["programs"])}) |')
        out += ['', f'{it.get("not_reproduced", 0)} programs whose recorded failure does not reproduce here are excluded; median '
                f'{it["median_seconds"]} s per program.', '',
                f'Of the plausible patches, {it["plausible_failing_recheck"]} fail some tests when the program is rebuilt with '
                'gcc\'s default flags: the programs have stack memory errors, and with the default stack protector (the tool '
                'builds with -fno-stack-protector) 5 of them abort and 3 print different output. For '
                f'{it["plausible_without_usable_variants"]} plausible patches the student\'s correct version itself fails on '
                'every perturbed input, so differential testing has nothing to compare; they are counted as not agreeing.', '',
                'Published baseline (ITSP study, Table 2): GenProg, AE, Prophet and Angelix together, each with 15 minutes per '
                'program, repaired **208 of 661 (31%)**.', '']
    with open(os.path.join(B, 'RESULTS.md'), 'w') as f:
        f.write('\n'.join(out))
    print('\n'.join(out))


if __name__ == '__main__':
    main()
