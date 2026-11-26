#!/usr/bin/env bash
# Remove an installation made by install.sh.
#   ./uninstall.sh                     # from ~/.local
#   ./uninstall.sh --prefix /usr/local
set -euo pipefail
PREFIX="${HOME}/.local"
if [ "${1:-}" = "--prefix" ]; then PREFIX="$2"; fi
if [ -f "$PREFIX/bin/jfixpass" ] && grep -q "JFixPass" "$PREFIX/bin/jfixpass"; then
  rm -f "$PREFIX/bin/jfixpass"
fi
rm -rf "$PREFIX/lib/jfixpass"
echo "removed JFixPass from $PREFIX"
