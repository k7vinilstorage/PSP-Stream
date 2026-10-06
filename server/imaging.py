"""Imagens sem o GStreamer em processo (servidor de Windows): JPEG a partir do
I420 da captura e imagem fixa -> I420 ou JPEG, pelo Pillow.

Faixas de cor: o I420 que sai do videoconvert (e o que o openh264 espera, sem
o VUI de faixa cheia) é BT.601 de faixa limitada, Y em 16-235 e cor em
16-240. O JPEG (JFIF) é de faixa cheia, 0-255. As duas conversões abaixo
fazem a ponte; sem elas, a imagem sai lavada (JPEG) ou com pretos e brancos
estourados (H.264).
"""
from i18n import tr

try:
    from PIL import Image
except ImportError:  # sem o Pillow: só H.264 com a captura (o I420 vai direto para o encoder)
    Image = None

# A mesma ideia dos filtros do videoscale (--scale), no que o Pillow tem.
RESAMPLE = {"nearest-neighbour": "NEAREST", "bilinear": "BILINEAR", "bilinear2": "BOX", "lanczos": "LANCZOS",
            "mitchell": "BICUBIC", "catrom": "BICUBIC"}

_TO_FULL_Y = [max(0, min(255, round((v - 16) * 255 / 219))) for v in range(256)]
_TO_FULL_C = [max(0, min(255, round((v - 128) * 255 / 224 + 128))) for v in range(256)]
_TO_LIMITED_Y = [16 + round(v * 219 / 255) for v in range(256)]
_TO_LIMITED_C = [128 + round((v - 128) * 224 / 255) for v in range(256)]


def available() -> bool:
    return Image is not None


def _need():
    if Image is None:
        raise RuntimeError(tr("Pillow is missing (pip install pillow)"))


def _resample(scale: str):
    return getattr(Image, RESAMPLE.get(scale, "BILINEAR"))


def i420_to_jpeg(i420: bytes, width: int, height: int, quality: int) -> bytes:
    """I420 (faixa limitada) -> JPEG baseline 4:2:0, como o do jpegenc (o que o sceJpeg do PSP decodifica)."""
    _need()
    import io
    size = width * height
    cw, ch = width // 2, height // 2
    y = Image.frombuffer("L", (width, height), i420[:size], "raw", "L", 0, 1).point(_TO_FULL_Y)
    u = Image.frombuffer("L", (cw, ch), i420[size:size + cw * ch], "raw", "L", 0, 1)
    v = Image.frombuffer("L", (cw, ch), i420[size + cw * ch:size + 2 * cw * ch], "raw", "L", 0, 1)
    u = u.point(_TO_FULL_C).resize((width, height), Image.NEAREST)
    v = v.point(_TO_FULL_C).resize((width, height), Image.NEAREST)
    out = io.BytesIO()
    Image.merge("YCbCr", (y, u, v)).save(out, "JPEG", quality=max(1, min(100, int(quality))), subsampling=2,
                                         optimize=False, progressive=False)
    return out.getvalue()


def fit(img, width: int, height: int, keep_aspect: bool = True, scale: str = "bilinear2"):
    """A imagem em width x height: com bordas pretas (keep_aspect) ou esticada."""
    img = img.convert("RGB")
    if not keep_aspect:
        return img.resize((width, height), _resample(scale))
    f = min(width / img.width, height / img.height)
    w, h = max(2, round(img.width * f / 2) * 2), max(2, round(img.height * f / 2) * 2)
    canvas = Image.new("RGB", (width, height))
    canvas.paste(img.resize((w, h), _resample(scale)), ((width - w) // 2, (height - h) // 2))
    return canvas


def rgb_to_i420(img) -> bytes:
    """RGB (Pillow) -> I420 de faixa limitada; a cor é a média de cada 2x2."""
    ycc = img.convert("YCbCr")
    y, u, v = ycc.split()
    half = (img.width // 2, img.height // 2)
    u = u.resize(half, Image.BOX).point(_TO_LIMITED_C)
    v = v.resize(half, Image.BOX).point(_TO_LIMITED_C)
    return y.point(_TO_LIMITED_Y).tobytes() + u.tobytes() + v.tobytes()


def image_to_i420(path: str, width: int, height: int, keep_aspect: bool = True, scale: str = "bilinear2") -> bytes:
    _need()
    with Image.open(path) as img:
        return rgb_to_i420(fit(img, width, height, keep_aspect, scale))


def transcode_image(path: str, width: int, height: int, quality: int, keep_aspect: bool = True,
                    scale: str = "bilinear2") -> bytes:
    """Qualquer imagem -> JPEG 4:2:0 de width x height (o modo static sem o GStreamer em processo)."""
    return i420_to_jpeg(image_to_i420(path, width, height, keep_aspect, scale), width, height, quality)
