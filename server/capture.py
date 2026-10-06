"""Montagem das partes do servidor: captura da tela, som e controles.

Usado na partida (pspstream.main) e pela interface web, que troca uma parte
com o servidor rodando (control.Controller). Nada aqui depende da sessão
com o PSP.
"""
import logging
import os
import sys
import time
from pathlib import Path

import distro
from i18n import tr

log = logging.getLogger("pspstream.capture")

WINDOWS = sys.platform == "win32"
# Fontes que o gst-launch captura num processo à parte (gst_pipe.py): no Windows, sempre.
PIPE_SOURCES = ("screen", "test", "gst")


def use_pipe() -> bool:
    """Captura pelo gst-launch (gst_pipe) em vez do PyGObject: o servidor de Windows. No Linux,
    PSPSTREAM_CAPTURE=pipe faz o mesmo (testes do caminho de Windows)."""
    return WINDOWS or os.environ.get("PSPSTREAM_CAPTURE") == "pipe"


def resolve_codec(args):
    """--codec auto vira h264p (com o openh264) ou jpeg. Devolve None, ou o
    motivo de não dar (H.264 sem openh264, ou tamanho diferente de 480x272)."""
    want = args.codec
    ok = False
    if want in ("auto", "h264", "h264p"):
        try:
            import h264
            h264.BACKEND = args.h264_encoder
            ok = h264.available()  # o openh264enc do GStreamer: o --codec h264 codifica dentro da captura
            if args.h264_encoder != "gstreamer":
                import openh264
                ok = ok or openh264.available()  # a libopenh264 direto basta (h264.H264Encoder e H264PEncoder)
        except (ImportError, ValueError):
            ok = False
    if want == "auto":
        args.codec = "h264p" if ok and tuple(args.size) == (480, 272) else "jpeg"
        if args.codec == "jpeg":
            log.info(tr("codec: JPEG (%s)"), tr("no openh264: {hint}").format(hint=distro.hint("openh264"))
                     if not ok else tr("--size other than 480x272"))
    if args.codec in ("h264", "h264p"):
        if not ok:
            return tr("--codec {codec} needs openh264: {hint}").format(codec=args.codec, hint=distro.hint("openh264"))
        if tuple(args.size) != (480, 272):
            return tr("--codec {codec} only works at 480x272 (the PSP decoder writes the whole screen)").format(
                codec=args.codec)
        if args.codec == "h264":
            log.info(tr("codec: H.264 (every frame IDR, the PSP's hardware decoder)"))
        else:
            log.info(tr("codec: H.264 with P frames (encoded when sending; EBOOT v0.9+, otherwise IDR only)"))
    return None


def open_injector(args):
    """Controles do PSP no PC: (injetor, descrição). RuntimeError se não der
    (sem /dev/uinput ou, no Windows, sem o ViGEmBus; perfil que não existe)."""
    from inject import Injector, load_profile
    try:
        profile = load_profile(args.keymap, args.profile)
    except SystemExit as exc:  # perfil que não existe
        raise RuntimeError(str(exc)) from None
    if args.source == "wolf":
        return open_wolf_injector(args, profile)
    if profile.get("type") == "gamepad":
        from gamepad import GamepadInjector
        out = None
        if WINDOWS and not args.input_dry_run:
            import win_gamepad
            out = win_gamepad.ViGEmPad()  # RuntimeError sem o driver ViGEmBus
        injector = GamepadInjector(profile, args.input_dry_run, args.input_timeout, out=out)
        kind = tr("virtual Xbox 360 controller")
    else:
        injector = Injector(profile, args.input_dry_run, args.mouse_speed, args.input_timeout)
        kind = tr("keyboard and mouse")
    log.info(tr("controls: profile '%s' (%s)%s"), args.profile, kind, " (dry-run)" if args.input_dry_run else "")
    return injector, kind


def open_wolf_injector(args, profile):
    """Com --source wolf, os controles vão para o jogo no Wolf como um controle de Xbox (pela API), não
    para o /dev/uinput desta máquina."""
    from inject import load_profile
    from wolf_input import WolfInjector
    name = args.profile
    if profile.get("type") != "gamepad":
        # o perfil padrão (game) é de teclado: com o Wolf, o padrão é o xbox, sem aviso
        from inject import canonical_profile
        level = logging.INFO if canonical_profile(name) == "game" else logging.WARNING
        log.log(level, tr("controls: profile '%s' is keyboard and mouse; through Wolf the controls go as an "
                          "Xbox controller: using the xbox profile (or --profile xbox-camera, xbox-shoulders)"), name)
        name = "xbox"
        try:
            profile = load_profile(args.keymap, name)
        except SystemExit as exc:
            raise RuntimeError(str(exc)) from None
    injector = WolfInjector(profile, args.input_dry_run, args.input_timeout)
    kind = tr("virtual Xbox controller in Wolf") + (tr(", with the {name} profile").format(name=name)
                                                     if name != args.profile else "")
    log.info(tr("controls: profile '%s' (%s; the PSP session joins the lobby)%s"), name, kind,
             " (dry-run)" if args.input_dry_run else "")
    return injector, kind


