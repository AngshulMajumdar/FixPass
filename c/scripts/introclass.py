#!/usr/bin/env python3
"""IntroClass (Le Goues et al., TSE 2015) with cfixpass.

prepare : convert every deterministic defect into cfixpass tests. The benchmark's checkers (bin/genprog_tests.py)
          are fuzzy: they search the output for the answer. Each check is translated into an equivalent regular
          expression ("output_regex"). The recorded verdicts ignore the exit status, so the tests do too.
          The conversion is checked against the pass/fail table in every defect's metadata.json.
run     : repair each defect with one suite (blackbox or whitebox); the other suite is held out. Identical sources
          with identical tests are repaired once (the tool is deterministic) and the result is counted for each copy.
report  : summary.json

Usage: python3 scripts/introclass.py prepare --data IntroClass
       python3 scripts/introclass.py run --suite blackbox [--budget 120] [--part 0 --parts 4]
       python3 scripts/introclass.py report
"""
import argparse
import glob
import hashlib
import json
import os
import re
import string
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'benchmarks', 'introclass')
PROGRAMS = ['checksum', 'digits', 'grade', 'median', 'smallest', 'syllables']
SUITES = ['blackbox', 'whitebox']
# the same compiler settings the repair tool uses for test runs (uninitialized locals read as zero, as they did in the
# environment that produced the benchmark's recorded outputs)
GCC = ['gcc', '-std=gnu11', '-O0', '-w', '-ftrivial-auto-var-init=zero']


def esc(s):
    return re.escape(s)


def oracle(program, truth, test_input):
    """A regular expression (searched with re.DOTALL) equivalent to the IntroClass checker for one test."""
    if program == 'checksum':
        t = re.search('Check[ ]?sum[ ]+is[ ]+([{}])'.format(re.escape(string.printable)), truth, re.I).group(1)
        return ('(?is)\\A(?=.*Check[ ]?sum[ ]+is[ ]+[\\s\\S])(?=.*Check[ ]?sum is {0})(?!.*Check[ ]?sum is [^{1}])'
                .format(esc(t), re.sub(r'([\]\\^-])', r'\\\1', t)))
    if program == 'digits':
        seq = re.findall('[-]?\\d\n', truth)
        g = '(?:(?![-]?\\d\n)[\\s\\S])*'
        return '\\A' + g + ''.join(esc(x) + g for x in seq) + '\\Z'
    if program == 'grade':
        m = re.search('Student has an ([A-D]) grade', truth, re.I)
        if m:
            t = m.group(1)
            others = ''.join(g for g in 'ABCD' if g != t)
            return f'(?is)\\A(?!.*Student has an [{others}] grade)(?=.*Student has an {t} grade)'
        assert re.search('Student has (failed) the course', truth, re.I)
        return '(?is)\\A(?!.*Student has an [ABCD] grade)(?=.*Student has failed the course)'
    if program in ('median', 'smallest'):
        word = program
        flags = '(?s)' if program == 'median' else '(?is)'
        t = re.search(r'([-]?\d+) is the ' + word, truth, re.I if program == 'smallest' else 0).group(1)
        v = int(t)
        num = '-0*' + str(-v) if v < 0 else ('-?0+' if v == 0 else '0*' + str(v))
        g = '(?:(?![-]?\\d+ is the ' + word + ')[\\s\\S])*?'
        neg = ''
        if program == 'median':  # the smallest checker searches its negatives in the *input*, so they never apply
            for n in test_input.split(' '):
                if n != t:
                    neg += '(?!.*' + esc(n) + ' is the median)'
        return flags + '\\A' + neg + g + num + ' is the ' + word
    if program == 'syllables':
        t = re.search('syllables is (\\d+)', truth, re.I).group(1)
        return f'(?s)\\A(?=.*(?i:syllables is {t}))(?!.*syllables is [^{t}])'
    raise ValueError(program)


def load_tests(data, program, suite):
    d = os.path.join(data, program, 'tests', suite)
    out = {}
    for f in glob.glob(os.path.join(d, '*.in')):
        k = os.path.basename(f)[:-3]
        with open(f) as fi, open(f[:-3] + '.out') as fo:
            out[k] = (fi.read(), fo.read())
    return out


def run_plain(binary, inp):
    try:
        p = subprocess.run([binary], input=inp.encode(), capture_output=True, timeout=2)
        return p.returncode, p.stdout.decode(errors='replace')
    except subprocess.TimeoutExpired:
        return None, ''


