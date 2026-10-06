"""Checagens do lado do PC que pesam muito na latência do PSP."""
import logging
import os
import re
import socket
import subprocess
from i18n import tr

log = logging.getLogger("pspstream.netcheck")


def local_ip() -> str:
    """IP da interface usada para sair para a rede (nenhum pacote é enviado)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def _iface_for(ip: str):
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr"], capture_output=True, text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].split("/")[0] == ip:
            return parts[1]
    return None


def link_freq(iw_link_output: str):
    """Frequência (MHz) da saída de `iw dev <iface> link`, ou None."""
    m = re.search(r"freq:\s*([\d.]+)", iw_link_output)
    return float(m.group(1)) if m else None


def channel_24(freq: float):
    if freq == 2484:
        return 14
    return round((freq - 2407) / 5) if 2412 <= freq <= 2472 else None


def band_advice(freq, iface: str):
    """(nível, mensagem) sobre a banda do PC. O PSP só fala 802.11b (2,4 GHz)."""
    if freq is None:
        return (logging.WARNING, tr("the PC is on Wi-Fi ({iface}): if you can, use a cable, or the router's 5 GHz "
                                    "network. On the same channel, every packet crosses the air twice and the PSP's "
                                    "bandwidth drops").format(iface=iface))
    if freq >= 4900:
        return (logging.INFO, tr("PC on {ghz:.1f} GHz Wi-Fi ({iface}): good, it does not compete for the PSP's "
                                 "2.4 GHz channel").format(ghz=freq / 1000, iface=iface))
    ch = channel_24(freq)
    return (logging.WARNING,
            tr("the PC is on 2.4 GHz Wi-Fi ({iface}, channel {channel}), the same as the PSP: every packet "
               "crosses the same channel twice and the PSP's bandwidth is halved. Use a cable, or connect the PC "
               "to the router's 5 GHz network (the PSP stays on 2.4). With one network name for both bands: "
               "nmcli connection modify <network> 802-11-wireless.band a").format(iface=iface, channel=ch or "?"))


def check_pc_wifi(ip: str) -> None:
    """Avisa se o PC fala com o PSP pelo Wi-Fi, e se o power save do Wi-Fi está ligado.

    PC no mesmo Wi-Fi: cada pacote atravessa o ar duas vezes (PC -> roteador ->
    PSP), dividindo a banda. Power save ligado (padrão do NetworkManager em
    muitos notebooks): o roteador segura os pacotes até a placa acordar, e cada
    pedido do PSP chega ao servidor com dezenas de ms de atraso.
    """
    iface = _iface_for(ip)
    if not iface or not os.path.exists(f"/sys/class/net/{iface}/wireless"):
        return
    state = None
    try:
        out = subprocess.run(["iw", "dev", iface, "get", "power_save"], capture_output=True, text=True,
                             timeout=2).stdout
        if "on" in out.lower():
            state = True
        elif "off" in out.lower():
            state = False
    except (OSError, subprocess.SubprocessError):
        pass
    freq = None
    try:
        freq = link_freq(subprocess.run(["iw", "dev", iface, "link"], capture_output=True, text=True,
                                        timeout=2).stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    level, msg = band_advice(freq, iface)
    log.log(level, "%s", msg)
    if state is True:
        log.warning(tr("the PC's Wi-Fi power save is ON: the router holds the PSP's requests until the card "
                       "wakes up. Turn it off: sudo iw dev %s set power_save off (until reboot) or "
                       "nmcli connection modify <network> 802-11-wireless.powersave 2 (permanent)"), iface)
    elif state is None:
        log.info(tr("check the PC's Wi-Fi power save: iw dev %s get power_save"), iface)
