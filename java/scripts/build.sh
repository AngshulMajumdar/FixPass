#!/usr/bin/env bash
# Build JFixPass into build/jfixpass.jar (requires JDK 17+; no other dependencies).
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build && mkdir -p build/classes
javac -Xlint:all -Werror -d build/classes src/jfixpass/*.java
jar --create --file build/jfixpass.jar --main-class jfixpass.Main -C build/classes .
echo "built build/jfixpass.jar"
