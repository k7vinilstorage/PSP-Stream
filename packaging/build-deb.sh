#!/bin/sh
# Pacote .deb para Ubuntu 22.04+ e Debian 12+ (e derivados), sem debhelper:
#
#   packaging/build-deb.sh            -> dist/pspstream_VERSÃO_ARQ.deb
#
# Precisa de: dpkg-deb, gcc, make, pkg-config e libdrm-dev (auxiliar KMS).
# Compile num Ubuntu 22.04 para o pacote servir no 22.04 e nos mais novos
# (a glibc do auxiliar KMS).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
version=$(sh "$root/packaging/version.sh")
arch=$(dpkg --print-architecture)
out=$root/dist
mkdir -p "$out"

make -C "$root/tools/kms" -B CFLAGS="-O2 -Wall -Wextra -fstack-protector-strong -D_FORTIFY_SOURCE=2" \
    LDFLAGS="-Wl,-z,relro -Wl,-z,now" >/dev/null
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
chmod 755 "$stage"  # o mktemp cria com 0700, e essa é a permissão do "./" no pacote
sh "$root/packaging/install-tree.sh" "$stage"
install -D -m 644 "$root/packaging/files/ufw-pspstream" "$stage/etc/ufw/applications.d/pspstream"
mv "$stage/usr/share/doc/pspstream/LICENSE" "$stage/usr/share/doc/pspstream/copyright"
gzip -9n "$stage/usr/share/doc/pspstream/CHANGELOG.md"

mkdir "$stage/DEBIAN"
cat > "$stage/DEBIAN/control" <<CONTROL
Package: pspstream
Version: $version
Architecture: $arch
Maintainer: k7vinilstorage <k7vinilstorage@users.noreply.github.com>
Installed-Size: $(du -sk "$stage" | cut -f1)
Depends: python3 (>= 3.10), python3-gi, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0,
 gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-bad, gstreamer1.0-pipewire,
 gstreamer1.0-gl, python3-evdev, libc6 (>= 2.34), libdrm2
Recommends: libopenh264-7 | libopenh264-6 | libopenh264-8, pulseaudio-utils, xdg-desktop-portal
Suggests: libcap2-bin
Section: video
Priority: optional
Homepage: https://github.com/k7vinilstorage/PSP-Stream
Description: tela e som do PC no PSP (servidor do PSPStream)
 Transmite a tela e o som do PC para um PSP com o EBOOT do PSPStream pelo
 Wi-Fi (H.264 com frames P, decodificado pelo hardware do PSP, e IMA ADPCM),
 e manda os botões do PSP de volta como teclado e mouse ou como um controle
 de Xbox virtual.
 .
 Depois de instalar: pspstream --check (confere tudo), pspstream (servidor)
 e http://localhost:5124 (configurações).
CONTROL
echo /etc/ufw/applications.d/pspstream > "$stage/DEBIAN/conffiles"
# A permissão de ler a tela (setcap no auxiliar KMS) é opcional e só o
# administrador dá, mas some quando a atualização troca o arquivo: se ela
# existia, o preinst marca e o postinst a devolve.
cat > "$stage/DEBIAN/preinst" <<'PREINST'
#!/bin/sh
set -e
if [ "$1" = upgrade ] && command -v getcap >/dev/null 2>&1 &&
    getcap /usr/libexec/pspstream/pspstream-kms 2>/dev/null | grep -q cap_sys_admin; then
    touch /run/pspstream-kms-cap 2>/dev/null || true
fi
PREINST
cat > "$stage/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    # controles: /dev/uinput agora, sem reiniciar (a regra do udev vale para a sessão ativa)
    modprobe uinput 2>/dev/null || true
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules 2>/dev/null || true
        udevadm trigger --subsystem-match=misc --sysname-match=uinput 2>/dev/null || true
    fi
    if [ -e /run/pspstream-kms-cap ]; then
        setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms 2>/dev/null || true
        rm -f /run/pspstream-kms-cap
    fi
fi
POSTINST
chmod 755 "$stage/DEBIAN/preinst" "$stage/DEBIAN/postinst"
(cd "$stage" && find usr -type f -exec md5sum {} + | sort -k2) > "$stage/DEBIAN/md5sums"

file="$out/pspstream_${version}_${arch}.deb"
dpkg-deb --build --root-owner-group -Zxz "$stage" "$file" >/dev/null
echo "$file"
