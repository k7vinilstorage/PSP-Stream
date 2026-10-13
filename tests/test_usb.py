"""Transporte USB (server/transports.UsbTransport e server/usb_link.py), sem PSP e sem o pyusb: os
endpoints e o módulo usb são falsos."""
import errno
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import doctor  # noqa: E402
import protocol  # noqa: E402
import transports  # noqa: E402
import usb_link  # noqa: E402
from transports import UsbTransport, parse_usb_requests  # noqa: E402


class FakeUSBError(OSError):
    def __init__(self, msg, errno=None):
        super().__init__(errno, msg)
        self.errno = errno


def fake_usb_module():
    """O pedaço do pyusb que o usb_link usa."""
    core = types.SimpleNamespace(USBError=FakeUSBError, find=MagicMock(return_value=None))
    util = types.SimpleNamespace(claim_interface=MagicMock(), dispose_resources=MagicMock())
    return types.SimpleNamespace(core=core, util=util)


class EndpointIn:
    """O EP 0x81: devolve os pacotes da fila; sem nada, o timeout do libusb (errno 110)."""

    def __init__(self, packets=()):
        self.packets = list(packets)
        self.bEndpointAddress = 0x81

    def read(self, size, timeout=500):
        if not self.packets:
            time.sleep(0.005)
            raise TimeoutError(110, "Operation timed out")
        return self.packets.pop(0)


class EndpointOut:
    def __init__(self, maxp=512):
        self.writes = []
        self.bEndpointAddress = 0x02
        self.wMaxPacketSize = maxp

    @property
    def written(self) -> bytes:
        return b"".join(self.writes)

    def write(self, data, timeout=3000):
        self.writes.append(bytes(data))
        return len(data)


class FakeSession:
    def __init__(self):
        self.alive = True
        self.requests = []

    def on_request(self, req):
        self.requests.append(req)
        if req.flags & protocol.REQ_BYE:
            self.close()

    def close(self):
        self.alive = False


