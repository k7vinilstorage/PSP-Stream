"""Checagens do lado do PC que pesam muito na latência do PSP."""
import logging
import os
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
    log.warning("o PC está no Wi-Fi (%s): se der, use cabo. No mesmo Wi-Fi, cada pacote cruza o ar duas "
                "vezes e a banda do PSP cai", iface)
    if state is True:
        log.warning("power save do Wi-Fi do PC LIGADO: o roteador segura os pedidos do PSP até a placa "
                    "acordar. Desligue: sudo iw dev %s set power_save off (até reiniciar) ou "
                    "nmcli connection modify <rede> 802-11-wireless.powersave 2 (permanente)", iface)
    elif state is None:
        log.info("confira o power save do Wi-Fi do PC: iw dev %s get power_save", iface)