def build_source(args, portal=None):
    """portal: sessão do portal já aberta (refazer o pipeline sem novo diálogo)."""
    w, h = args.size
    if args.source == "static":
        from sources import StaticSource
        try:
            from gst_source import transcode_image
        except (ImportError, ValueError):
            import imaging
            if not imaging.available():
                # Sem GStreamer nem Pillow: envia o arquivo como está (precisa ser
                # JPEG 4:2:0 de até 480x272) e a qualidade não muda.
                return StaticSource(Path(args.image).read_bytes())
            transcode_image = imaging.transcode_image

        if args.codec == "h264p":
            from h264 import image_to_i420
            return StaticSource(image_to_i420(args.image, w, h, not args.stretch, args.scale),
                                quality=args.quality, raw_i420=True)
        if args.codec == "h264":
            from h264 import H264Encoder, image_to_i420
            raw = image_to_i420(args.image, w, h, not args.stretch, args.scale)
            enc = H264Encoder(w, h, args.quality)

            def reencode(q):
                enc.set_quality(q)
                return enc.encode(raw)
        else:
            def reencode(q):
                return transcode_image(args.image, w, h, q, not args.stretch, args.scale)

        return StaticSource(reencode(args.quality), reencode, args.quality)

    if args.source == "wolf":
        from wolf_api import WolfApi
        from wolf_source import WolfSource
        audio = (args.audio_rate, 1 if args.audio_mono else 2) if wolf_audio(args) else None
        return WolfSource(WolfApi(args.wolf_socket), args.wolf_target, args.wolf_video_convert, w, h, args.fps,
                          args.quality, args.scale, not args.stretch, args.codec, args.wolf_rtp_port,
                          args.wolf_audio_rtp_port, audio=audio, pin=args.wolf_pin)

    if use_pipe() and args.source in PIPE_SOURCES:
        import gst_pipe
        keep = not args.stretch
        if args.source == "screen":
            candidates = gst_pipe.screen_candidates(args.monitor, not args.no_cursor, args.fps, w, h, args.scale, keep)
        elif args.source == "test":
            candidates = gst_pipe.test_candidates(args.fps, w, h, args.scale, keep)
        else:
            if not args.gst_src:
                raise SystemExit(tr("--source gst needs --gst-src \"<GStreamer elements>\""))
            candidates = gst_pipe.custom_candidates(args.gst_src, w, h, args.scale, keep)
        return gst_pipe.PipeSource(candidates, w, h, args.fps, args.quality, args.codec, label=args.source)

    if args.source == "kms":
        from kms import KmsSource
        return KmsSource(w, h, args.fps, args.quality, args.scale, not args.stretch, args.codec,
                         args.kms_card, args.kms_monitor)

    from gst_source import SOURCES, GstSource
    keepalive, gpu_from = portal, None
    if args.source == "portal":
        if keepalive is None:
            keepalive = open_portal(args)
        if args.dmabuf:
            gpu_from = tuple(keepalive.size) if keepalive.size else (w, h)
            if not keepalive.size and not args.stretch:
                log.warning(tr("--dmabuf: the portal did not report the screen size; the image may come out stretched"))
        src = keepalive.gst_source(dmabuf=args.dmabuf)
    elif args.source == "gst":
        if not args.gst_src:
            raise SystemExit(tr("--source gst needs --gst-src \"<GStreamer elements>\""))
        src = args.gst_src
    else:
        src = SOURCES[args.source]
    return GstSource(src, w, h, args.fps, args.quality, args.scale, not args.stretch, keepalive, args.codec,
                     gpu_from)


DMABUF_FIRST_FRAME_S = 5


def wolf_audio(args) -> bool:
    """O som vem do Wolf: com --source wolf, o padrão (monitor) ou --audio-device wolf."""
    return args.source == "wolf" and not args.no_audio and args.audio_device in ("monitor", "wolf")


