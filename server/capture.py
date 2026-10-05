"""Montagem das partes do servidor: captura da tela, som e controles.

Usado na partida (pspstream.main) e pela interface web, que troca uma parte
com o servidor rodando (control.Controller). Nada aqui depende da sessão
com o PSP.
"""
import logging
import time
from pathlib import Path

import distro

log = logging.getLogger("pspstream.capture")


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
            if want != "h264" and args.h264_encoder != "gstreamer":
                import openh264
                ok = ok or openh264.available()  # frames P: a libopenh264 direto basta
        except (ImportError, ValueError):
            ok = False
    if want == "auto":
        args.codec = "h264p" if ok and tuple(args.size) == (480, 272) else "jpeg"
        if args.codec == "jpeg":
            log.info("codec: JPEG (%s)", f"sem o openh264: {distro.hint('openh264')}"
                     if not ok else "--size diferente de 480x272")
    if args.codec in ("h264", "h264p"):
        if not ok:
            return f"--codec {args.codec} precisa do openh264: {distro.hint('openh264')}"
        if tuple(args.size) != (480, 272):
            return f"--codec {args.codec} só funciona em 480x272 (o decoder do PSP escreve a tela inteira)"
        if args.codec == "h264":
            log.info("codec: H.264 (todo frame IDR, decoder de hardware do PSP)")
        else:
            log.info("codec: H.264 com frames P (codificado na hora de enviar; EBOOT v0.9+, senão só IDR)")
    return None


def open_injector(args):
    """Controles do PSP no PC: (injetor, descrição). RuntimeError se não der
    (sem /dev/uinput, perfil que não existe)."""
    from inject import Injector, load_profile
    try:
        profile = load_profile(args.keymap, args.profile)
    except SystemExit as exc:  # perfil que não existe
        raise RuntimeError(str(exc)) from None
    if profile.get("type") == "gamepad":
        from gamepad import GamepadInjector
        injector = GamepadInjector(profile, args.input_dry_run, args.input_timeout)
        kind = "controle de Xbox 360 virtual"
    else:
        injector = Injector(profile, args.input_dry_run, args.mouse_speed, args.input_timeout)
        kind = "teclado e mouse"
    log.info("controles: perfil '%s' (%s)%s", args.profile, kind, " (dry-run)" if args.input_dry_run else "")
    return injector, kind


def build_source(args, portal=None):
    """portal: sessão do portal já aberta (refazer o pipeline sem novo diálogo)."""
    w, h = args.size
    if args.source == "static":
        from sources import StaticSource
        try:
            from gst_source import transcode_image
        except (ImportError, ValueError):
            # Sem GStreamer: envia o arquivo como está (precisa ser JPEG 4:2:0
            # de até 480x272) e a qualidade não muda.
            return StaticSource(Path(args.image).read_bytes())

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
                          args.wolf_audio_rtp_port, audio=audio)

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
                log.warning("--dmabuf: o portal não disse o tamanho da tela; a imagem pode sair esticada")
        src = keepalive.gst_source(dmabuf=args.dmabuf)
    elif args.source == "gst":
        if not args.gst_src:
            raise SystemExit("--source gst precisa de --gst-src \"<elementos GStreamer>\"")
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
        raise RuntimeError("--audio-device wolf só vale com --source wolf")
    if wolf_audio(args):
        return open_wolf_audio(args, seq0)
    if not audio.available():
        raise RuntimeError(f"faltam o pulsesrc e o adpcmenc do GStreamer ({distro.hint('good', 'bad')})")
    try:
        capture = audio.AudioCapture(args.audio_device, args.audio_rate, 1 if args.audio_mono else 2)
        capture.seq = seq0
        capture.start()
    except Exception as exc:  # sem PipeWire/PulseAudio, fonte errada...
        raise RuntimeError(str(exc)) from None
    log.info("som: %s, %d Hz %s, IMA ADPCM em pacotes de %.0f ms (~%.0f KB/s quando o PSP pede)",
             capture.device, capture.rate, "estéreo" if capture.channels == 2 else "mono", capture.packet_ms,
             capture.kbps)
    return capture


def open_wolf_audio(args, seq0: int = 0):
    """O som do alvo no Wolf, pela sessão que a captura do Wolf (WolfSource) mantém."""
    import audio
    import wolf_source
    if not audio.available(pulse=False):
        raise RuntimeError(f"falta o adpcmenc do GStreamer ({distro.hint('bad')})")
    source = wolf_source.current()
    if source is None:
        raise RuntimeError("a captura do Wolf não está rodando")
    capture = wolf_source.WolfAudio(source, args.audio_rate, 1 if args.audio_mono else 2)
    capture.seq = seq0
    capture.start()
    log.info("som: do Wolf (o som do alvo, pela sessão do PSPStream), %d Hz %s, IMA ADPCM em pacotes de %.0f ms "
             "(~%.0f KB/s quando o PSP pede)", capture.rate, "estéreo" if capture.channels == 2 else "mono",
             capture.packet_ms, capture.kbps)
    return capture


def start_audio(args):
    """Captura do som, ou None (o vídeo continua sem som)."""
    try:
        return open_audio(args)
    except RuntimeError as exc:
        log.warning("som desativado: %s", exc)
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
        reason = source.failed or (None if got else f"nenhum frame em {DMABUF_FIRST_FRAME_S} s")
    except Exception as exc:  # pipeline que não monta (GLib.Error) ou não inicia
        reason = str(exc)
    if reason is None:
        log.info("captura: DMA-BUF + redução na GPU (OpenGL), --dmabuf")
        return source
    log.warning("--dmabuf não funcionou (%s); voltando para a captura pela memória comum", reason)
    if source is not None:
        source.stop()
    args.dmabuf = False
    fallback = build_source(args, portal)
    fallback.start()
    return fallback