def wait_for(cond, timeout=2.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        time.sleep(0.005)
    return cond()


class UsbTestCase(unittest.TestCase):
    def setUp(self):
        self.usb = fake_usb_module()
        for p in (patch.object(usb_link, "usb", self.usb), patch.object(usb_link, "HAVE_PYUSB", True)):
            p.start()
            self.addCleanup(p.stop)


class ParseTest(unittest.TestCase):
    def test_partial_request_waits_for_the_rest(self):
        data = protocol.Request(buttons=7, flags=protocol.REQ_FRAME).pack()
        buf = bytearray(data[:30])
        self.assertEqual(parse_usb_requests(buf), [])
        buf += data[30:] + data[:10]
        reqs = parse_usb_requests(buf)
        self.assertEqual([r.buttons for r in reqs], [7])
        self.assertEqual(bytes(buf), data[:10])

    def test_window_request_carries_the_frame(self):
        data = protocol.Request(flags=protocol.REQ_FRAME | protocol.REQ_NACK).pack() + protocol.pack_nack(42, ())
        buf = bytearray(data[:-1])
        self.assertEqual(parse_usb_requests(buf), [])  # o NACK ainda incompleto
        buf += data[-1:]
        self.assertEqual([r.want_frame for r in parse_usb_requests(buf)], [42])
        self.assertEqual(buf, bytearray())

    def test_garbage_raises(self):
        with self.assertRaises(ValueError):
            parse_usb_requests(bytearray(b"x" * protocol.REQ_STRUCT.size))


class UsbTransportTest(UsbTestCase):
    def setUp(self):
        super().setUp()
        self.ep_in, self.ep_out = EndpointIn(), EndpointOut()
        self.dev = object()
        self.transport = UsbTransport(self.dev, self.ep_in, self.ep_out)

    def start(self):
        session = FakeSession()
        self.transport.start(session)
        self.addCleanup(self.transport.close)
        return session

    def test_metadata(self):
        self.assertEqual((self.transport.name, self.transport.addr), ("usb", ("USB", 0)))

    def test_frame_is_header_and_jpeg_in_one_write(self):
        jpeg = b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9"
        self.assertEqual(self.transport.send_frame(1, jpeg, 123456), len(jpeg))
        self.assertEqual(len(self.ep_out.writes), 1)
        magic, frame_no, size, ts = protocol.FRAME_HDR_STRUCT.unpack_from(self.ep_out.written)
        self.assertEqual((magic, frame_no, size, ts), (protocol.MAGIC_FRAME, 1, len(jpeg), 123456))
        self.assertEqual(self.ep_out.written[protocol.FRAME_HDR_STRUCT.size:], jpeg)

    def test_zero_length_packet_ends_a_multiple_of_the_packet_size(self):
        # sem o pacote curto, o pedido de 32 KB do PSP não termina e o frame fica preso
        jpeg = b"\x00" * (2 * 512 - protocol.FRAME_HDR_STRUCT.size)
        self.transport.send_frame(1, jpeg, 0)
        self.assertEqual([len(w) for w in self.ep_out.writes], [1024, 0])
        self.ep_out.writes.clear()
        self.transport.send_frame(2, jpeg + b"\x00", 0)
        self.assertEqual([len(w) for w in self.ep_out.writes], [1025])

    def test_audio_and_pong(self):
        audio = protocol.pack_audio(seq=1, pos=100, rate=44100, channels=2, samples=128, block=b"testblock")
        self.assertEqual(self.transport.send_audio(audio), len(audio))
        self.transport.send_pong(0x12345678)
        self.assertEqual(self.ep_out.writes[0], audio)
        self.assertEqual(protocol.PONG_STRUCT.unpack(self.ep_out.writes[1]), (protocol.MAGIC_PONG, 0x12345678))

    def test_reader_delivers_requests(self):
        self.ep_in.packets.append(protocol.Request(buttons=1, lx=120, ly=130, flags=protocol.REQ_FRAME).pack())
        session = self.start()
        self.assertTrue(wait_for(lambda: session.requests))
        self.assertEqual((session.requests[0].buttons, session.requests[0].lx, session.requests[0].ly), (1, 120, 130))

    def test_pending_hello_comes_first(self):
        hello = protocol.Request(flags=protocol.REQ_HELLO | protocol.REQ_FRAME)
        buf = bytearray(protocol.Request(buttons=2, flags=protocol.REQ_FRAME).pack())
        self.transport = UsbTransport(self.dev, self.ep_in, self.ep_out, pending=[hello], buf=buf)
        session = self.start()
        self.assertTrue(wait_for(lambda: len(session.requests) == 2))
        self.assertTrue(session.requests[0].flags & protocol.REQ_HELLO)
        self.assertEqual(session.requests[1].buttons, 2)

    def test_ping_is_answered_and_not_delivered(self):
        # como no UDP: o PING não passa pela sessão
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_PING, echo_ts=0xCAFEBABE).pack())
        session = self.start()
        self.assertTrue(wait_for(lambda: self.ep_out.writes))
        self.assertEqual(protocol.PONG_STRUCT.unpack(self.ep_out.writes[0]), (protocol.MAGIC_PONG, 0xCAFEBABE))
        time.sleep(0.05)
        self.assertEqual(session.requests, [])

    def test_bye_ends_the_session_and_the_reader_frees_the_device(self):
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_BYE).pack())
        session = self.start()
        self.assertTrue(wait_for(lambda: not session.alive))
        self.assertTrue(wait_for(lambda: self.usb.util.dispose_resources.called))
        self.usb.util.dispose_resources.assert_called_once_with(self.dev)
        self.transport.send_frame(1, b"x", 0)  # depois de fechado não escreve mais
        self.assertEqual(self.ep_out.writes, [])

    def test_idle_timeout(self):
        # o PSP parou sem sair do cabo (tela de configuração): a sessão não fica viva para sempre
        with patch.object(transports, "IDLE_TIMEOUT_S", 0.05):
            session = self.start()
            self.assertTrue(wait_for(lambda: not session.alive))

    def test_close_leaves_the_device_to_the_reader(self):
        # fechar o aparelho com uma leitura em andamento em outra thread é uso depois de liberar no libusb
        reading = threading.Event()
        release = threading.Event()
        freed_by = []

        def read(size, timeout=500):
            reading.set()
            release.wait(2)
            raise TimeoutError(110, "Operation timed out")
        self.ep_in.read = read
        self.usb.util.dispose_resources.side_effect = lambda dev: freed_by.append(threading.current_thread().name)
        self.start()
        self.assertTrue(reading.wait(2))
        self.transport.close()
        self.assertEqual(freed_by, [])  # a leitura ainda está no libusb
        release.set()
        self.assertTrue(wait_for(lambda: freed_by))
        self.assertEqual(freed_by, ["usb-reader"])

    def test_close_before_start_frees_the_device(self):
        self.transport.close()
        self.usb.util.dispose_resources.assert_called_once_with(self.dev)


