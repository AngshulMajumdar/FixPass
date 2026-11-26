"""Input gate: the supported subset (one console C file, standard headers, no host access beyond stdin/stdout)."""
from __future__ import annotations

import re

from . import lexer

MAX_BYTES = 200_000
MAX_TESTS = 500

HEADERS = frozenset('''assert complex ctype errno fenv float inttypes iso646 limits locale math setjmp stdalign stdarg stdbool stddef
stdint stdio stdlib stdnoreturn string tgmath time uchar wchar wctype'''.split())

# Calls that reach outside stdin/stdout or interfere with the test harness.
BANNED = frozenset('''system popen pclose fork vfork execl execlp execle execv execvp execvpe fopen freopen fdopen remove rename tmpfile
tmpnam mkstemp socket connect bind listen accept kill signal sigaction raise setrlimit chdir unlink rmdir mkdir open creat dlopen
pthread_create thrd_create'''.split())


def check(src: str):
    """None if the source is in the supported subset, otherwise the reason it is rejected."""
    if len(src.encode('utf-8', 'replace')) > MAX_BYTES:
        return f'source exceeds {MAX_BYTES} bytes'
    if '\x00' in src:
        return 'NUL bytes are not supported'
    toks = lexer.lex(src)
    # names the program defines itself (a function or variable called 'remove' is fine)
    defined = set()
    for i in range(1, len(toks)):
        if toks[i].kind == lexer.IDENT and toks[i - 1].kind in (lexer.KEYWORD, lexer.IDENT) or \
                toks[i].kind == lexer.IDENT and toks[i - 1].text in ('*', ','):
            defined.add(toks[i].text)
    for k, t in enumerate(toks):
        if t.kind == lexer.PP:
            body = re.sub(r'\\\n', '', t.text)
            m = re.match(r'#\s*(\w*)(.*)', body, re.S)
            directive = m.group(1) if m else ''
            rest = m.group(2).strip() if m else ''
            if directive == 'include':
                h = re.fullmatch(r'<\s*([A-Za-z0-9_]+)\.h\s*>(\s*//.*|\s*/\*.*)?', rest)
                if not h:
                    return 'only standard <header.h> includes are supported: ' + body.strip()
                if h.group(1) not in HEADERS:
                    return f'header <{h.group(1)}.h> is outside the supported standard library subset'
            elif directive in ('pragma', 'import', 'include_next', 'embed'):
                return f'#{directive} is not supported'
        elif t.kind == lexer.IDENT and t.text in BANNED and t.text not in defined and k + 1 < len(toks) \
                and toks[k + 1].text == '(':
            return f"'{t.text}' is not supported (programs may use only stdin and stdout)"
        elif t.kind in (lexer.IDENT, lexer.KEYWORD) and t.text in ('asm', '__asm', '__asm__', '_Pragma'):
            return f"'{t.text}' is not supported"
    if not any(t.text == 'main' for t in toks if t.kind == lexer.IDENT):
        return 'no main function'
    return None
