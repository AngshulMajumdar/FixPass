#!/usr/bin/env bash
# Install FixPass: jfixpass (Java) and/or cfixpass (C).
#   bash install.sh                       # both, to ~/.local
#   bash install.sh --only java|c         # one of them
#   bash install.sh --prefix /usr/local   # system-wide (may need sudo)
# jfixpass needs a JDK 17+; cfixpass needs Python 3.9+ and gcc or clang. A half whose toolchain is missing is skipped.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ONLY=""
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --prefix) PASS+=(--prefix "$2"); shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
case "$ONLY" in ""|java|c) ;; *) echo "install.sh: --only takes java or c" >&2; exit 2 ;; esac
done_any=0
if [ "$ONLY" != "c" ]; then
  if command -v javac >/dev/null 2>&1 && command -v java >/dev/null 2>&1; then
    bash "$HERE/java/install.sh" "${PASS[@]+"${PASS[@]}"}"; done_any=1
  elif [ "$ONLY" = "java" ]; then
    echo "install.sh: jfixpass needs a JDK 17 or newer (java and javac on PATH)" >&2; exit 1
  else
    echo "skipping jfixpass: no JDK found (java and javac on PATH)"
  fi
fi
if [ "$ONLY" != "java" ]; then
  if command -v python3 >/dev/null 2>&1 && { command -v gcc >/dev/null 2>&1 || command -v clang >/dev/null 2>&1 || command -v cc >/dev/null 2>&1; }; then
    bash "$HERE/c/install.sh" "${PASS[@]+"${PASS[@]}"}"; done_any=1
  elif [ "$ONLY" = "c" ]; then
    echo "install.sh: cfixpass needs python3 (3.9+) and gcc or clang" >&2; exit 1
  else
    echo "skipping cfixpass: needs python3 (3.9+) and gcc or clang"
  fi
fi
[ "$done_any" = 1 ] || { echo "install.sh: nothing installed" >&2; exit 1; }
