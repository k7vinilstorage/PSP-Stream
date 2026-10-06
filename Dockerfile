# PSPStream num container, ao lado do Wolf (Games on Whales). Como usar:
# docs/WOLF.md e os arquivos em docker/. A imagem pronta é publicada pelo CI
# em ghcr.io/k7vinilstorage/pspstream (nightly = a main; latest e X.Y = as
# versões). Para compilar:
#
#   docker build -t pspstream .
#   docker build -t pspstream https://github.com/k7vinilstorage/PSP-Stream.git#main
#
# Só o servidor: com --source wolf, a imagem, o som e os controles vêm pela
# API do Wolf. Sem PipeWire, portal, evdev nem /dev/uinput.
FROM ubuntu:24.04

LABEL org.opencontainers.image.title="PSPStream" \
      org.opencontainers.image.description="A tela, o som e os controles de um lobby do Wolf (Games on Whales) no PSP" \
      org.opencontainers.image.source="https://github.com/k7vinilstorage/PSP-Stream" \
      org.opencontainers.image.licenses="MIT"

# GStreamer: base (tcpserversrc, videoscale, audioresample), good (jpegenc),
# bad (gdpdepay, adpcmenc). libopenh264-7: H.264 com frames P (o servidor
# carrega a biblioteca direto). Vem do universe do Ubuntu, que já está ativo
# na imagem oficial; é o binário do openh264 compilado pelo Ubuntu.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-gi gir1.2-gstreamer-1.0 gir1.2-gst-plugins-base-1.0 \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
        libopenh264-7 \
    && rm -rf /var/lib/apt/lists/*

COPY LICENSE /opt/pspstream/
COPY assets /opt/pspstream/assets
COPY server /opt/pspstream/server

# Sistema de arquivos só leitura (read_only no compose): o registro dos
# plugins do GStreamer fica pronto na imagem, o bytecode do Python também, e
# as configurações da interface web vão para /config (um volume).
ENV GST_REGISTRY=/opt/pspstream/gst-registry.bin \
    GST_REGISTRY_UPDATE=no \
    HOME=/config \
    XDG_CONFIG_HOME=/config \
    XDG_CACHE_HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN python3 -m compileall -q /opt/pspstream/server \
    && python3 -c 'import gi; gi.require_version("Gst", "1.0"); from gi.repository import Gst; Gst.init(None)' \
    && chmod 644 "$GST_REGISTRY" \
    && useradd --system --uid 10001 --home-dir /config --shell /usr/sbin/nologin pspstream \
    && install -d -m 0777 /config

# Usuário comum. O socket da API do Wolf é do root (o Wolf não muda a
# permissão dele, e o próprio Wolf UI roda como root): os arquivos em docker/
# usam o uid 0 sem nenhuma capability; a outra saída (liberar o socket para o
# uid 10001) está em docs/WOLF.md.
USER pspstream
WORKDIR /config
EXPOSE 5123/tcp 5123/udp
ENTRYPOINT ["python3", "/opt/pspstream/server/pspstream.py"]
CMD ["--source", "wolf"]
