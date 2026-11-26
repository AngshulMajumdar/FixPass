#!/usr/bin/env bash
# Remove FixPass installed by install.sh.   bash uninstall.sh [--prefix DIR]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
bash "$HERE/java/uninstall.sh" "$@"
bash "$HERE/c/uninstall.sh" "$@"
