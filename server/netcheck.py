"""Checagens do lado do PC que pesam muito na latência do PSP."""
import logging
import os
import re
import subprocess

log = logging.getLogger("pspstream.netcheck")


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
        return (logging.WARNING, f"o PC está no Wi-Fi ({iface}): se der, use cabo, ou a rede de 5 GHz do "
                                 "roteador. No mesmo canal, cada pacote cruza o ar duas vezes e a banda do PSP cai")
    if freq >= 4900:
        return (logging.INFO, f"PC no Wi-Fi de {freq / 1000:.1f} GHz ({iface}): bom, ele não disputa o canal "
                              "de 2,4 GHz do PSP")
    ch = channel_24(freq)
    return (logging.WARNING,
            f"o PC está no Wi-Fi de 2,4 GHz ({iface}, canal {ch if ch else '?'}), o mesmo do PSP: cada pacote "
            "cruza o mesmo canal duas vezes e a banda do PSP cai pela metade. Use cabo, ou conecte o PC na "
            "rede de 5 GHz do roteador (o PSP continua no 2,4). Com um nome de rede só para as duas bandas: "
            "nmcli connection modify <rede> 802-11-wireless.band a")


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
        log.warning("power save do Wi-Fi do PC LIGADO: o roteador segura os pedidos do PSP até a placa "
                    "acordar. Desligue: sudo iw dev %s set power_save off (até reiniciar) ou "
                    "nmcli connection modify <rede> 802-11-wireless.powersave 2 (permanente)", iface)
    elif state is None:
        log.info("confira o power save do Wi-Fi do PC: iw dev %s get power_save", iface)
