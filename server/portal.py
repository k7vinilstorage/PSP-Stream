"""Captura de tela no Wayland via xdg-desktop-portal (ScreenCast) + PipeWire.

No Wayland um programa não pode ler a tela por conta própria: ele pede ao
portal, o compositor (GNOME/KDE) mostra um diálogo para escolher monitor ou
janela, e devolve um stream PipeWire que o GStreamer lê com `pipewiresrc`.

Com `persist_mode=2` o portal devolve um "restore token". Ele fica guardado
em ~/.config/pspstream/portal_token, e nas próximas vezes o diálogo não
aparece (se o compositor suportar).
"""
import logging
import os
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402
from i18n import tr  # noqa: E402

log = logging.getLogger("pspstream.portal")

BUS_NAME = "org.freedesktop.portal.Desktop"
OBJ_PATH = "/org/freedesktop/portal/desktop"
IFACE = "org.freedesktop.portal.ScreenCast"

SOURCE_MONITOR = 1
SOURCE_WINDOW = 2
CURSOR_HIDDEN = 1
CURSOR_EMBEDDED = 2


def _token_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "pspstream" / "portal_token"


class PortalError(RuntimeError):
    pass


class ScreenCastSession:
    """Mantenha o objeto vivo enquanto o stream for usado: fechar a conexão
    D-Bus encerra a sessão de captura."""

    def __init__(self, bus, session_handle, node_id, fd, size):
        self.bus = bus
        self.session_handle = session_handle
        self.node_id = node_id
        self.fd = fd
        self.size = size

    def gst_source(self, dmabuf: bool = False) -> str:
        """dmabuf=True: a tela fica na memória da GPU (o pipeline reduz no OpenGL).
        Cada pipeline recebe uma cópia do fd, para dar para refazer o pipeline
        (ex.: --dmabuf que não funcionou) sem abrir outra sessão do portal."""
        fd = os.dup(self.fd)
        from gst_source import has_property
        copy = "" if dmabuf or not has_property("pipewiresrc", "always-copy") else " always-copy=true"
        return f"pipewiresrc fd={fd} path={self.node_id} do-timestamp=true{copy}"

    def close(self):
        try:
            self.bus.call_sync(BUS_NAME, self.session_handle, "org.freedesktop.portal.Session",
                               "Close", None, None, Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error:
            pass


class _Portal:
    def __init__(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            raise PortalError(tr("no D-Bus session bus: {error}").format(error=exc.message)) from exc
        self.sender = self.bus.get_unique_name()[1:].replace(".", "_")
        self.counter = 0

    def prop(self, name):
        try:
            res = self.bus.call_sync(BUS_NAME, OBJ_PATH, "org.freedesktop.DBus.Properties", "Get",
                                     GLib.Variant("(ss)", (IFACE, name)), GLib.VariantType("(v)"),
                                     Gio.DBusCallFlags.NONE, -1, None)
        except GLib.Error as exc:
            raise PortalError(tr("ScreenCast portal unavailable: {error}").format(error=exc.message)) from exc
        return res.unpack()[0]

    def request(self, method, make_args, timeout_s=120):
        """Chama um método do portal e espera o sinal Response do Request."""
        self.counter += 1
        token = f"pspstream{os.getpid()}_{self.counter}"
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        loop = GLib.MainLoop()
        result = {}

        def on_response(_conn, _sender, _path, _iface, _signal, params):
            result["code"], result["results"] = params.unpack()
            loop.quit()

        # Assina antes de chamar, para não perder uma resposta rápida.
        sub = self.bus.signal_subscribe(BUS_NAME, "org.freedesktop.portal.Request", "Response",
                                        path, None, Gio.DBusSignalFlags.NONE, on_response)
        try:
            self.bus.call_sync(BUS_NAME, OBJ_PATH, IFACE, method, make_args(token), None,
                               Gio.DBusCallFlags.NONE, -1, None)
            GLib.timeout_add_seconds(timeout_s, loop.quit)
            loop.run()
        except GLib.Error as exc:
            raise PortalError(tr("{method} failed: {error}").format(method=method, error=exc.message)) from exc
        finally:
            self.bus.signal_unsubscribe(sub)
        if "code" not in result:
            raise PortalError(tr("{method}: no answer in {seconds} s").format(method=method, seconds=timeout_s))
        if result["code"] == 1:
            raise PortalError(tr("capture cancelled in the dialog"))
        if result["code"] != 0:
            raise PortalError(tr("{method} refused by the portal (code {code})").format(method=method, code=result["code"]))
        return result["results"]


def open_screencast(window: bool = False, cursor: bool = True, remember: bool = True) -> ScreenCastSession:
    portal = _Portal()
    version = portal.prop("version")
    cursor_modes = portal.prop("AvailableCursorModes")
    log.info("portal ScreenCast v%d", version)

    session = portal.request("CreateSession", lambda tok: GLib.Variant("(a{sv})", ({
        "handle_token": GLib.Variant("s", tok),
        "session_handle_token": GLib.Variant("s", f"pspstream{os.getpid()}"),
    },)))["session_handle"]

    opts = {
        "types": GLib.Variant("u", SOURCE_WINDOW if window else SOURCE_MONITOR),
        "multiple": GLib.Variant("b", False),
    }
    want_cursor = CURSOR_EMBEDDED if cursor else CURSOR_HIDDEN
    if cursor_modes & want_cursor:
        opts["cursor_mode"] = GLib.Variant("u", want_cursor)
    token_file = _token_file()
    if remember and version >= 4:
        opts["persist_mode"] = GLib.Variant("u", 2)  # lembrar até revogar
        if token_file.exists():
            opts["restore_token"] = GLib.Variant("s", token_file.read_text().strip())

    def select_args(tok):
        return GLib.Variant("(oa{sv})", (session, dict(opts, handle_token=GLib.Variant("s", tok))))

    portal.request("SelectSources", select_args)
    log.info(tr("choose the monitor/window in the system dialog (if it shows up)..."))
    started = portal.request("Start", lambda tok: GLib.Variant("(osa{sv})", (
        session, "", {"handle_token": GLib.Variant("s", tok)})))

    streams = started.get("streams") or []
    if not streams:
        raise PortalError(tr("the portal returned no stream"))
    node_id, props = streams[0]
    size = props.get("size")
    if remember and started.get("restore_token"):
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(started["restore_token"])

    res, fds = portal.bus.call_with_unix_fd_list_sync(
        BUS_NAME, OBJ_PATH, IFACE, "OpenPipeWireRemote",
        GLib.Variant("(oa{sv})", (session, {})), GLib.VariantType("(h)"),
        Gio.DBusCallFlags.NONE, -1, None, None)
    fd = fds.get(res.unpack()[0])
    log.info(tr("PipeWire: node %d, %s"), node_id, f"{size[0]}x{size[1]}" if size else tr("size ?"))
    return ScreenCastSession(portal.bus, session, node_id, fd, size)