def prepare(args):
    os.makedirs(OUT, exist_ok=True)
    cases = []
    mismatch = []
    tmp = tempfile.mkdtemp()
    for program in PROGRAMS:
        suites = {s: load_tests(args.data, program, s) for s in SUITES}
        tests = {s: [{'id': k, 'input': i, 'output_regex': oracle(program, o, i)}
                     for k, (i, o) in sorted(suites[s].items(), key=lambda kv: int(kv[0]))] for s in SUITES}
        for md in sorted(glob.glob(os.path.join(args.data, program, '*', '*', 'metadata.json'))):
            sub = os.path.dirname(md)
            with open(md) as f:
                meta = json.load(f)
            src_path = os.path.join(sub, program + '.c')
            if not os.path.exists(src_path):
                continue
            with open(src_path, errors='replace') as f:
                src = f.read()
            rel = os.path.relpath(sub, args.data)
            case = {'id': rel.replace('/', '_'), 'program': program, 'path': rel, 'source_sha': hashlib.sha256(src.encode()).hexdigest(),
                    'nondeterministic': bool(meta.get('nondeterministic'))}
            # validate the regex oracle against the recorded pass/fail table (deterministic programs)
            binary = os.path.join(tmp, 'p')
            ok = subprocess.run(GCC + ['-o', binary, src_path, '-lm'], capture_output=True).returncode == 0
            case['compiles_with_gcc'] = ok
            for s in SUITES:
                case[s] = {'pass': sorted(meta[s]['pass'], key=int), 'fail': sorted(meta[s]['fail'], key=int)}
                if ok and not case['nondeterministic']:
                    for t in tests[s]:
                        code, out = run_plain(binary, t['input'])
                        mine = code is not None and code >= 0 and re.search(t['output_regex'], out, re.S) is not None
                        recorded = t['id'] in meta[s]['pass']
                        if t['id'] not in meta[s]['pass'] and t['id'] not in meta[s]['fail']:
                            continue
                        if mine != recorded:
                            mismatch.append({'case': case['id'], 'suite': s, 'test': t['id'], 'recorded_pass': recorded,
                                             'converted_pass': mine, 'exit': code})
            cases.append(case)
        with open(os.path.join(OUT, f'tests_{program}.json'), 'w') as f:
            json.dump(tests, f, indent=1)
    with open(os.path.join(OUT, 'cases.json'), 'w') as f:
        json.dump(cases, f, indent=1)
    with open(os.path.join(OUT, 'oracle_validation.json'), 'w') as f:
        json.dump({'mismatches': mismatch}, f, indent=1)
    det = [c for c in cases if not c['nondeterministic']]
    print(f'{len(cases)} submissions, {len(det)} deterministic, {len(mismatch)} test verdicts differ from metadata')
    for s in SUITES:
        defects = [c for c in det if c[s]['fail'] and c[s]['pass']]
        print(f'{s}: {len(defects)} defects (fail >= 1 and pass >= 1), {len({c["source_sha"] for c in defects})} distinct sources')


def selected(cases, suite):
    """Defects of one suite: deterministic submissions that fail at least one and pass at least one test."""
    return [c for c in cases if not c['nondeterministic'] and c[suite]['fail'] and c[suite]['pass']]


