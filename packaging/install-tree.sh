#!/bin/sh
# Copia o servidor para DESTDIR com a estrutura dos pacotes (.deb e .rpm usam
# este mesmo script):
#
#   /usr/bin/pspstream                       atalho para o servidor
#   /usr/share/pspstream/{server,assets}     o servidor (mesma estrutura do repositório)
#   /usr/libexec/pspstream/pspstream-kms     auxiliar da captura KMS (sem permissão especial:
#                                            "pspstream --setup" oferece o setcap)
#   /usr/lib/udev/rules.d, modules-load.d    /dev/uinput para quem está sentado no PC (controles)
#   /usr/lib/systemd/user/pspstream.service  serviço de usuário (desligado)
#   /usr/share/applications/pspstream.desktop
#   /usr/share/doc/pspstream                 READMEs (en, pt-BR), CHANGELOG, LICENSE (o resto: a wiki)
#
#   packaging/install-tree.sh DESTDIR
#
# O auxiliar KMS precisa estar compilado (make -C tools/kms).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:?uso: install-tree.sh DESTDIR}
share=$dest/usr/share/pspstream

[ -x "$root/tools/kms/pspstream-kms" ] || { echo "build it first: make -C tools/kms" >&2; exit 1; }

install -d "$share/server/web" "$share/assets"
install -m 644 "$root"/server/*.py "$root/server/keymap.json" "$share/server/"
install -m 644 "$root"/server/web/* "$share/server/web/"
install -m 644 "$root/assets/testcard.jpg" "$share/assets/"
install -D -m 755 "$root/packaging/pspstream" "$dest/usr/bin/pspstream"
install -D -m 755 "$root/tools/kms/pspstream-kms" "$dest/usr/libexec/pspstream/pspstream-kms"
install -D -m 644 "$root/packaging/files/60-pspstream-uinput.rules" "$dest/usr/lib/udev/rules.d/60-pspstream-uinput.rules"
install -D -m 644 "$root/packaging/files/pspstream-uinput.conf" "$dest/usr/lib/modules-load.d/pspstream.conf"
install -D -m 644 "$root/packaging/files/pspstream.service" "$dest/usr/lib/systemd/user/pspstream.service"
install -D -m 644 "$root/packaging/files/pspstream.desktop" "$dest/usr/share/applications/pspstream.desktop"
install -d "$dest/usr/share/doc/pspstream"
install -m 644 "$root/README.md" "$root/README.pt-BR.md" "$root/CHANGELOG.md" "$root/LICENSE" "$dest/usr/share/doc/pspstream/"
