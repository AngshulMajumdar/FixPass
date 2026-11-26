"""Command line: cfixpass SOURCE.c [--tests T.json] [--extra-tests E.json] [--spec S.txt] [--work-dir DIR] [-o BINARY]"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

from . import __version__, gate, runner
from .engine import Engine


def main(argv=None):
    p = argparse.ArgumentParser(prog='cfixpass', description='A repairing compiler for C (no model, CPU only).')
    p.add_argument('source', nargs='?')
    p.add_argument('--tests', help='behavioural tests: [{"input": "...", "output": "..."}]')
    p.add_argument('--extra-tests', help='more tests, appended')
    p.add_argument('--spec', help='intent text, recorded in the report')
    p.add_argument('--work-dir', help='where original.c, candidate.c, corrected.c and report.json go '
                                      '(default: ./cfixpass-out/<source name>)')
    p.add_argument('-o', '--out', help='also copy the compiled program here')
    p.add_argument('--time-budget', type=float, help='seconds for the whole repair (default 1800)')
    p.add_argument('--version', action='store_true')
    a = p.parse_args(argv)
    if a.version:
        print(f'cfixpass {__version__}')
        return 0
    if not a.source:
        p.print_usage(sys.stderr)
        return 2
    try:
        with open(a.source, encoding='utf-8', errors='replace') as f:
            src = f.read()
        tests = []
        for path in (a.tests, a.extra_tests):
            if path:
                with open(path) as f:
                    tests += runner.load_tests(json.load(f))
        if len(tests) > gate.MAX_TESTS:
            raise ValueError(f'at most {gate.MAX_TESTS} tests')
        runner.compiler()
    except (OSError, ValueError, RuntimeError) as e:
        print(f'cfixpass: {e}', file=sys.stderr)
        return 2
    work = a.work_dir
    if not work:
        stem = os.path.splitext(os.path.basename(a.source))[0] or 'program'
        work = os.path.join('cfixpass-out', stem)
        if os.path.isdir(work):
            entries = os.listdir(work)
            if entries and 'report.json' not in entries:
                print(f'cfixpass: {work} exists and is not a cfixpass output directory', file=sys.stderr)
                return 2
            shutil.rmtree(work)
    elif os.path.isdir(work) and os.listdir(work):
        print(f'cfixpass: work directory {work} must be empty or absent', file=sys.stderr)
        return 2
    eng = Engine(work, tests, a.time_budget)
    if a.spec:
        with open(a.spec) as f:
            eng.report['spec'] = f.read()[:20000]
    code = eng.run(src, a.out)
    status = eng.report.get('status', '')
    print(status)
    if code == 0:
        print(f'corrected source: {os.path.join(work, "corrected.c")}')
        print(f'program:          {a.out or os.path.join(work, "program")}')
    elif code == 1:
        print(f'candidate source: {os.path.join(work, "candidate.c")}')
    print(f'report:           {os.path.join(work, "report.json")}')
    return code


if __name__ == '__main__':
    sys.exit(main())
