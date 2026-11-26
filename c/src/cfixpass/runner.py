"""Compiling candidates and running tests: resource limits, fuel, coverage traces, deterministic output comparison."""
from __future__ import annotations

import ctypes
import functools
import os
import re
import resource
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field

from .instrument import FUEL_STATUS
from .text import line_similarity, normalize_output

MAX_OUTPUT = 1 << 20


@dataclass
class Test:
    input: str
    output: str | None = None
    regex: str | None = None
    exit_code: int | None = None

    def check(self, out: str) -> bool:
        if self.regex is not None:
            try:
                return re.search(self.regex, out, re.S) is not None
            except re.error:
                return False
        return normalize_output(out) == normalize_output(self.output or '')

    def similarity(self, out: str) -> float:
        return 0.0 if self.regex is not None else line_similarity(out, self.output or '')


def load_tests(items):
    tests = []
    for t in items:
        if not isinstance(t, dict) or not isinstance(t.get('input', ''), str):
            raise ValueError('each test needs a string "input"')
        code = t.get('exit_code')
        if code is not None and not isinstance(code, int):
            raise ValueError('"exit_code" must be an integer')
        if isinstance(t.get('output'), str):
            tests.append(Test(t['input'], output=t['output'], exit_code=code))
        elif isinstance(t.get('output_regex'), str):
            re.compile(t['output_regex'])
            tests.append(Test(t['input'], regex=t['output_regex'], exit_code=code))
        else:
            raise ValueError('each test needs a string "output" (or "output_regex")')
    return tests


@dataclass
class Outcome:
    index: int
    passed: bool
    timeout: bool = False
    crash: str | None = None        # 'signal N', 'fuel exhausted', 'output limit'
    out: str = ''
    ms: float = 0.0
    similarity: float = 0.0
    hits: tuple | None = None
    last: int = -1                  # last statement reached (coverage runs)
    fuel_used: int = 0

    def score(self):
        return 2.0 if self.passed else self.similarity


@functools.lru_cache(maxsize=1)
def compiler():
    cc = os.environ.get('CFIXPASS_CC')
    if cc:
        return cc
    for c in ('gcc', 'clang', 'cc'):
        if shutil.which(c):
            return c
    raise RuntimeError('no C compiler found (install gcc or clang, or set CFIXPASS_CC)')


@functools.lru_cache(maxsize=1)
def base_flags():
    flags = ['-std=gnu11', '-O0', '-w', '-fno-stack-protector']
    # deterministic values for uninitialized locals (otherwise behaviour depends on stack garbage)
    try:
        r = subprocess.run([compiler(), '-ftrivial-auto-var-init=zero', '-x', 'c', '-c', '-o', os.devnull, '-'],
                           input=b'int f(void){int x; return x;}', capture_output=True, timeout=30)
        if r.returncode == 0:
            flags.append('-ftrivial-auto-var-init=zero')
    except (OSError, subprocess.SubprocessError):
        pass
    return flags


def compile_c(src: str, path_base: str):
    """Compile to ``path_base`` (an executable). Returns (ok, diagnostics)."""
    c_path = path_base + '.c'
    with open(c_path, 'w') as f:
        f.write(src)
    try:
        r = subprocess.run([compiler()] + base_flags() + ['-o', path_base, c_path, '-lm'], capture_output=True,
                           timeout=60, preexec_fn=_compile_limits)
    except subprocess.TimeoutExpired:
        return False, 'compiler timeout'
    return r.returncode == 0, r.stderr.decode(errors='replace')[-4000:]


def _compile_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


_personality = None


def _child_limits(cpu_sec):
    def f():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec + 1))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if sys.platform.startswith('linux'):
            try:  # no address-space randomization: printed pointers and stack garbage stay the same between runs
                ctypes.CDLL(None).personality(0x0040000)
            except Exception:
                pass
    return f


def run(binary: str, test: Test, index: int, timeout: float, fuel: int | None, trace: bool) -> Outcome:
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C'}
    if fuel is not None:
        env['CFIXPASS_FUEL'] = str(fuel)
    with tempfile.TemporaryFile() as fin, tempfile.TemporaryFile() as fout, tempfile.TemporaryFile() as ftr:
        fin.write(test.input.encode())
        fin.seek(0)
        pass_fds = ()
        if trace:
            env['CFIXPASS_TRACE_FD'] = str(ftr.fileno())
            pass_fds = (ftr.fileno(),)
        t0 = time.time()
        timed_out = False
        try:
            p = subprocess.run([binary], stdin=fin, stdout=fout, stderr=subprocess.DEVNULL, env=env, timeout=timeout,
                               pass_fds=pass_fds, preexec_fn=_child_limits(max(1, int(timeout) + 1)))
            code = p.returncode
        except subprocess.TimeoutExpired:
            timed_out, code = True, None
        ms = (time.time() - t0) * 1000
        fout.seek(0)
        raw = fout.read(MAX_OUTPUT + 1)
        out = raw.decode('utf-8', errors='replace')
        hits, last, why, fuel_used = None, -1, 0, 0
        if trace:
            ftr.seek(0)
            data = ftr.read()
            if len(data) >= 24:
                n, last, why, _, left = struct.unpack('iiiiQ', data[:24])
                if fuel is not None:
                    fuel_used = max(0, fuel - left)
                if len(data) >= 24 + 4 * n:
                    hits = struct.unpack(f'{n}I', data[24:24 + 4 * n])
    crash = None
    if timed_out:
        crash = 'timeout'
    elif why == -1 or (code == FUEL_STATUS and fuel is not None and not trace):
        crash = 'fuel exhausted'
    elif code is not None and code < 0:
        crash = f'signal {-code}'
    elif trace and why > 0:
        crash = f'signal {why}'
    elif len(raw) >= MAX_OUTPUT:
        crash = 'output limit'
    if crash == 'fuel exhausted' and fuel is not None:
        fuel_used = fuel
    passed = crash is None and test.check(out) and (test.exit_code is None or code == test.exit_code)
    sim = 0.0 if passed else test.similarity(out)
    return Outcome(index, passed, timed_out, crash, out[:20000], ms, 1.0 if passed else sim, hits, last, fuel_used)
