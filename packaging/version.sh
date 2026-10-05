#!/bin/sh
# Versão dos pacotes. Numa tag (v1.2 ou v1.2.0): a da tag. Fora dela: a do
# servidor (VERSION em server/pspstream.py) + ~git<data>.<commit>, que fica
# antes da versão final na ordem do apt e do dnf (1.1~git... < 1.1).
# PSPSTREAM_VERSION, se definida, vale mais.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
if [ -n "${PSPSTREAM_VERSION:-}" ]; then
    echo "$PSPSTREAM_VERSION"
    exit 0
fi
tag=$(git -C "$root" describe --tags --exact-match --match 'v[0-9]*' 2>/dev/null || true)
if [ -n "$tag" ]; then
    echo "${tag#v}"
    exit 0
fi
base=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$root/server/pspstream.py")
when=$(git -C "$root" log -1 --format=%cd --date=format:%Y%m%d 2>/dev/null || date -u +%Y%m%d)
commit=$(git -C "$root" rev-parse --short HEAD 2>/dev/null || echo local)
echo "${base}~git${when}.${commit}"
