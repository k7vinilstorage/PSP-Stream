"""Servidor em qualquer Linux: distribuição, comandos de instalação, --check
e a busca da libopenh264.

  python3 -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import distro  # noqa: E402
import doctor  # noqa: E402
import openh264  # noqa: E402
import paths  # noqa: E402


class DistroTest(unittest.TestCase):
    CASES = {
        ("ubuntu", "debian"): "debian",
        ("linuxmint", "ubuntu debian"): "debian",
        ("pop", "ubuntu debian"): "debian",
        ("zorin", "ubuntu"): "debian",
        ("debian", ""): "debian",
        ("fedora", ""): "fedora",
        ("nobara", "rhel centos fedora"): "fedora",
        ("arch", ""): "arch",
        ("manjaro", "arch"): "arch",
        ("endeavouros", "arch"): "arch",
        ("cachyos", "arch"): "arch",
        ("opensuse-tumbleweed", "opensuse suse"): "suse",
        ("gentoo", ""): "",
        ("", ""): "",
    }

    def test_family(self):
        for (id_, like), fam in self.CASES.items():
            self.assertEqual(distro.family_of({"ID": id_, "ID_LIKE": like}), fam, id_)

    def test_os_release_parsing(self):
        with tempfile.NamedTemporaryFile("w", suffix="os-release", delete=False) as f:
            f.write('PRETTY_NAME="Linux Mint 22"\nNAME="Linux Mint"\nID=linuxmint\nID_LIKE="ubuntu debian"\n'
                    "# comentário\nVERSION_ID='22'\n")
        try:
            info = distro.read_os_release(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(info["PRETTY_NAME"], "Linux Mint 22")
        self.assertEqual(info["VERSION_ID"], "22")
        self.assertEqual(distro.family_of(info), "debian")
        self.assertEqual(distro.read_os_release("/nao/existe"), {})

    def test_every_need_has_every_family(self):
        for need, by_family in distro.PACKAGES.items():
            self.assertEqual(set(by_family), set(distro.INSTALL), need)

    def setUp(self):
        # sem o pacote instalado nesta máquina (o perfil do ufw e o serviço do firewalld)
        for name in ("UFW_PROFILE", "FIREWALLD_SERVICE"):
            patcher = mock.patch.object(distro, name, Path("/nao/existe"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_commands(self):
        self.assertEqual(distro.install_command(["openh264", "evdev"], "debian"),
                         "sudo apt install libopenh264-dev python3-evdev")
        # gl e base são o mesmo pacote no Fedora: não repete
        self.assertEqual(distro.install_command(["base", "gl"], "fedora"), "sudo dnf install gstreamer1-plugins-base")
        self.assertEqual(distro.install_command(["evdev"], "arch"), "sudo pacman -S --needed python-evdev")
        self.assertEqual(distro.install_command(["evdev"], ""), "")
        self.assertIn("fedora-cisco-openh264", distro.hint("openh264", fam="fedora"))
        self.assertIn("/wiki/Installation", distro.hint("evdev", fam=""))
        self.assertEqual(distro.firewall_command(5123, "ufw"), "sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp")
        self.assertIn("--add-port=5123/udp", distro.firewall_command(5123, "firewalld"))
        self.assertEqual(distro.firewall_command(5123, ""), "")
        # com o pacote instalado: o perfil do ufw e o serviço do firewalld
        with mock.patch.object(distro, "UFW_PROFILE", Path(__file__)), \
                mock.patch.object(distro, "FIREWALLD_SERVICE", Path(__file__)):
            self.assertEqual(distro.firewall_command(5123, "ufw"), "sudo ufw allow PSPStream")
            self.assertIn("--add-service=pspstream", distro.firewall_command(5123, "firewalld"))
            self.assertIn("5600/udp", distro.firewall_command(5600, "ufw"))  # outra porta: a regra pela porta


class DoctorTest(unittest.TestCase):
    def test_runs_here(self):
        items = doctor.run_checks(port=0)  # porta 0: não disputa a do servidor
        groups = {i.group for i in items}
        self.assertTrue({"System", "GStreamer", "Video", "Controls", "Audio", "Network"} <= groups, groups)
        for item in items:
            self.assertIn(item.state, ("ok", "warn", "missing", "info"))
        self.assertTrue(doctor.report(items))

    def test_report_collects_packages(self):
        items = [
            doctor.Item("GStreamer", "missing", "gi", ("gi",), essential=True),
            doctor.Item("Audio", "warn", "pactl", ("pactl",)),
            doctor.Item("Capture", "warn", "portal", extra=["xdg-desktop-portal", "xdg-desktop-portal-gnome"]),
            doctor.Item("Controls", "warn", "uinput", fix=doctor.UINPUT_RULE),
            doctor.Item("Video", "ok", "openh264", ("openh264",)),  # ok: não entra
        ]
        with mock.patch.object(distro, "current", return_value=("debian", "Ubuntu 24.04")):
            text = doctor.report(items)
        self.assertIn("sudo apt install python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 "
                      "pulseaudio-utils xdg-desktop-portal xdg-desktop-portal-gnome", text)
        self.assertNotIn("libopenh264", text)
        self.assertIn("60-pspstream-uinput.rules", text)
        self.assertNotIn("All set", text)
        with mock.patch.object(distro, "current", return_value=("debian", "Ubuntu 24.04")):
            self.assertIn("All set", doctor.report([doctor.Item("System", "ok", "x")]))

    def test_exit_code(self):
        missing = [doctor.Item("GStreamer", "missing", "gi", ("gi",), essential=True)]
        with mock.patch.object(doctor, "run_checks", return_value=missing), mock.patch("builtins.print"):
            self.assertEqual(doctor.main(0), 1)
        warn = [doctor.Item("Audio", "warn", "pactl", ("pactl",))]
        with mock.patch.object(doctor, "run_checks", return_value=warn), mock.patch("builtins.print"):
            self.assertEqual(doctor.main(0), 0)

    def test_check_option(self):
        import pspstream
        with mock.patch.object(doctor, "main", return_value=0) as check:
            self.assertEqual(pspstream.main(["--check", "--port", "6000"]), 0)
        check.assert_called_once_with(6000, pspstream.VERSION)


class SetupTest(unittest.TestCase):
    ITEMS = [
        doctor.Item("GStreamer", "warn", "pipewiresrc", ("pipewire",)),
        doctor.Item("Controls", "warn", "uinput", ("evdev",), fix=doctor.UINPUT_RULE),
        doctor.Item("Capture", "info", "KMS helper not built", ("kms",), fix="make -C tools/kms"),
        doctor.Item("Network", "info", "ufw firewall active", fix="sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp"),
        doctor.Item("Audio", "ok", "pactl", ("pactl",)),
    ]

    def test_plan(self):
        steps = dict(doctor.plan(self.ITEMS, "debian"))
        titles = list(steps)
        self.assertEqual(steps[titles[0]], ["sudo apt install gstreamer1.0-pipewire python3-evdev"])
        uinput = steps["Open /dev/uinput for the controls"]
        self.assertEqual(len(uinput), 3)  # a linha continuada com \\ vira uma só
        self.assertTrue(uinput[1].startswith("printf") and uinput[1].endswith("60-pspstream-uinput.rules"))
        kms = next(v for k, v in steps.items() if k.startswith("KMS capture"))
        self.assertTrue(kms[0].startswith("sudo apt install gcc make pkg-config libdrm-dev"))
        self.assertEqual(steps["Open the port in the firewall"], ["sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp"])
        # distribuição desconhecida: sem o passo dos pacotes
        self.assertNotIn("Install the missing packages", dict(doctor.plan(self.ITEMS, "")))

    def test_setup_runs_only_what_was_confirmed(self):
        answers = iter(["y", "n", "", "n"])
        with mock.patch.object(doctor, "run_checks", return_value=self.ITEMS), \
                mock.patch.object(distro, "current", return_value=("debian", "Ubuntu 24.04")), \
                mock.patch.object(doctor.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                mock.patch.object(doctor, "main", return_value=0), mock.patch("builtins.print"):
            self.assertEqual(doctor.setup(5123, "t", ask=lambda _: next(answers)), 0)
        run.assert_called_once_with("sudo apt install gstreamer1.0-pipewire python3-evdev", shell=True)

    def test_setup_without_terminal(self):
        def eof(_):
            raise EOFError
        with mock.patch.object(doctor, "run_checks", return_value=self.ITEMS), \
                mock.patch.object(doctor.subprocess, "run") as run, \
                mock.patch.object(doctor, "main", return_value=0), mock.patch("builtins.print"):
            doctor.setup(5123, "t", ask=eof)
        run.assert_not_called()


class KmsPermissionTest(unittest.TestCase):
    """A permissão do auxiliar KMS (setcap cap_sys_admin+ep): lida do atributo
    do arquivo, e a mensagem diz qual arquivo e o comando certo."""

    PKG = Path("/usr/libexec/pspstream/pspstream-kms")

    @staticmethod
    def xattr(magic, permitted):
        import struct
        return struct.pack("<IIIII", magic, permitted, 0, 0, 0)

    def has(self, data):
        with mock.patch("os.getxattr", return_value=data):
            return paths.has_cap_sys_admin(self.PKG)

    def test_reads_the_capability(self):
        self.assertTrue(self.has(self.xattr(0x02000001, 1 << 21)))     # cap_sys_admin=ep
        self.assertFalse(self.has(self.xattr(0x02000000, 1 << 21)))    # só +p: não vale ao rodar
        self.assertFalse(self.has(self.xattr(0x02000001, 1 << 13)))    # outra (cap_net_raw)
        self.assertFalse(self.has(b"\x01"))
        with mock.patch("os.getxattr", side_effect=OSError(61, "sem atributo")):
            self.assertFalse(paths.has_cap_sys_admin(self.PKG))

    def test_fix_for_repo_and_package(self):
        self.assertEqual(paths.kms_fix(self.PKG), f"sudo setcap cap_sys_admin+ep {self.PKG}")
        self.assertEqual(paths.kms_fix(paths.REPO_KMS_HELPER), f"make -C {ROOT / 'tools' / 'kms'} cap")

    def test_problem_without_permission(self):
        with mock.patch.object(paths, "has_cap_sys_admin", return_value=False):
            msg = paths.kms_permission_problem(self.PKG)
            self.assertIn(f"sudo setcap cap_sys_admin+ep {self.PKG}", msg)
            self.assertNotIn("make", msg)
            msg = paths.kms_permission_problem(paths.REPO_KMS_HELPER)
            self.assertIn("make -C", msg)
            self.assertIn("again after every make", msg)

    def test_problem_with_permission_ignored(self):
        nosuid = mock.Mock(f_flag=os.ST_NOSUID)
        with mock.patch.object(paths, "has_cap_sys_admin", return_value=True):
            with mock.patch("os.statvfs", return_value=nosuid):
                self.assertIn("nosuid", paths.kms_permission_problem(self.PKG))
            with mock.patch("os.statvfs", return_value=mock.Mock(f_flag=0)), \
                    mock.patch.object(paths, "no_new_privs", return_value=True):
                self.assertIn("no_new_privs", paths.kms_permission_problem(self.PKG))
            with mock.patch("os.statvfs", return_value=mock.Mock(f_flag=0)), \
                    mock.patch.object(paths, "no_new_privs", return_value=False):
                self.assertIn("has the permission", paths.kms_permission_problem(self.PKG))

    def test_no_new_privs_here(self):
        status = Path("/proc/self/status").read_text()
        self.assertEqual(paths.no_new_privs(), "NoNewPrivs:\t1" in status)

    def test_doctor(self):
        def check(cap, ignored=""):
            items = []
            with mock.patch.object(paths, "kms_helper", return_value=self.PKG), \
                    mock.patch.object(Path, "exists", return_value=True), \
                    mock.patch.object(paths, "has_cap_sys_admin", return_value=cap), \
                    mock.patch.object(paths, "kms_cap_ignored", return_value=ignored):
                doctor.check_kms(items)
            return items[0]
        self.assertEqual(check(True).state, "ok")
        item = check(True, "x está numa partição montada com nosuid")
        self.assertEqual(item.state, "warn")
        self.assertIn("nosuid", item.text)
        self.assertEqual(check(False).fix, f"sudo setcap cap_sys_admin+ep {self.PKG}")


class OpenH264SearchTest(unittest.TestCase):
    def test_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in ("libopenh264-2.4.1-linux64.7.so", "libopenh264-2.4.1-linux64.7.so.bz2", "outra.so"):
                (d / name).write_bytes(b"")
            with mock.patch.object(openh264, "LIB_DIRS", (d, d / "nao-existe")), \
                    mock.patch.dict(os.environ, {"PSPSTREAM_OPENH264": "/opt/x/libopenh264.so"}):
                got = list(openh264.lib_candidates())
        self.assertEqual(got[0], "/opt/x/libopenh264.so")  # a variável vem primeiro
        self.assertEqual(got[1:1 + len(openh264.LIB_NAMES)], list(openh264.LIB_NAMES))
        self.assertEqual(got[-1], str(d / "libopenh264-2.4.1-linux64.7.so"))  # o .bz2 não
        self.assertEqual(len(got), 2 + len(openh264.LIB_NAMES))


class GstCompatTest(unittest.TestCase):
    def test_unknown_property_is_left_out(self):
        try:
            import gst_source
        except (ImportError, ValueError):
            self.skipTest("sem GStreamer")
        with mock.patch.object(gst_source, "has_property", return_value=False):
            desc = gst_source.build_pipeline("videotestsrc", 480, 272, 60, 60)
        self.assertNotIn("n-threads", desc)
        with mock.patch.object(gst_source, "has_property", return_value=True):
            self.assertIn("n-threads", gst_source.build_pipeline("videotestsrc", 480, 272, 60, 60))


if __name__ == "__main__":
    unittest.main()
