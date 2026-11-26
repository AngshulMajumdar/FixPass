#!/usr/bin/env bash
# Install JFixPass as a command-line compiler (Linux / macOS).
#
#   ./install.sh                     # installs to ~/.local  (bin/jfixpass, lib/jfixpass/jfixpass.jar)
#   ./install.sh --prefix /usr/local # system-wide (may need sudo)
#
# Requires a JDK 17+ (javac must be present: JFixPass drives the JDK's own compiler).
set -euo pipefail

VERSION="0.1.0"
PREFIX="${HOME}/.local"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $1" >&2; exit 2 ;;
  esac
done

HERE="$(cd "$(dirname "$0")" && pwd)"

if ! command -v java >/dev/null 2>&1 || ! command -v javac >/dev/null 2>&1; then
  echo "install.sh: a JDK 17 or newer is required (java and javac on PATH)." >&2
  exit 1
fi
JV="$(javac -version 2>&1 | grep -Eo 'javac [0-9]+' | grep -Eo '[0-9]+' | head -1)"
if [ -z "$JV" ] || [ "$JV" -lt 17 ]; then
  echo "install.sh: JDK 17 or newer required (found: $(javac -version 2>&1 | grep javac))." >&2
  exit 1
fi

echo "building JFixPass $VERSION ..."
bash "$HERE/scripts/build.sh" >/dev/null

LIB="$PREFIX/lib/jfixpass"
BIN="$PREFIX/bin"
mkdir -p "$LIB" "$BIN"
cp "$HERE/build/jfixpass.jar" "$LIB/jfixpass.jar"

cat > "$BIN/jfixpass" <<LAUNCHER
#!/usr/bin/env bash
# JFixPass $VERSION launcher (installed by install.sh)
#   jfixpass Source.java [--tests T.json] [--extra-tests E.json] [--spec S.txt] [--out CLASSDIR] [--work-dir DIR]
# Without --work-dir, results go to ./jfixpass-out/<Source>/ (replaced on each run).
JAR="$LIB/jfixpass.jar"
LAUNCHER
cat >> "$BIN/jfixpass" <<'LAUNCHER'
if [ "${1:-}" = "--version" ]; then echo "jfixpass VERSION_PLACEHOLDER"; exit 0; fi
has_wd=0; src=""; wd=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --work-dir) has_wd=1; wd="${args[i+1]:-}"; i=$((i + 1)) ;;
    --tests|--extra-tests|--spec|--out|-o) i=$((i + 1)) ;;
    -*) ;;
    *) src="${args[i]}" ;;
  esac
done
if [ "$has_wd" = 0 ] && [ -n "$src" ]; then
  stem="$(basename "$src")"; stem="${stem%.java}"
  wd="jfixpass-out/$stem"
  if [ -d "$wd" ]; then
    if [ -f "$wd/report.json" ] || [ -z "$(ls -A "$wd")" ]; then
      rm -rf "$wd"
    else
      echo "jfixpass: $wd exists and is not a jfixpass work directory" >&2
      exit 2
    fi
  fi
  mkdir -p jfixpass-out
  set -- "$@" --work-dir "$wd"
fi
java -Xss64m -XX:+UseSerialGC -jar "$JAR" "$@"
code=$?
if [ -n "$wd" ] && [ -f "$wd/report.json" ]; then
  if [ -f "$wd/corrected.java" ]; then
    echo "corrected source: $wd/corrected.java   classes: $wd/classes   report: $wd/report.json"
  else
    echo "candidate source: $wd/candidate.java   report: $wd/report.json"
  fi
fi
exit $code
LAUNCHER
sed -i.bak "s/VERSION_PLACEHOLDER/$VERSION/" "$BIN/jfixpass" && rm -f "$BIN/jfixpass.bak"
chmod +x "$BIN/jfixpass"

echo "installed: $BIN/jfixpass"
echo "           $LIB/jfixpass.jar"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "add to PATH:  export PATH=\"$BIN:\$PATH\"" ;;
esac
