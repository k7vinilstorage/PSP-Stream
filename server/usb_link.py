"""PSPStream - o PSP pelo cabo USB (054C:02D2, o driver psp/usbdrv do EBOOT com transport=usb).

Usa o pyusb (libusb) para achar o PSP, abrir a interface bulk (EP 0x81 IN: pedidos e controles do
PSP; EP 0x02 OUT: frames e som) e criar o UsbTransport. Só no Linux; sem a regra do udev
(packaging/files/61-pspstream-usb.rules), só o root abre o aparelho.

Como no UDP, só um HELLO (ou um pedido sem nenhuma sessão ativa) abre a sessão: o driver do PSP
continua ativo depois de trocar para o Wi-Fi, e abrir a sessão só por o PSP estar no cabo derrubaria
a sessão de Wi-Fi a cada volta do monitor.
"""
import errno
import logging
import threading
import time
from typing import Optional

import protocol
from i18n import tr

log = logging.getLogger("pspstream.usb")

PSP_VID = 0x054C  # Sony Corp.
PSP_PID = 0x02D2  # o driver do PSPStream (psp/usbdrv)
EP_IN_ADDR = 0x81   # Bulk IN (PSP -> PC, pedidos de 52 bytes)
EP_OUT_ADDR = 0x02  # Bulk OUT (PC -> PSP, frames e som)

try:
    import usb.core
    import usb.util
    HAVE_PYUSB = True
except ImportError:
    usb = None
    HAVE_PYUSB = False


def is_available() -> bool:
    """O pyusb está instalado?"""
    return HAVE_PYUSB


def find_psp_usb():
    """O PSP com o driver do PSPStream no cabo (aparelho do pyusb), ou None. Só enumera, sem abrir:
    a falta de permissão aparece no open_device."""
    if not HAVE_PYUSB:
        return None
    try:
        return usb.core.find(idVendor=PSP_VID, idProduct=PSP_PID)
    except Exception as exc:  # sem backend do libusb, por exemplo
        log.debug("usb.core.find: %s", exc)
        return None


def dispose(dev) -> None:
    """Solta a interface e fecha o aparelho (sem erro se o PSP já saiu do cabo)."""
    try:
        usb.util.dispose_resources(dev)
    except Exception:
        pass


def open_device(dev=None):
    """Configura o aparelho, pega a interface 0 e acha os endpoints: (dev, ep_in, ep_out), None se não há
    PSP, ou exceção (usb.core.USBError com errno EACCES sem a regra do udev)."""
    if not HAVE_PYUSB:
        raise RuntimeError(tr("pyusb is not installed"))
    if dev is None:
        dev = find_psp_usb()
    if dev is None:
        return None
    try:
        try:  # um driver do kernel na interface (não deve haver: classe 0xFF)
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass
        try:
            cfg = dev.get_active_configuration()
        except usb.core.USBError as exc:
            if exc.errno == errno.EACCES:
                raise
            cfg = None
        if cfg is None or cfg.bConfigurationValue != 1:
            dev.set_configuration(1)
        usb.util.claim_interface(dev, 0)
        intf = dev.get_active_configuration()[(0, 0)]
        ep_in = next((ep for ep in intf if ep.bEndpointAddress == EP_IN_ADDR), None)
        ep_out = next((ep for ep in intf if ep.bEndpointAddress == EP_OUT_ADDR), None)
        if ep_in is None or ep_out is None:
            raise ValueError(tr("PSP USB endpoints not found (expected 0x81 and 0x02)"))
        return dev, ep_in, ep_out
    except Exception:
        dispose(dev)
        raise


class UsbMonitor:
    """Thread que espera o PSP no cabo e abre a sessão USB quando ele começa o stream (HELLO)."""

    def __init__(self, server, poll_interval: float = 1.0):
        self.server = server
        self.poll_interval = poll_interval
        self.running = True
        self.thread: Optional[threading.Thread] = None
        self.link = None      # (dev, ep_in, ep_out, buf): o PSP aberto, esperando o HELLO
        self.denied = False   # o aviso de permissão já saiu (até o PSP sair do cabo)

    def start(self) -> None:
        if not HAVE_PYUSB:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name="usb-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def _run(self) -> None:
        log.info(tr("USB: waiting for the PSP on the cable (transport=usb on the PSP)"))
        while self.running and getattr(self.server, "running", True):
            try:
                if not self.step():
                    time.sleep(self.poll_interval)
            except Exception as exc:
                log.debug("USB: %s", exc)
                self._drop()
                time.sleep(self.poll_interval)
        self._drop()

    def step(self) -> bool:
        """Uma volta: True se leu do PSP (volta já), False para esperar poll_interval."""
        from transports import UsbTransport, parse_usb_requests, usb_read
        cur = self.server.session()
        if cur is not None and isinstance(cur.transport, UsbTransport):
            return False  # a sessão USB cuida do aparelho
        if self.link is None and not self._open():
            return False
        dev, ep_in, ep_out, buf = self.link
        data = usb_read(ep_in)
        if not data:
            return True  # a leitura já esperou 0,5 s
        buf.extend(data)
        try:
            reqs = parse_usb_requests(buf)
        except ValueError:  # meio de um pedido de um stream anterior: recomeça do próximo
            buf.clear()
            return True
        for i, req in enumerate(reqs):
            if req.flags & protocol.REQ_BYE:
                continue
            cur = self.server.session()
            # Só um HELLO (ou um PSP sem sessão nenhuma ativa) abre sessão: pedidos atrasados de um
            # stream USB que já acabou não derrubam a sessão de Wi-Fi.
            if req.flags & protocol.REQ_HELLO or cur is None:
                self.link = None
                self.server.replace(UsbTransport(dev, ep_in, ep_out, pending=reqs[i:], buf=buf))
                return True
        return True

    def _open(self) -> bool:
        dev = find_psp_usb()
        if dev is None:
            self.denied = False
            return False
        try:
            opened = open_device(dev)
        except usb.core.USBError as exc:
            if exc.errno == errno.EACCES:
                if not self.denied:
                    self.denied = True
                    log.warning(tr("USB: no permission to open the PSP (054C:02D2). Install the udev rule: "
                                   "pspstream --setup (or see pspstream --check)"))
            else:
                log.debug("USB: open: %s", exc)
            return False
        if opened is None:
            return False
        self.denied = False
        self.link = (*opened, bytearray())
        return True

    def _drop(self) -> None:
        link, self.link = self.link, None
        if link is not None:
            dispose(link[0])
