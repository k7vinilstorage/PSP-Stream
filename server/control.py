"""Aplica as configurações com o servidor rodando (interface web, web.py).

Cada mudança vale no momento que o esquema (settings.py) diz: na hora, ou
refazendo a captura, o som ou os controles. A captura nova sobe antes de a
velha parar e entra na sessão com o PSP conectado (Session.set_source): a
numeração dos frames continua e o PSP não precisa reconectar. Se a nova não
sobe, a velha continua e a interface mostra o motivo.
"""
import copy
import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

import capture
import protocol
import settings
from settings import BY_KEY, SETTINGS
from transports import set_dscp
from i18n import N_, set_language, tr

log = logging.getLogger("pspstream.control")

FIRST_FRAME_S = 3.0  # captura nova sem frame nem erro depois disso: aceita (a tela pode estar parada)
ORDER = ("capture", "live", "audio", "input", "next", "restart")
ADAPTIVE_KEYS = ("adaptive", "target_fps", "q_min", "q_max")


def keymap_profiles(path) -> dict:
    """Perfis do keymap.json: nome -> 'gamepad' ou 'keyboard'."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    return {name: "gamepad" if p.get("type") == "gamepad" else "keyboard"
            for name, p in data.items() if not name.startswith("_") and isinstance(p, dict)}


def audio_sources() -> list:
    """Fontes de som do PipeWire/PulseAudio (pactl), além de monitor e test."""
    names = ["monitor", "test"]
    if shutil.which("pactl") and not settings.WINDOWS:
        try:
            out = subprocess.run(["pactl", "list", "short", "sources"], capture_output=True, text=True,
                                 timeout=3).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) > 1 and settings.AUDIO_DEVICE_RE.match(parts[1]):
                names.append(parts[1])
    return names


def wolf_lobbies(socket_path) -> list:
    """Nomes dos lobbies abertos no Wolf, para sugerir o alvo (vazio se o Wolf não responde)."""
    from wolf_api import WolfApi, WolfApiError
    try:
        lobbies = WolfApi(socket_path, timeout=1.0).lobbies()
    except WolfApiError:
        return []
    return [str(lb.get("name") or lb.get("id")) for lb in lobbies]


class Controller:
    def __init__(self, args, store, server, udp_sock, explicit=(), version="", ip_fn=None, input_note="",
                 audio_note=""):
        self.args = args
        self.store = store
        self.server = server
        self.udp = udp_sock
        self.explicit = set(explicit)  # atributos do argparse dados na linha de comando
        self.version = version
        self.ip_fn = ip_fn or (lambda: "")
        self.web_url = ""
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.input_note = input_note  # tipo dos controles, ou por que estão desligados
        self.audio_note = audio_note  # por que o som está desligado
        self.pending = {}             # chave -> valor que só vale ao reiniciar o servidor

    # ---- leitura ----

    def values(self) -> dict:
        values = {s.key: settings.arg_value(s, self.args) for s in SETTINGS}
        values.update(self.pending)
        return values

    def choices(self, setting):
        if setting.key == "source":
            choices = tuple(c for c in setting.choices if c != "wolf" or self._wolf_available())
            return choices + (("gst",) if getattr(self.args, "gst_src", None) else ())
        if setting.key == "wolf_video_convert":  # elementos dados na linha de comando: o valor atual vale
            current = getattr(self.args, "wolf_video_convert", "auto")
            return setting.choices + (() if current in setting.choices else (current,))
        if setting.key == "profile":
            return tuple(keymap_profiles(self.args.keymap))
        return setting.choices

    def _wolf_available(self) -> bool:
        """A opção "wolf" só aparece se o socket da API existe (ou se já é a fonte)."""
        if self.args.source == "wolf":
            return True
        path = getattr(self.args, "wolf_socket", "")
        return bool(path) and Path(path).exists()

    def config(self) -> dict:
        """Esquema + valores, para montar a página."""
        items = []
        for s in SETTINGS:
            item = {"key": s.key, "kind": s.kind, "group": tr(s.group), "label": tr(s.label),
                    "help": tr(s.help) if s.help else "", "apply": s.apply,
                    "apply_text": tr(settings.APPLY_TEXT[s.apply]), "flag": s.flag,
                    "cli": settings.cli_dest(s) in self.explicit}
            if s.kind == "choice":
                item["choices"] = list(self.choices(s))
            if s.key == "audio_device":
                item["suggestions"] = audio_sources() + (["wolf"] if self._wolf_available() else [])
            if s.key == "wolf_target" and self._wolf_available():
                item["suggestions"] = wolf_lobbies(self.args.wolf_socket)
            if s.min is not None:
                item["min"] = s.min
            if s.max is not None:
                item["max"] = s.max
            if s.show_if:
                item["show_if"] = {"key": s.show_if[0], "values": list(s.show_if[1])}
            items.append(item)
        return {"version": self.version, "settings": items, "values": self.values(),
                "pending": dict(self.pending), "profiles": keymap_profiles(self.args.keymap),
                "config_path": str(self.store.path) if self.store else ""}

    def status(self) -> dict:
        args, server = self.args, self.server
        src = server.source
        st = {
            "version": self.version,
            "uptime_s": round(time.monotonic() - self.started),
            "ip": self.ip_fn(),
            "port": args.port,
            "capture": {"source": args.source, "codec": args.codec, "fps_limit": args.fps,
                        "quality": src.quality, "failed": getattr(src, "failed", None)},
            "audio": None,
            "wolf": src.status() if hasattr(src, "status") else None,
            "input": {"on": server.injector is not None, "profile": args.profile, "note": tr(self.input_note)},
            "psp": None,
        }
        cap = server.audio
        if cap is not None:
            st["audio"] = {"on": True, "device": cap.device, "rate": cap.rate, "channels": cap.channels,
                           "kbps": round(cap.kbps, 1), "failed": cap.failed}
        else:
            st["audio"] = {"on": False, "note": tr(self.audio_note)}
        sess = server.session()
        if sess is not None:
            info = {"transport": sess.transport.name, "addr": "%s:%d" % tuple(sess.transport.addr[:2]),
                    "since_s": round(time.monotonic() - sess.started), "streaming": sess.hello_seen,
                    "frames": sess.stats.total_frames, "mb": round(sess.stats.total_bytes / 1e6, 1),
                    "audio_on": sess.audio_on, "wifi": None, "summary": None}
            if sess.wifi:
                info["wifi"] = {"signal": sess.wifi[0], "power_save": bool(sess.wifi[1] & protocol.WIFI_POWER_SAVE)}
            summary = sess.stats.last_summary
            if summary:
                keys = ("fps", "source_fps", "kb_per_frame", "wifi_kbps", "latency_ms", "latency_p95_ms",
                        "transfer_ms", "decode_ms", "ping_ms", "hitches", "hitch_max_ms", "quality", "lost",
                        "resent_pct", "audio_kbps")
                info["summary"] = {k: (round(summary[k], 1) if isinstance(summary[k], float) else summary[k])
                                   for k in keys if k in summary}
            st["psp"] = info
        return st

    # ---- mudanças ----

    def apply(self, changes: dict) -> dict:
        """Valida tudo antes; depois aplica por grupo. Grava no arquivo o que
        foi aplicado (ou fica para reiniciar)."""
        if not isinstance(changes, dict):
            return {"ok": False, "errors": {"": tr("expected an object")}}
        with self.lock:
            current = self.values()
            errors, parsed = {}, {}
            for key, raw in changes.items():
                setting = BY_KEY.get(key)
                if setting is None:
                    errors[key] = tr("unknown setting")
                    continue
                try:
                    value = settings.coerce(setting, raw, self.choices(setting) if setting.kind == "choice" else None)
                except ValueError as exc:
                    errors[key] = str(exc)
                    continue
                if value != current[key]:
                    parsed[key] = value
            errors.update({k: v for k, v in settings.check_together({**current, **parsed}).items()
                           if k in parsed or k in changes})
            if errors:
                return {"ok": False, "errors": errors, "values": current, "pending": dict(self.pending)}
            groups = {}
            for key, value in parsed.items():
                groups.setdefault(BY_KEY[key].apply, {})[key] = value
            if "capture" in groups and "fps" in groups.get("live", {}):  # a captura nova já nasce com ele
                groups["capture"]["fps"] = groups["live"].pop("fps")
            applied, failed, later = [], {}, []
            for apply in ORDER:
                part = groups.get(apply)
                if not part:
                    continue
                try:
                    if apply == "capture":
                        self._restart_capture(part)
                    elif apply == "live":
                        self._apply_live(part)
                    elif apply == "audio":
                        self._restart_audio(part)
                    elif apply == "input":
                        self._restart_input(part)
                    elif apply == "next":
                        self._commit(self.args, part)
                    else:
                        self.pending.update(part)
                except RuntimeError as exc:
                    msg = str(exc)
                    log.warning(tr("web interface: %s"), msg)
                    failed.update({k: msg for k in part})
                    continue
                if apply in ("next", "restart"):
                    later.extend({"key": k, "when": tr(settings.APPLY_TEXT[apply])} for k in part)
                else:
                    applied.extend(part)
                log.info(tr("web interface: %s"), ", ".join(f"{k} = {v}" for k, v in part.items()))
            saved = {k: v for k, v in parsed.items() if k not in failed}
            save_error = None
            if saved and self.store is not None:
                self.store.update(saved)
                try:
                    self.store.save()
                except OSError as exc:
                    save_error = tr("could not save {path}: {error}").format(path=self.store.path, error=exc)
                    log.warning("%s", save_error)
            return {"ok": not failed and not save_error, "applied": applied, "later": later, "errors": failed,
                    "save_error": save_error, "values": self.values(), "pending": dict(self.pending)}

    @staticmethod
    def _commit(args, part: dict) -> None:
        for key, value in part.items():
            settings.set_arg(BY_KEY[key], args, value)

    def _candidate(self, part: dict):
        new = copy.copy(self.args)
        self._commit(new, part)
        return new

    def _restart_capture(self, part: dict) -> None:
        old_args, new = self.args, self._candidate(part)
        old_source = old_args.source
        if "codec" in part:
            new.codec = new.codec_choice
            err = capture.resolve_codec(new)
            if err:
                raise RuntimeError(err)
        if new.source != "portal":
            new.dmabuf = False
        old = self.server.source
        same_portal = (old_args.source == "portal" and new.source == "portal" and new.window == old_args.window
                       and new.no_cursor == old_args.no_cursor)
        portal = old.keepalive if same_portal else None
        try:
            source = capture.start_source(new, portal)
        except SystemExit as exc:  # build_source: --source gst sem --gst-src
            raise RuntimeError(str(exc)) from None
        except Exception as exc:  # portal recusado, GStreamer, KMS sem o auxiliar...
            raise RuntimeError(tr("could not start the capture: {error}").format(error=exc)) from None
        deadline = time.monotonic() + FIRST_FRAME_S
        while not getattr(source, "failed", None) and source.latest()[1] is None and time.monotonic() < deadline:
            time.sleep(0.05)
        failed = getattr(source, "failed", None)
        if failed:
            source.stop()
            if new.source == "portal" and portal is None and getattr(source, "keepalive", None) is not None:
                source.keepalive.close()
            raise RuntimeError(tr("the new capture failed: {error}").format(error=failed))
        self.server.set_source(source)
        vars(self.args).update(vars(new))
        old.stop()
        if old_args.source == "portal" and not same_portal and getattr(old, "keepalive", None) is not None:
            old.keepalive.close()  # sessão do portal que ninguém mais usa
        log.info(tr("capture: %s, %s, up to %d fps"), new.source, new.codec, new.fps)
        if "wolf" in (old_source, self.args.source):
            self._follow_source_audio()
            self._follow_source_input()

    def _follow_source_input(self) -> None:
        """Com o Wolf, os controles vão pela API; sem ele, pelo /dev/uinput: trocam junto com a captura."""
        old = self.server.injector
        if old is None and self.args.no_input:
            return
        injector, kind = None, N_("off")
        if not self.args.no_input:
            try:
                injector, kind = capture.open_injector(self.args)
            except RuntimeError as exc:
                kind = tr("disabled: {error}").format(error=exc)
                log.warning(tr("controls disabled: %s"), exc)
        self.server.set_injector(injector)
        if old is not None:
            old.release_all()
            old.close()
        self.input_note = kind

    def _follow_source_audio(self) -> None:
        """O som do Wolf vem da sessão da captura do Wolf: troca junto com ela."""
        if self.args.no_audio:
            return
        old = self.server.audio
        try:
            cap = capture.open_audio(self.args, seq0=old.seq if old is not None else 0)
        except RuntimeError as exc:
            cap = None
            self.audio_note = tr("the capture did not open: {error}").format(error=exc)
            log.warning(tr("audio disabled: %s"), exc)
        self.server.set_audio(cap)
        if old is not None:
            old.stop()

    def _apply_live(self, part: dict) -> None:
        self._commit(self.args, part)
        if "language" in part:
            set_language(part["language"])
        src = self.server.source
        if "fps" in part and hasattr(src, "set_fps"):
            src.set_fps(part["fps"])
        if "dscp" in part and self.udp is not None:
            set_dscp(self.udp, part["dscp"])
        if "quality" in part or ("adaptive" in part and not part["adaptive"]):
            src.set_quality(self.args.quality)
        sess = self.server.session()
        if sess is not None and any(k in part for k in ADAPTIVE_KEYS):
            stats = sess.stats
            if not self.args.adaptive or self.args.bench or src.quality is None:
                stats.adaptive = None
            elif stats.adaptive is None:
                from adaptive import AdaptiveQuality
                stats.adaptive = AdaptiveQuality(src, self.args.target_fps, self.args.q_min, self.args.q_max)
            else:
                stats.adaptive.budget_ms = 1000.0 / self.args.target_fps
                stats.adaptive.q_min, stats.adaptive.q_max = self.args.q_min, self.args.q_max

    def _restart_audio(self, part: dict) -> None:
        new = self._candidate(part)
        old = self.server.audio
        cap = None
        if not new.no_audio:
            cap = capture.open_audio(new, seq0=old.seq if old is not None else 0)  # RuntimeError
        self.server.set_audio(cap)
        if old is not None:
            old.stop()
        self._commit(self.args, part)
        self.audio_note = "" if cap is not None else N_("turned off in the web interface")
        if cap is None:
            log.info(tr("audio off"))

    def _restart_input(self, part: dict) -> None:
        new = self._candidate(part)
        old = self.server.injector
        injector, kind = None, N_("off")
        if not new.no_input:
            injector, kind = capture.open_injector(new)  # RuntimeError
        self.server.set_injector(injector)
        if old is not None:
            old.release_all()
            old.close()
        self._commit(self.args, part)
        self.input_note = kind
        if injector is None:
            log.info(tr("controls off"))