def open_audio(args, seq0: int = 0):
    """Abre a captura do som (args.audio_device, audio_rate, audio_mono).
    seq0: continua a numeração dos pacotes de uma captura anterior (o PSP
    trata um número menor como pacote atrasado). RuntimeError com o motivo
    se não der."""
    try:
        import audio
    except (ImportError, ValueError) as exc:
        raise RuntimeError(str(exc)) from None
    if args.audio_device == "wolf" and args.source != "wolf":
        raise RuntimeError(tr("--audio-device wolf only works with --source wolf"))
    if wolf_audio(args):
        return open_wolf_audio(args, seq0)
    if use_pipe():
        return open_pipe_audio(args, seq0)
    if not audio.available():
        raise RuntimeError(tr("GStreamer's pulsesrc and adpcmenc are missing ({hint})").format(hint=distro.hint("good", "bad")))
    try:
        capture = audio.AudioCapture(args.audio_device, args.audio_rate, 1 if args.audio_mono else 2)
        capture.seq = seq0
        capture.start()
    except Exception as exc:  # sem PipeWire/PulseAudio, fonte errada...
        raise RuntimeError(str(exc)) from None
    log.info(tr("audio: %s, %d Hz %s, IMA ADPCM in %.0f ms packets (~%.0f KB/s when the PSP asks)"),
             capture.device, capture.rate, tr("stereo") if capture.channels == 2 else "mono", capture.packet_ms,
             capture.kbps)
    return capture


def open_pipe_audio(args, seq0: int = 0):
    """O som pelo gst-launch (servidor de Windows: o WASAPI em loopback, o que sai nas caixas)."""
    import gst_pipe
    try:
        capture = gst_pipe.PipeAudioCapture(args.audio_device, args.audio_rate, 1 if args.audio_mono else 2)
        capture.seq = seq0
        capture.start()
    except (RuntimeError, ValueError) as exc:
        raise RuntimeError(str(exc)) from None
    device = tr("what plays on the speakers") if args.audio_device == "monitor" else args.audio_device
    log.info(tr("audio: %s, %d Hz %s, IMA ADPCM in %.0f ms packets (~%.0f KB/s when the PSP asks)"),
             device, capture.rate, tr("stereo") if capture.channels == 2 else "mono", capture.packet_ms, capture.kbps)
    return capture


def open_wolf_audio(args, seq0: int = 0):
    """O som do alvo no Wolf, pela sessão que a captura do Wolf (WolfSource) mantém."""
    import audio
    import wolf_source
    if not audio.available(pulse=False):
        raise RuntimeError(tr("GStreamer's adpcmenc is missing ({hint})").format(hint=distro.hint("bad")))
    source = wolf_source.current()
    if source is None:
        raise RuntimeError(tr("the Wolf capture is not running"))
    capture = wolf_source.WolfAudio(source, args.audio_rate, 1 if args.audio_mono else 2)
    capture.seq = seq0
    capture.start()
    log.info(tr("audio: from Wolf (the target's audio, through the PSPStream session), %d Hz %s, IMA ADPCM in "
                "%.0f ms packets (~%.0f KB/s when the PSP asks)"), capture.rate,
             tr("stereo") if capture.channels == 2 else "mono",
             capture.packet_ms, capture.kbps)
    return capture


def start_audio(args):
    """Captura do som, ou None (o vídeo continua sem som)."""
    try:
        return open_audio(args)
    except RuntimeError as exc:
        log.warning(tr("audio disabled: %s"), exc)
        return None


def open_portal(args):
    from portal import open_screencast
    return open_screencast(window=args.window, cursor=not args.no_cursor, remember=not args.forget)


def start_source(args, portal=None):
    """--dmabuf é experimental: se o pipeline não sobe ou não sai frame em
    alguns segundos (DMA-BUF ou OpenGL indisponível), volta para a captura
    pela memória comum na mesma sessão do portal (sem outro diálogo)."""
    if args.source == "portal" and portal is None:
        portal = open_portal(args)
    if not args.dmabuf:
        source = build_source(args, portal)
        source.start()
        return source
    source = None
    try:
        source = build_source(args, portal)
        source.start()
        deadline = time.monotonic() + DMABUF_FIRST_FRAME_S
        got = None
        while not got and not source.failed and time.monotonic() < deadline:
            got = source.wait_newer(0, 0.1)
        reason = source.failed or (None if got else tr("no frame in {seconds} s").format(seconds=DMABUF_FIRST_FRAME_S))
    except Exception as exc:  # pipeline que não monta (GLib.Error) ou não inicia
        reason = str(exc)
    if reason is None:
        log.info(tr("capture: DMA-BUF + scaling on the GPU (OpenGL), --dmabuf"))
        return source
    log.warning(tr("--dmabuf did not work (%s); falling back to capturing through regular memory"), reason)
    if source is not None:
        source.stop()
    args.dmabuf = False
    fallback = build_source(args, portal)
    fallback.start()
    return fallback