class FakeServer:
    running = True

    def __init__(self, cur=None):
        self.cur = cur
        self.replaced = []

    def session(self):
        return self.cur if self.cur is not None and self.cur.alive else None

    def replace(self, transport):
        self.replaced.append(transport)
        self.cur = types.SimpleNamespace(transport=transport, alive=True)
        return self.cur


class UsbMonitorTest(UsbTestCase):
    def setUp(self):
        super().setUp()
        self.ep_in, self.ep_out = EndpointIn(), EndpointOut()
        self.dev = object()
        self.opened = patch.object(usb_link, "open_device", return_value=(self.dev, self.ep_in, self.ep_out))
        self.open_device = self.opened.start()
        self.addCleanup(self.opened.stop)
        self.usb.core.find.return_value = self.dev

    def wifi_session(self):
        return types.SimpleNamespace(transport=types.SimpleNamespace(name="udp"), alive=True)

    def test_psp_on_the_cable_does_not_take_a_wifi_session(self):
        # o driver USB do PSP fica ativo depois de trocar para o Wi-Fi: sem pedido, nada muda
        server = FakeServer(self.wifi_session())
        monitor = usb_link.UsbMonitor(server)
        for _ in range(3):
            monitor.step()
        self.assertEqual(server.replaced, [])
        # pedidos atrasados de um stream USB anterior também não
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_FRAME).pack())
        monitor.step()
        self.assertEqual(server.replaced, [])

    def test_hello_opens_the_session(self):
        server = FakeServer(self.wifi_session())
        monitor = usb_link.UsbMonitor(server)
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_HELLO | protocol.REQ_FRAME).pack()
                                  + protocol.Request(buttons=5, flags=protocol.REQ_FRAME).pack())
        monitor.step()
        self.assertEqual(len(server.replaced), 1)
        transport = server.replaced[0]
        self.assertEqual(transport.name, "usb")
        self.assertEqual([bool(r.flags & protocol.REQ_HELLO) for r in transport.pending], [True, False])
        self.assertIsNone(monitor.link)  # o aparelho agora é da sessão
        # com a sessão USB viva, o monitor não mexe no aparelho
        monitor.step()
        self.assertEqual(self.open_device.call_count, 1)

    def test_any_request_opens_when_there_is_no_session(self):
        server = FakeServer()
        monitor = usb_link.UsbMonitor(server)
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_FRAME).pack())
        monitor.step()
        self.assertEqual(len(server.replaced), 1)

    def test_bye_does_not_open(self):
        server = FakeServer()
        monitor = usb_link.UsbMonitor(server)
        self.ep_in.packets.append(protocol.Request(flags=protocol.REQ_BYE).pack())
        monitor.step()
        self.assertEqual(server.replaced, [])

    def test_garbage_is_skipped(self):
        server = FakeServer(self.wifi_session())
        monitor = usb_link.UsbMonitor(server)
        self.ep_in.packets += [b"junk" * 20, protocol.Request(flags=protocol.REQ_HELLO).pack()]
        monitor.step()
        monitor.step()
        self.assertEqual(len(server.replaced), 1)

    def test_permission_warning_once(self):
        self.open_device.side_effect = FakeUSBError("Access denied", errno.EACCES)
        monitor = usb_link.UsbMonitor(FakeServer())
        with self.assertLogs("pspstream.usb", "WARNING") as logs:
            for _ in range(5):
                self.assertFalse(monitor.step())
        self.assertEqual(len(logs.records), 1)
        self.assertIn("udev", logs.output[0])

    def test_device_gone_is_dropped(self):
        monitor = usb_link.UsbMonitor(FakeServer(self.wifi_session()))
        monitor.step()
        self.assertIsNotNone(monitor.link)

        def gone(size, timeout=500):
            raise FakeUSBError("No such device", errno.ENODEV)
        self.ep_in.read = gone
        with self.assertRaises(FakeUSBError):
            monitor.step()
        monitor._drop()
        self.assertIsNone(monitor.link)
        self.usb.util.dispose_resources.assert_called_with(self.dev)


