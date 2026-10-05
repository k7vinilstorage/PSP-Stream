#!/bin/sh
# Pacote .rpm para o Fedora:
#
#   packaging/build-rpm.sh [args do rpmbuild]   -> dist/pspstream-VERSÃO-1.fcNN.ARQ.rpm
#
# Precisa de: rpm-build, gcc, make, libdrm-devel e git (o Source0 sai dos
# arquivos do repositório, inclusive mudanças ainda não commitadas).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sh "$root/packaging/version.sh")
out=$root/dist
mkdir -p "$out"
top=$(mktemp -d)
trap 'rm -rf "$top"' EXIT
mkdir -p "$top/SOURCES" "$top/SPECS"
# tar dos arquivos do repositório (sem builds locais), com a pasta pspstream-VERSÃO
(cd "$root" && git ls-files --cached --others --exclude-standard | grep -v '^dist/' |
    tar -czf "$top/SOURCES/pspstream-$version.tar.gz" --transform "s,^,pspstream-$version/," -T -)
rpmbuild -bb --quiet --define "_topdir $top" --define "pspversion $version" "$@" "$root/packaging/pspstream.spec"
for f in "$top"/RPMS/*/*.rpm; do
    cp "$f" "$out/"
    echo "$out/$(basename "$f")"
done
