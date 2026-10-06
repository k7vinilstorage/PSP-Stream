# Pacote .rpm (Fedora). Gerado por packaging/build-rpm.sh, que define a versão
# (pspversion) e monta o Source0 a partir do repositório.
%{!?pspversion: %global pspversion 1.1}
%global debug_package %{nil}

Name:           pspstream
Version:        %{pspversion}
Release:        1%{?dist}
Summary:        PC screen and audio on the PSP (PSPStream server)
License:        MIT
URL:            https://github.com/k7vinilstorage/PSP-Stream
Source0:        pspstream-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  make
BuildRequires:  pkgconfig(libdrm)
Requires:       python3 >= 3.10
Requires:       python3-gobject
Requires:       gstreamer1-plugins-base
Requires:       gstreamer1-plugins-good
Requires:       gstreamer1-plugins-bad-free
Requires:       pipewire-gstreamer
Requires:       python3-evdev
# H.264 com frames P (repositório fedora-cisco-openh264, ativo no Fedora Workstation); sem ela, JPEG
Recommends:     gstreamer1-plugin-openh264
Recommends:     pulseaudio-utils
Recommends:     xdg-desktop-portal

%description
Streams the PC screen and audio to a PSP running the PSPStream EBOOT over
Wi-Fi (H.264 with P frames, decoded by the PSP hardware, and IMA ADPCM),
and sends the PSP buttons back as keyboard and mouse or as a virtual Xbox
controller.

After installing: pspstream --check (checks everything), pspstream (server)
and http://localhost:5124 (settings).

%prep
%autosetup -n pspstream-%{version}

%build
%set_build_flags
%make_build -C tools/kms

%install
sh packaging/install-tree.sh %{buildroot}
install -D -m 644 packaging/files/firewalld-pspstream.xml %{buildroot}%{_prefix}/lib/firewalld/services/pspstream.xml

%pre
# A permissão de ler a tela (setcap no auxiliar KMS) é opcional e só o
# administrador dá, mas some quando a atualização troca o arquivo: se ela
# existia, o %post a devolve.
if [ "$1" -gt 1 ] && getcap %{_prefix}/libexec/pspstream/pspstream-kms 2>/dev/null | grep -q cap_sys_admin; then
    touch /run/pspstream-kms-cap 2>/dev/null || :
fi

%post
# controles: /dev/uinput agora, sem reiniciar (a regra do udev vale para a sessão ativa)
modprobe uinput >/dev/null 2>&1 || :
udevadm control --reload-rules >/dev/null 2>&1 || :
udevadm trigger --subsystem-match=misc --sysname-match=uinput >/dev/null 2>&1 || :
if [ -e /run/pspstream-kms-cap ]; then
    setcap cap_sys_admin+ep %{_prefix}/libexec/pspstream/pspstream-kms >/dev/null 2>&1 || :
    rm -f /run/pspstream-kms-cap
fi

%files
%{_bindir}/pspstream
%{_datadir}/pspstream/
%{_prefix}/libexec/pspstream/
%{_prefix}/lib/udev/rules.d/60-pspstream-uinput.rules
%{_prefix}/lib/modules-load.d/pspstream.conf
%{_prefix}/lib/systemd/user/pspstream.service
%{_prefix}/lib/firewalld/services/pspstream.xml
%{_datadir}/applications/pspstream.desktop
%{_docdir}/pspstream/
