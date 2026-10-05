"""Distribuição Linux: família, nomes dos pacotes e o comando de instalação.

As mensagens de erro ("falta o X") e o --check (doctor.py) dizem o comando
certo para a distribuição: Ubuntu/Debian e derivadas (apt), Fedora (dnf),
Arch (pacman) e openSUSE (zypper). Lê o /etc/os-release (ID e ID_LIKE).

Nomes conferidos: Ubuntu 24.04 (apt-cache) e Fedora 44. Arch e openSUSE:
pelos repositórios, sem teste numa máquina.
"""
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

FAMILY_IDS = {
    "debian": ("debian", "ubuntu", "linuxmint", "pop", "elementary", "zorin", "kali", "raspbian", "neon", "pureos",
               "deepin", "mx", "tuxedo", "kubuntu", "xubuntu", "lubuntu"),
    "fedora": ("fedora", "rhel", "centos", "rocky", "almalinux", "nobara", "ultramarine", "bazzite"),
    "arch": ("arch", "manjaro", "endeavouros", "cachyos", "garuda", "artix", "steamos"),
    "suse": ("opensuse", "opensuse-tumbleweed", "opensuse-leap", "opensuse-slowroll", "sles", "suse"),
}

INSTALL = {
    "debian": "sudo apt install",
    "fedora": "sudo dnf install",
    "arch": "sudo pacman -S --needed",
    "suse": "sudo zypper install",
}

# o que falta -> pacotes de cada família
PACKAGES = {
    "gi": {"debian": ["python3-gi", "gir1.2-gstreamer-1.0", "gir1.2-gst-plugins-base-1.0"],
           "fedora": ["python3-gobject", "gstreamer1"],
           "arch": ["python-gobject", "gstreamer"],
           "suse": ["python3-gobject", "typelib-1_0-Gst-1_0", "typelib-1_0-GstVideo-1_0",
                    "typelib-1_0-GstAllocators-1_0"]},
    "base": {"debian": ["gstreamer1.0-plugins-base"], "fedora": ["gstreamer1-plugins-base"],
             "arch": ["gst-plugins-base"], "suse": ["gstreamer-plugins-base"]},
    "good": {"debian": ["gstreamer1.0-plugins-good"], "fedora": ["gstreamer1-plugins-good"],
             "arch": ["gst-plugins-good"], "suse": ["gstreamer-plugins-good"]},
    "bad": {"debian": ["gstreamer1.0-plugins-bad"], "fedora": ["gstreamer1-plugins-bad-free"],
            "arch": ["gst-plugins-bad"], "suse": ["gstreamer-plugins-bad"]},
    "pipewire": {"debian": ["gstreamer1.0-pipewire"], "fedora": ["pipewire-gstreamer"],
                 "arch": ["gst-plugin-pipewire"], "suse": ["gstreamer-plugin-pipewire"]},
    "gl": {"debian": ["gstreamer1.0-gl"], "fedora": ["gstreamer1-plugins-base"],
           "arch": ["gst-plugins-base"], "suse": ["gstreamer-plugins-base"]},
    # Debian/Ubuntu: o -dev puxa a libopenh264-N da versão (7 no Ubuntu 24.04)
    "openh264": {"debian": ["libopenh264-dev"], "fedora": ["openh264", "gstreamer1-plugin-openh264"],
                 "arch": ["openh264"], "suse": ["libopenh264-7"]},
    "evdev": {"debian": ["python3-evdev"], "fedora": ["python3-evdev"], "arch": ["python-evdev"],
              "suse": ["python3-evdev"]},
    "pactl": {"debian": ["pulseaudio-utils"], "fedora": ["pulseaudio-utils"], "arch": ["libpulse"],
              "suse": ["pulseaudio-utils"]},
    "kms": {"debian": ["gcc", "make", "pkg-config", "libdrm-dev", "libcap2-bin"],
            "fedora": ["gcc", "make", "pkgconf-pkg-config", "libdrm-devel", "libcap"],
            "arch": ["gcc", "make", "pkgconf", "libdrm", "libcap"],
            "suse": ["gcc", "make", "pkgconf-pkg-config", "libdrm-devel", "libcap-progs"]},
}

# Notas por família, para o que não é só instalar um pacote.
NOTES = {
    ("openh264", "fedora"): "vem do repositório fedora-cisco-openh264, já ativo no Fedora Workstation",
    ("openh264", "suse"): "vem do repositório codecs.opensuse.org (ativo no Tumbleweed)",
}


def read_os_release(path="/etc/os-release") -> dict:
    info = {}
    try:
        text = Path(path).read_text()
    except OSError:
        return info
    for line in text.splitlines():
        m = re.match(r'^([A-Z_]+)=(.*)$', line.strip())
        if m:
            info[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return info


def family_of(info: dict) -> str:
    """'debian', 'fedora', 'arch', 'suse' ou '' (desconhecida)."""
    ids = [info.get("ID", "").lower()] + info.get("ID_LIKE", "").lower().split()
    for name in ids:
        for fam, members in FAMILY_IDS.items():
            if name in members or any(name.startswith(m + "-") for m in members):
                return fam
    return ""


@lru_cache(maxsize=1)
def current() -> tuple:
    """(família, nome bonito) desta máquina."""
    info = read_os_release()
    return family_of(info), info.get("PRETTY_NAME", "Linux")


def packages(needs, fam=None) -> list:
    """Pacotes de uma lista de necessidades ('gi', 'openh264'...), sem repetir."""
    fam = fam if fam is not None else current()[0]
    out = []
    for need in needs:
        for pkg in PACKAGES.get(need, {}).get(fam, []):
            if pkg not in out:
                out.append(pkg)
    return out


def install_command(needs, fam=None) -> str:
    """'sudo apt install ...' para esta distribuição, ou '' se ela não é conhecida."""
    fam = fam if fam is not None else current()[0]
    pkgs = packages(needs, fam)
    if not fam or not pkgs:
        return ""
    return f"{INSTALL[fam]} {' '.join(pkgs)}"


def hint(*needs, fam=None) -> str:
    """Texto curto para mensagens de erro: 'sudo apt install x' (+ nota)."""
    fam = fam if fam is not None else current()[0]
    cmd = install_command(needs, fam)
    if not cmd:
        return "instale pelo gerenciador de pacotes (veja o README, Instalação)"
    notes = [NOTES[(n, fam)] for n in needs if (n, fam) in NOTES]
    return cmd + (f" ({'; '.join(notes)})" if notes else "")


def firewall() -> str:
    """'ufw', 'firewalld' ou '' (nenhum ativo que a gente reconheça)."""
    if not shutil.which("systemctl"):
        return ""
    for name in ("firewalld", "ufw"):
        try:
            if subprocess.run(["systemctl", "is-active", "--quiet", name], capture_output=True,
                              timeout=3).returncode == 0:
                return name
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def firewall_command(port: int, name=None) -> str:
    name = firewall() if name is None else name
    if name == "ufw":
        return f"sudo ufw allow {port}/udp && sudo ufw allow {port}/tcp"
    if name == "firewalld":
        return (f"sudo firewall-cmd --permanent --add-port={port}/tcp --add-port={port}/udp "
                "&& sudo firewall-cmd --reload")
    return ""
