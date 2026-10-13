import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import protocol
import usb_link
from transports import UsbTransport


class MockEndpointIn:
    def __init__(self, packets=None):
        self.packets = list(packets or [])
        self.bEndpointAddress = 0x81

    def read(self, size, timeout=500):
        if not self.packets:
            time.sleep(0.01)
            raise usb_link.usb.core.USBTimeoutError("Connection timed out")
        data = self.packets.pop(0)
        return data


class MockEndpointOut:
    def __init__(self):
        self.written = bytearray()
        self.bEndpointAddress = 0x02

    def write(self, data, timeout=3000):
        self.written.extend(data)
        return len(data)


class UsbTransportTest(unittest.TestCase):
    def setUp(self):
        self.dev = MagicMock()
        self.ep_in = MockEndpointIn()
        self.ep_out = MockEndpointOut()
        self.transport = UsbTransport(self.dev, self.ep_in, self.ep_out)

    def test_transport_metadata(self):
        self.assertEqual(self.transport.name, "usb")
        self.assertEqual(self.transport.addr, ("USB", 0))

    def test_send_frame(self):
        fake_jpeg = b"\xFF\xD8\xFF\xE0" + b"\x00" * 100 + b"\xFF\xD9"
        send_ms = 123456
        sent_len = self.transport.send_frame(1, fake_jpeg, send_ms)
        self.assertEqual(sent_len, len(fake_jpeg))
        self.assertEqual(self.transport.sent_chunks, 1)

        # Confere o cabeçalho no payload gravado
        magic, frame_no, size, ts = protocol.FRAME_HDR_STRUCT.unpack_from(self.ep_out.written, 0)
        self.assertEqual(magic, protocol.MAGIC_FRAME)
        self.assertEqual(frame_no, 1)
        self.assertEqual(size, len(fake_jpeg))
        self.assertEqual(ts, send_ms)
        self.assertEqual(bytes(self.ep_out.written[protocol.FRAME_HDR_STRUCT.size:]), fake_jpeg)

    def test_send_audio(self):
        audio_data = protocol.pack_audio(seq=1, pos=100, rate=44100, channels=2, samples=128, block=b"testblock")
        sent_len = self.transport.send_audio(audio_data)
        self.assertEqual(sent_len, len(audio_data))
        self.assertEqual(bytes(self.ep_out.written), audio_data)

    def test_send_pong(self):
        token = 0x12345678
        self.transport.send_pong(token)
        magic, echo = protocol.PONG_STRUCT.unpack(self.ep_out.written)
        self.assertEqual(magic, protocol.MAGIC_PONG)
        self.assertEqual(echo, token)

    def test_reader_receives_request(self):
        # Monta um request válido
        req = protocol.Request(buttons=0x0001, lx=120, ly=130, flags=protocol.REQ_FRAME)
        packed_req = req.pack()
        self.ep_in.packets.append(packed_req)

        session = MagicMock()
        session.alive = True
        received_requests = []
        session.on_request.side_effect = lambda r: received_requests.append(r)

        self.transport.start(session)
        # Espera o leitor processar
        t0 = time.monotonic()
        while not received_requests and time.monotonic() - t0 < 1.0:
            time.sleep(0.01)

        self.transport.close()
        self.assertEqual(len(received_requests), 1)
        self.assertEqual(received_requests[0].buttons, 0x0001)
        self.assertEqual(received_requests[0].lx, 120)
        self.assertEqual(received_requests[0].ly, 130)

    def test_reader_receives_pipelined_window_request(self):
        req = protocol.Request(flags=protocol.REQ_FRAME | protocol.REQ_NACK)
        packed_nack = protocol.pack_nack(frame_no=42, missing=())
        full_msg = req.pack() + packed_nack
        self.ep_in.packets.append(full_msg)

        session = MagicMock()
        session.alive = True
        received_requests = []
        session.on_request.side_effect = lambda r: received_requests.append(r)

        self.transport.start(session)
        t0 = time.monotonic()
        while not received_requests and time.monotonic() - t0 < 1.0:
            time.sleep(0.01)

        self.transport.close()
        self.assertEqual(len(received_requests), 1)
        self.assertEqual(received_requests[0].want_frame, 42)


    def test_reader_responds_to_ping(self):
        req = protocol.Request(flags=protocol.REQ_PING, echo_ts=0xCAFEBABE)
        self.ep_in.packets.append(req.pack())

        session = MagicMock()
        session.alive = True
        self.transport.start(session)

        t0 = time.monotonic()
        while len(self.ep_out.written) < protocol.PONG_STRUCT.size and time.monotonic() - t0 < 1.0:
            time.sleep(0.01)

        self.transport.close()
        magic, echo = protocol.PONG_STRUCT.unpack(self.ep_out.written[:protocol.PONG_STRUCT.size])
        self.assertEqual(magic, protocol.MAGIC_PONG)
        self.assertEqual(echo, 0xCAFEBABE)


class UsbLinkTest(unittest.TestCase):
    @patch("usb_link.usb.core.find")
    def test_find_psp_usb(self, mock_find):
        fake_dev = MagicMock()
        mock_find.return_value = fake_dev
        dev = usb_link.find_psp_usb()
        self.assertEqual(dev, fake_dev)
        mock_find.assert_called_once_with(idVendor=usb_link.PSP_VID, idProduct=usb_link.PSP_PID)

    @patch("usb_link.usb.util.claim_interface")
    @patch("usb_link.usb.core.find")
    def test_open_device(self, mock_find, mock_claim):
        fake_dev = MagicMock()
        mock_find.return_value = fake_dev
        fake_dev.is_kernel_driver_active.return_value = False

        ep_in = MagicMock()
        ep_in.bEndpointAddress = 0x81
        ep_out = MagicMock()
        ep_out.bEndpointAddress = 0x02

        fake_cfg = MagicMock()
        fake_cfg.__getitem__.return_value = [ep_in, ep_out]
        fake_dev.get_active_configuration.return_value = fake_cfg

        dev, in_found, out_found = usb_link.open_device(fake_dev)
        self.assertEqual(dev, fake_dev)
        self.assertEqual(in_found, ep_in)
        self.assertEqual(out_found, ep_out)


class AudioProtocolSelfDelimitTest(unittest.TestCase):
    def test_pack_audio_contains_block_length(self):
        block = b"helloaudio123456"
        pkt = protocol.pack_audio(seq=1, pos=0, rate=44100, channels=2, samples=32, block=block)
        magic, seq, pos, rate, ch, codec, samples, reserved = protocol.AUDIO_HDR_STRUCT.unpack_from(pkt)
        self.assertEqual(magic, protocol.MAGIC_AUDIO)
        self.assertEqual(reserved, len(block))


if __name__ == "__main__":
    unittest.main()
