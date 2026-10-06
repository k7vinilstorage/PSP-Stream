"""Leitura mínima do cabeçalho JPEG (dimensões, amostragem, progressivo).

O decoder de hardware do PSP (sceJpeg) só aceita JPEG baseline 4:2:0, e o
cliente só exibe até 480x272. Isto serve para validar a imagem antes de enviar.
"""
from dataclasses import dataclass
from i18n import tr


@dataclass
class JpegInfo:
    width: int
    height: int
    progressive: bool
    sampling: tuple  # ((h, v), ...) por componente

    @property
    def is_420(self) -> bool:
        return (len(self.sampling) == 3 and self.sampling[0] == (2, 2)
                and self.sampling[1] == (1, 1) and self.sampling[2] == (1, 1))

    def problems(self, max_w: int = 480, max_h: int = 272) -> list[str]:
        found = []
        if self.width > max_w or self.height > max_h:
            found.append(tr("{size} is larger than {max}").format(size=f"{self.width}x{self.height}", max=f"{max_w}x{max_h}"))
        if self.progressive:
            found.append(tr("progressive JPEG (must be baseline)"))
        if not self.is_420:
            found.append(tr("{sampling} sampling (sceJpeg needs 4:2:0)").format(sampling=self.sampling))
        return found


def jpeg_info(data: bytes) -> JpegInfo:
    if data[:2] != b"\xff\xd8":
        raise ValueError(tr("not a JPEG (no SOI marker)"))
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            raise ValueError(tr("invalid marker at byte {offset}").format(offset=i))
        marker = data[i + 1]
        if marker == 0xFF:  # bytes de preenchimento
            i += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # marcadores sem tamanho
            i += 2
            continue
        seglen = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2):
            p = i + 4
            height = int.from_bytes(data[p + 1:p + 3], "big")
            width = int.from_bytes(data[p + 3:p + 5], "big")
            ncomp = data[p + 5]
            sampling = tuple(
                (data[p + 7 + 3 * k] >> 4, data[p + 7 + 3 * k] & 0x0F) for k in range(ncomp)
            )
            return JpegInfo(width, height, marker == 0xC2, sampling)
        if marker == 0xDA:  # início dos dados sem ter achado SOF
            break
        i += 2 + seglen
    raise ValueError(tr("SOF header not found"))
