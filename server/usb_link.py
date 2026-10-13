"""PSPStream - Conexão e monitoramento USB para o PSP (054C:02D2).

Utiliza pyusb (libusb) para detectar o PSP conectado na porta USB,
reivindicar a interface Bulk (EP 0x81 IN para controles/pedidos e EP 0x02 OUT para frames/áudio)
e instanciar o UsbTransport.
"""
import logging
import threading
import time
from typing import Optional, Tuple

import protocol
from i18n import tr

log = logging.getLogger("pspstream.usb")

PSP_VID = 0x054C  # Sony Corp.
PSP_PID = 0x02D2  # PSP Stream bulk driver
EP_IN_ADDR = 0x81   # Bulk IN (PSP -> PC, 52-byte requests)
EP_OUT_ADDR = 0x02  # Bulk OUT (PC -> PSP, frames and audio)

try:
    import usb.core
    import usb.util
    HAVE_PYUSB = True
except ImportError:
    usb = None
    HAVE_PYUSB = False


def is_available() -> bool:
    """Verifica se o suporte a USB está disponível no sistema (pyusb instalado)."""
    return HAVE_PYUSB


def find_psp_usb():
    """Localiza o dispositivo PSP Stream conectado via USB. Retorna o dispositivo pyusb ou None."""
    if not HAVE_PYUSB:
        return None
    try:
        return usb.core.find(idVendor=PSP_VID, idProduct=PSP_PID)
    except usb.core.USBError as exc:
        if exc.errno == 13:
            log.warning(tr("PSP USB device found, but permission denied. "
                           "Install packaging/files/61-pspstream-usb.rules into /etc/udev/rules.d/"))
        else:
            log.debug("usb.core.find error: %s", exc)
        return None
    except Exception as exc:
        log.debug("Unexpected error searching USB: %s", exc)
        return None


def open_device(dev=None):
    """Configura o dispositivo USB, reinvindica a interface 0 e obtém endpoints IN e OUT.

    Retorna (dev, ep_in, ep_out) ou levanta exceção.
    """
    if not HAVE_PYUSB:
        raise RuntimeError(tr("pyusb is not installed. Run: pip install pyusb"))

    if dev is None:
        dev = find_psp_usb()
    if dev is None:
        return None

    try:
        # Se algum driver de kernel estiver associado à interface, desconecta
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except (NotImplementedError, usb.core.USBError):
            pass

        # Configura o dispositivo se ainda não estiver ativo na config 1
        try:
            cfg = dev.get_active_configuration()
            if cfg.bConfigurationValue != 1:
                dev.set_configuration(1)
        except Exception:
            try:
                dev.set_configuration(1)
            except Exception:
                pass

        usb.util.claim_interface(dev, 0)

        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        ep_in = None
        ep_out = None
        for ep in intf:
            if ep.bEndpointAddress == EP_IN_ADDR:
                ep_in = ep
            elif ep.bEndpointAddress == EP_OUT_ADDR:
                ep_out = ep

        if ep_in is None or ep_out is None:
            usb.util.dispose_resources(dev)
            raise ValueError(tr("PSP USB endpoints not found (expected 0x81 and 0x02)"))

        return dev, ep_in, ep_out

    except Exception:
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        raise


class UsbMonitor:
    """Thread em segundo plano que monitora a conexão do PSP via USB e dispara a sessão."""

    def __init__(self, server, poll_interval: float = 1.0):
        self.server = server
        self.poll_interval = poll_interval
        self.running = True
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if not HAVE_PYUSB:
            log.info(tr("USB streaming disabled (pyusb not installed). Run: pip install pyusb"))
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name="usb-monitor", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False

    def _run(self) -> None:
        from transports import UsbTransport

        log.info(tr("USB monitor active: plug your PSP with USB cable (transport=usb)"))
        while self.running and getattr(self.server, "running", True):
            try:
                cur = self.server.session()
                # Se já há uma sessão USB ativa e viva, não precisa abrir de novo
                if cur is None or not (isinstance(cur.transport, UsbTransport) and cur.alive):
                    dev = find_psp_usb()
                    if dev is not None:
                        try:
                            opened = open_device(dev)
                            if opened:
                                dev_obj, ep_in, ep_out = opened
                                transport = UsbTransport(dev_obj, ep_in, ep_out)
                                log.info(tr("PSP connected via USB (054C:02D2)! Starting USB session..."))
                                self.server.replace(transport)
                        except usb.core.USBError as e:
                            if e.errno == 13:
                                log.warning(tr("PSP USB permission denied. Check udev rules."))
                            else:
                                log.debug("USB device open failed: %s", e)
                        except Exception as e:
                            log.debug("USB device open exception: %s", e)
            except Exception as e:
                log.debug("USB monitor loop error: %s", e)

            time.sleep(self.poll_interval)