def run(args):
    with open(os.path.join(OUT, 'cases.json')) as f:
        cases = json.load(f)
    held = 'whitebox' if args.suite == 'blackbox' else 'blackbox'
    defects = selected(cases, args.suite)
    groups = {}
    for c in defects:
        groups.setdefault((c['program'], c['source_sha']), []).append(c)
    keys = sorted(groups)
    keys = [k for i, k in enumerate(keys) if i % args.parts == args.part]
    res_path = os.path.join(OUT, f'results_{args.suite}.json')
    results = json.load(open(res_path)) if os.path.exists(res_path) else {}
    for n, key in enumerate(keys):
        program, sha = key
        rid = f'{program}_{sha[:16]}'
        if rid in results and not args.redo:
            continue
        c = groups[key][0]
        with open(os.path.join(OUT, f'tests_{program}.json')) as f:
            tests = json.load(f)
        repair = [{k: t[k] for k in ('input', 'output_regex')} for t in tests[args.suite]]
        heldout = [{k: t[k] for k in ('input', 'output_regex')} for t in tests[held]]
        work = tempfile.mkdtemp(prefix='ic-')
        tfile = os.path.join(work, 'tests.json')
        json.dump(repair, open(tfile, 'w'))
        src_path = os.path.join(args.data, c['path'], program + '.c')
        t0 = time.time()
        p = subprocess.run([sys.executable, '-m', 'cfixpass.cli', src_path, '--tests', tfile, '--work-dir',
                            os.path.join(work, 'w'), '--time-budget', str(args.budget)],
                           capture_output=True, text=True, cwd=work,
                           env=dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'src')))
        secs = time.time() - t0
        rep = {}
        rp = os.path.join(work, 'w', 'report.json')
        if os.path.exists(rp):
            rep = json.load(open(rp))
        else:
            rep = {'status': 'no report: ' + p.stderr[-1500:]}
        entry = {'program': program, 'copies': [x['id'] for x in groups[key]], 'exit': p.returncode,
                 'seconds': round(secs, 1), 'status': rep.get('status')}
        corrected = os.path.join(work, 'w', 'corrected.c')
        if p.returncode == 0 and os.path.exists(corrected):
            src = open(src_path, errors='replace').read()
            fixed = open(corrected).read()
            entry['changed'] = fixed != src
            entry['patch'] = unified(src, fixed)
            # held-out suite: compile the corrected program exactly as the benchmark does (gcc, default flags)
            binary = os.path.join(work, 'heldout')
            ok = subprocess.run(GCC + ['-o', binary, corrected, '-lm'], capture_output=True).returncode == 0
            passed = 0
            for t in heldout:
                code, out = run_plain(binary, t['input']) if ok else (None, '')
                passed += code is not None and code >= 0 and re.search(t['output_regex'], out, re.S) is not None
            entry['heldout'] = f'{passed}/{len(heldout)}'
            entry['heldout_all'] = passed == len(heldout)
        results[rid] = entry
        json.dump(results, open(res_path, 'w'), indent=1)
        subprocess.run(['rm', '-rf', work])
        print(f'[{n + 1}/{len(keys)}] {rid} exit={p.returncode} {entry.get("heldout", "")} {secs:.1f}s', flush=True)


def unified(a, b):
    import difflib
    return ''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), 'buggy.c', 'repaired.c', n=1))


def report(args):
    with open(os.path.join(OUT, 'cases.json')) as f:
        cases = json.load(f)
    summary = {}
    for suite in SUITES:
        rp = os.path.join(OUT, f'results_{suite}.json')
        if not os.path.exists(rp):
            continue
        results = json.load(open(rp))
        defects = selected(cases, suite)
        by_id = {}
        for r in results.values():
            for cid in r['copies']:
                by_id[cid] = r
        s = {'defects': len(defects), 'distinct_sources': len({(c['program'], c['source_sha']) for c in defects}),
             'attempted': sum(1 for c in defects if c['id'] in by_id), 'by_program': {}}
        for prog in PROGRAMS + ['all']:
            ds = [c for c in defects if prog in ('all', c['program'])]
            rs_all = [by_id[c['id']] for c in ds if c['id'] in by_id]
            # the original already passes the repair suite here (its recorded failure does not reproduce): not a defect
            stale = [r for r in rs_all if r['exit'] == 0 and r.get('changed') is False]
            rs = [r for r in rs_all if not (r['exit'] == 0 and r.get('changed') is False)]
            row = {'defects': len(ds) - len(stale), 'not_reproduced': len(stale),
                   'plausible': sum(1 for r in rs if r['exit'] == 0),
                   'pass_all_heldout': sum(1 for r in rs if r.get('heldout_all')),
                   'rejected_by_gate': sum(1 for r in rs if r['exit'] == 2)}
            fr = [int(r['heldout'].split('/')[0]) / int(r['heldout'].split('/')[1]) for r in rs if r.get('heldout')]
            row['mean_heldout_pass_rate'] = round(sum(fr) / len(fr), 3) if fr else None
            if prog == 'all':
                s.update(row)
                secs = sorted(r['seconds'] for r in results.values())
                s['median_seconds_per_distinct_source'] = secs[len(secs) // 2] if secs else None
            else:
                s['by_program'][prog] = row
        summary[suite] = s
    json.dump(summary, open(os.path.join(OUT, 'summary.json'), 'w'), indent=1)
    print(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['prepare', 'run', 'report'])
    ap.add_argument('--data', default=os.path.join(ROOT, 'IntroClass'))
    ap.add_argument('--suite', choices=SUITES, default='blackbox')
    ap.add_argument('--budget', type=float, default=120)
    ap.add_argument('--part', type=int, default=0)
    ap.add_argument('--parts', type=int, default=1)
    ap.add_argument('--redo', action='store_true')
    a = ap.parse_args()
    {'prepare': prepare, 'run': run, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
