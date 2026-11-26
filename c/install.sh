#!/usr/bin/env bash
# Install cfixpass: a private Python environment in PREFIX/lib/cfixpass and the command PREFIX/bin/cfixpass.
# Usage: bash install.sh [--prefix DIR]        (default prefix: ~/.local)
set -euo pipefail
PREFIX="$HOME/.local"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    -h|--help) echo "usage: bash install.sh [--prefix DIR]"; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null || { echo "install.sh: python3 (3.9 or newer) is required" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || { echo "install.sh: python 3.9 or newer is required" >&2; exit 1; }
if ! command -v gcc >/dev/null && ! command -v clang >/dev/null && ! command -v cc >/dev/null; then
  echo "install.sh: a C compiler is required (gcc or clang)" >&2; exit 1
fi
LIB="$PREFIX/lib/cfixpass"
BIN="$PREFIX/bin"
mkdir -p "$LIB" "$BIN"
rm -rf "$LIB/venv"
"$PY" -m venv "$LIB/venv"
"$LIB/venv/bin/python" -m pip install --quiet --upgrade pip
"$LIB/venv/bin/python" -m pip install --quiet "$HERE"
cat > "$BIN/cfixpass" <<LAUNCH
#!/usr/bin/env bash
exec "$LIB/venv/bin/python" -m cfixpass.cli "\$@"
LAUNCH
chmod +x "$BIN/cfixpass"
"$BIN/cfixpass" --version
echo "installed: $BIN/cfixpass"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "add to your shell profile:  export PATH=\"$BIN:\$PATH\"" ;;
esac
