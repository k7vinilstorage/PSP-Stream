#!/bin/sh
# Notas da release: a seção "## VERSÃO" do CHANGELOG.md (até a próxima "## ").
#   packaging/release-notes.sh 1.2
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
awk -v v="$1" '
    /^## / { if (found) exit; if ($2 == v) { found = 1; next } }
    found { print }
' "$root/CHANGELOG.md"
