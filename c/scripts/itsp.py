#!/usr/bin/env python3
"""ITSP (Yi et al., ESEC/FSE 2017): 661 incorrect CS-101 student programs from IIT Kanpur, each with a later correct
version by the same student and the course's test cases (dataset/test/in.<problem>.<k>.txt, out.<problem>.<k>.txt).

As in the ITSP study, all of a problem's tests are given to the repair tool, and outputs are compared ignoring trailing
whitespace and blank lines (the study uses diff -ZB). Correctness beyond the tests is checked by differential testing:
the repaired program and the student's own correct version are run on perturbed copies of the test inputs.

Usage: python3 scripts/itsp.py prepare --data ITSP/dataset
       python3 scripts/itsp.py run [--budget 120] [--part 0 --parts 2]
       python3 scripts/itsp.py report
"""
import argparse
import glob
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'benchmarks', 'itsp')
GCC = ['gcc', '-std=gnu11', '-O0', '-w', '-ftrivial-auto-var-init=zero']


def norm(s):
    """diff -ZB: ignore trailing whitespace on each line and blank lines."""
    return [l.rstrip() for l in s.replace('\r\n', '\n').split('\n') if l.strip()]


def run_plain(binary, inp, timeout=2):
    try:
        p = subprocess.run([binary], input=inp.encode(), capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        return None, ''


def compile_(src_path, binary):
    return subprocess.run(GCC + ['-o', binary, src_path, '-lm'], capture_output=True).returncode == 0


def perturb(inputs, n=40, seed=11):
    """Deterministic variants of the test inputs: integers and decimals changed, structure kept."""
    rnd = random.Random(seed)
    num = re.compile(r'(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])')
    out, seen = [], set(inputs)
    for _ in range(n * 6):
        if len(out) >= n:
            break
        base = rnd.choice(inputs)
        spans = list(num.finditer(base))
        if not spans:
            continue
        s = base
        for m in sorted(rnd.sample(spans, min(len(spans), rnd.randint(1, 3))), key=lambda m: -m.start()):
            v = m.group()
            if '.' in v:
                nv = f'{float(v) * rnd.choice([0.5, 2, -1, 1.5]) + rnd.choice([0, 1, -1]):.{len(v.split(".")[1])}f}'
            else:
                x = int(v)
                nv = str(rnd.choice([x + 1, x - 1, x * 2, abs(x) + rnd.randint(1, 9), 0, 1, max(1, x // 2)]))
            s = s[:m.start()] + nv + s[m.end():]
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def prepare(args):
    os.makedirs(OUT, exist_ok=True)
    cases, problems, issues = [], {}, []
    tmp = tempfile.mkdtemp()
    for bug in sorted(glob.glob(os.path.join(args.data, 'Lab-*', '*', '*_buggy.c'))):
        lab = bug.split(os.sep)[-3]
        prob = bug.split(os.sep)[-2]
        pid = os.path.basename(bug)[:-len('_buggy.c')]
        if prob not in problems:
            tests = []
            for f in sorted(glob.glob(os.path.join(args.data, 'test', f'in.{prob}.*.txt')),
                            key=lambda p: int(p.split('.')[-2])):
                k = f.split('.')[-2]
                with open(f, errors='replace') as fi, open(os.path.join(args.data, 'test', f'out.{prob}.{k}.txt'), errors='replace') as fo:
                    tests.append({'input': fi.read(), 'output': fo.read()})
            problems[prob] = tests
        tests = problems[prob]
        correct = bug.replace('_buggy.c', '_correct.c')
        rel = os.path.relpath(bug, args.data)
        case = {'id': f'{lab}_{prob}_{pid}', 'lab': lab, 'problem': prob, 'buggy': rel,
                'correct': os.path.relpath(correct, args.data), 'tests': len(tests)}
        # check the conversion: the buggy version fails at least one test, the correct version passes all
        for kind, path in (('buggy', bug), ('correct', correct)):
            b = os.path.join(tmp, kind)
            ok = compile_(path, b)
            passed = 0
            for t in tests:
                code, out = run_plain(b, t['input']) if ok else (None, '')
                passed += code is not None and code >= 0 and norm(out) == norm(t['output'])
            case[f'{kind}_compiles'] = ok
            case[f'{kind}_passes'] = passed
        if case['buggy_passes'] == len(tests) or case['correct_passes'] != len(tests):
            issues.append(case['id'])
        cases.append(case)
    json.dump(problems, open(os.path.join(OUT, 'tests.json'), 'w'), indent=1)
    json.dump(cases, open(os.path.join(OUT, 'cases.json'), 'w'), indent=1)
    print(f'{len(cases)} programs, {len(problems)} problems; buggy compiles: {sum(c["buggy_compiles"] for c in cases)}; '
          f'buggy fails >= 1 test: {sum(c["buggy_passes"] < c["tests"] for c in cases)}; '
          f'correct passes all: {sum(c["correct_passes"] == c["tests"] for c in cases)}')


def run(args):
    cases = json.load(open(os.path.join(OUT, 'cases.json')))
    problems = json.load(open(os.path.join(OUT, 'tests.json')))
    res_path = os.path.join(OUT, f'results_part{args.part}.json')
    results = json.load(open(res_path)) if os.path.exists(res_path) else {}
    todo = [c for i, c in enumerate(cases) if i % args.parts == args.part]
    for n, c in enumerate(todo):
        if c['id'] in results and not args.redo:
            continue
        tests = problems[c['problem']]
        work = tempfile.mkdtemp(prefix='itsp-')
        tfile = os.path.join(work, 'tests.json')
        json.dump([{'input': t['input'], 'output': t['output']} for t in tests], open(tfile, 'w'))
        src_path = os.path.join(args.data, c['buggy'])
        t0 = time.time()
        p = subprocess.run([sys.executable, '-m', 'cfixpass.cli', src_path, '--tests', tfile, '--work-dir',
                            os.path.join(work, 'w'), '--time-budget', str(args.budget)], capture_output=True, text=True,
                           cwd=work, env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        secs = time.time() - t0
        rep = {}
        rp = os.path.join(work, 'w', 'report.json')
        if os.path.exists(rp):
            rep = json.load(open(rp))
        else:
            rep = {'status': 'no report: ' + p.stderr[-1500:]}
        entry = {'lab': c['lab'], 'exit': p.returncode, 'seconds': round(secs, 1), 'status': rep.get('status')}
        corrected = os.path.join(work, 'w', 'corrected.c')
        if p.returncode == 0 and os.path.exists(corrected):
            # the tool's own validation compares like diff -ZB? re-check with the study's comparison
            fixed_bin, ref_bin = os.path.join(work, 'fixed'), os.path.join(work, 'ref')
            ok = compile_(corrected, fixed_bin) and compile_(os.path.join(args.data, c['correct']), ref_bin)
            passed = 0
            for t in tests:
                code, out = run_plain(fixed_bin, t['input']) if ok else (None, '')
                passed += code is not None and code >= 0 and norm(out) == norm(t['output'])
            entry['tests_diff_ZB'] = f'{passed}/{len(tests)}'
            variants = perturb([t['input'] for t in tests])
            agree = valid = 0
            for v in variants:
                rc, ro = run_plain(ref_bin, v)
                if rc is None or rc < 0:
                    continue          # the student's correct version itself fails: not a usable input
                valid += 1
                fc, fo = run_plain(fixed_bin, v)
                agree += fc is not None and fc >= 0 and norm(fo) == norm(ro)
            entry['differential'] = f'{agree}/{valid}'
            entry['agrees_with_correct_version'] = valid > 0 and agree == valid
            src = open(src_path, errors='replace').read()
            entry['patch'] = unified(src, open(corrected).read())
        results[c['id']] = entry
        json.dump(results, open(res_path, 'w'), indent=1)
        subprocess.run(['rm', '-rf', work])
        print(f'[{n + 1}/{len(todo)}] {c["id"]} exit={p.returncode} {entry.get("differential", "")} {secs:.1f}s', flush=True)


def unified(a, b):
    import difflib
    return ''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), 'buggy.c', 'repaired.c', n=1))


def report(args):
    cases = json.load(open(os.path.join(OUT, 'cases.json')))
    results = {}
    for f in glob.glob(os.path.join(OUT, 'results_part*.json')):
        results.update(json.load(open(f)))
    labs = sorted({c['lab'] for c in cases}, key=lambda l: int(l.split('-')[1]))
    s = {'programs': len(cases), 'attempted': len(results), 'by_lab': {}}
    for lab in labs + ['all']:
        rs_all = [results[c['id']] for c in cases if lab in ('all', c['lab']) and c['id'] in results]
        # the original already passes all tests here (its recorded failure does not reproduce): excluded
        stale = [r for r in rs_all if r['exit'] == 0 and not r.get('patch')]
        rs = [r for r in rs_all if not (r['exit'] == 0 and not r.get('patch'))]
        row = {'programs': sum(1 for c in cases if lab in ('all', c['lab'])) - len(stale), 'not_reproduced': len(stale),
               'plausible': sum(r['exit'] == 0 for r in rs),
               'agree_with_correct_version': sum(1 for r in rs if r.get('agrees_with_correct_version')),
               'plausible_failing_recheck': sum(1 for r in rs if r['exit'] == 0 and r.get('tests_diff_ZB')
                                                and r['tests_diff_ZB'].split('/')[0] != r['tests_diff_ZB'].split('/')[1]),
               'plausible_without_usable_variants': sum(1 for r in rs if r['exit'] == 0
                                                        and str(r.get('differential', '')).endswith('/0')),
               'rejected_by_gate': sum(r['exit'] == 2 for r in rs)}
        if lab == 'all':
            s.update(row)
            secs = sorted(r['seconds'] for r in rs)
            s['median_seconds'] = secs[len(secs) // 2] if secs else None
        else:
            s['by_lab'][lab] = row
    json.dump(s, open(os.path.join(OUT, 'summary.json'), 'w'), indent=1)
    print(json.dumps(s, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['prepare', 'run', 'report'])
    ap.add_argument('--data', default=os.path.join(ROOT, 'ITSP', 'dataset'))
    ap.add_argument('--budget', type=float, default=120)
    ap.add_argument('--part', type=int, default=0)
    ap.add_argument('--parts', type=int, default=1)
    ap.add_argument('--redo', action='store_true')
    a = ap.parse_args()
    {'prepare': prepare, 'run': run, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