class UsbLinkTest(UsbTestCase):
    def test_find(self):
        dev = object()
        self.usb.core.find.return_value = dev
        self.assertIs(usb_link.find_psp_usb(), dev)
        self.usb.core.find.assert_called_once_with(idVendor=usb_link.PSP_VID, idProduct=usb_link.PSP_PID)

    def test_open_device(self):
        dev = MagicMock()
        dev.is_kernel_driver_active.return_value = False
        ep_in, ep_out = types.SimpleNamespace(bEndpointAddress=0x81), types.SimpleNamespace(bEndpointAddress=0x02)
        cfg = MagicMock()
        cfg.bConfigurationValue = 1
        cfg.__getitem__.return_value = [ep_in, ep_out]
        dev.get_active_configuration.return_value = cfg
        self.assertEqual(usb_link.open_device(dev), (dev, ep_in, ep_out))
        dev.set_configuration.assert_not_called()
        self.usb.util.claim_interface.assert_called_once_with(dev, 0)

    def test_open_device_without_permission(self):
        dev = MagicMock()
        dev.get_active_configuration.side_effect = FakeUSBError("Access denied", errno.EACCES)
        with self.assertRaises(FakeUSBError):
            usb_link.open_device(dev)
        self.usb.util.dispose_resources.assert_called_once_with(dev)

    def test_open_device_without_the_endpoints(self):
        dev = MagicMock()
        cfg = MagicMock()
        cfg.bConfigurationValue = 1
        cfg.__getitem__.return_value = []
        dev.get_active_configuration.return_value = cfg
        with self.assertRaises(ValueError):
            usb_link.open_device(dev)


class DoctorUsbTest(UsbTestCase):
    def check(self, rule_exists):
        items = []
        with patch.object(Path, "exists", return_value=rule_exists):
            doctor.check_usb(items)
        return items[0]

    def test_without_pyusb(self):
        with patch.object(usb_link, "HAVE_PYUSB", False):
            item = self.check(False)
        self.assertEqual((item.state, item.needs, item.fix), ("info", ("pyusb",), ""))
        self.assertFalse(any("udev" in title for title, _ in doctor.plan([item], "debian")))

    def test_rule_missing(self):
        item = self.check(False)
        self.assertEqual((item.state, item.fix), ("info", doctor.USB_RULE))
        steps = dict(doctor.plan([item], "debian"))
        cmds = next(cmds for title, cmds in steps.items() if "udev" in title)
        self.assertTrue(cmds[0].startswith("printf") and "| sudo tee /etc/udev/rules.d/61-pspstream-usb.rules" in cmds[0])
        self.assertNotIn("Open the port in the firewall", steps)

    def test_ready(self):
        self.assertEqual(self.check(True).state, "ok")

    def test_rule_is_the_packaged_one(self):
        # o --setup grava a mesma regra que o .deb/.rpm instala, e nenhuma dá acesso a todos (MODE=0666)
        packaged = (ROOT / "packaging" / "files" / "61-pspstream-usb.rules").read_text()
        rule = next(line for line in packaged.splitlines() if line and not line.startswith("#"))
        self.assertIn(rule, doctor.USB_RULE)
        self.assertNotIn("MODE", rule)
        self.assertIn('TAG+="uaccess"', rule)
        self.assertIn("61-pspstream-usb.rules", (ROOT / "packaging" / "install-tree.sh").read_text())
        self.assertIn("61-pspstream-usb.rules", (ROOT / "packaging" / "pspstream.spec").read_text())


class AudioHeaderTest(unittest.TestCase):
    def test_pack_audio_carries_the_block_length(self):
        # pelo USB não há datagrama: o PSP sabe onde o pacote acaba por este campo
        block = b"helloaudio123456"
        pkt = protocol.pack_audio(seq=1, pos=0, rate=44100, channels=2, samples=32, block=block)
        *_, length = protocol.AUDIO_HDR_STRUCT.unpack_from(pkt)
        self.assertEqual(length, len(block))
        self.assertEqual(protocol.unpack_audio(pkt)[-1], block)


if __name__ == "__main__":
    unittest.main()
