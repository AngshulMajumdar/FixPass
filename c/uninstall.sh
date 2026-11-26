#!/usr/bin/env bash
# Remove cfixpass installed by install.sh. Usage: bash uninstall.sh [--prefix DIR]   (default: ~/.local)
set -euo pipefail
PREFIX="$HOME/.local"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --prefix=*) PREFIX="${1#*=}"; shift ;;
    *) echo "uninstall.sh: unknown option $1" >&2; exit 2 ;;
  esac
done
rm -rf "$PREFIX/lib/cfixpass"
rm -f "$PREFIX/bin/cfixpass"
echo "removed cfixpass from $PREFIX"
