"""openh264 chamado direto (ctypes), sem o GStreamer no caminho.

Pelo GStreamer (appsrc -> openh264enc -> appsink), cada frame passa por duas
filas e acorda duas threads, e o QP só muda refazendo o encoder (= IDR).
Aqui o frame é codificado na thread que chama (o GIL fica livre durante o
encode), e o QP muda no meio do stream sem IDR: com o controle de taxa
desligado (RC_OFF_MODE), o QP de cada frame é o iDLayerQp da camada, que
o SetOption(ENCODER_OPTION_SVC_ENCODE_PARAM_EXT) atualiza sem refazer nada
(WelsEncoderParamAdjust, ramo sem reset).

A API em C do openh264 é estável no começo das estruturas, mas a 2.6.0
acrescentou campos de PSNR no fim de SEncParamExt, SSourcePicture e
SLayerBSInfo; o último muda o tamanho de cada camada em SFrameBSInfo. O
layout é escolhido pela versão da biblioteca e conferido duas vezes: os
padrões de GetDefaultParams têm de bater (iMaxQp 51, iLtrMarkPeriod 30...)
e, a cada frame, a soma dos NALs tem de dar o iFrameSizeInBytes. Qualquer
diferença vira OpenH264Error, e h264.py volta para o GStreamer.

Os parâmetros imitam o openh264enc do GStreamer que foi medido no PSP
(mesmo SPS/PPS: nível pelo bitrate de 50 Mbps, ids constantes, 1 fatia,
CAVLC); um teste compara os dois.
"""
import ctypes
import logging
import os
from pathlib import Path
from ctypes import (CFUNCTYPE, POINTER, Structure, byref, c_bool, c_char_p, c_float, c_int, c_longlong, c_ubyte,
                    c_uint, c_ushort, c_void_p, cast, string_at)

log = logging.getLogger("pspstream.openh264")

LIB_NAMES = ("libopenh264.so.8", "libopenh264.so.7", "libopenh264.so.6", "libopenh264.so.5", "libopenh264.so")
# Sem o pacote da distribuição: a biblioteca do Cisco (github.com/cisco/openh264/releases) em
# ~/.local/lib ou em lib/ do projeto, ou o caminho em PSPSTREAM_OPENH264.
LIB_DIRS = (Path.home() / ".local" / "lib", Path(__file__).resolve().parent.parent / "lib")


def lib_candidates():
    env = os.environ.get("PSPSTREAM_OPENH264")
    if env:
        yield env
    yield from LIB_NAMES
    for d in LIB_DIRS:
        try:
            found = sorted(d.glob("libopenh264*.so*"), reverse=True)  # a versão maior primeiro
        except OSError:
            continue
        yield from (str(p) for p in found if not p.name.endswith((".bz2", ".sig")))

MAX_SPATIAL_LAYER_NUM = 4
MAX_SLICES_NUM_TMP = 35
MAX_LAYER_NUM_OF_FRAME = 128

CAMERA_VIDEO_REAL_TIME = 0
RC_OFF_MODE = -1
SM_SINGLE_SLICE = 0
MEDIUM_COMPLEXITY = 1
CONSTANT_ID = 0
VIDEO_FORMAT_I420 = 23
FRAME_TYPE_IDR, FRAME_TYPE_I, FRAME_TYPE_P, FRAME_TYPE_SKIP = 1, 2, 3, 4
ENCODER_OPTION_DATAFORMAT = 0
ENCODER_OPTION_SVC_ENCODE_PARAM_EXT = 3
BITRATE = 50_000_000  # como no openh264enc medido: o nível do SPS (4.1) sai do bitrate


class OpenH264Error(RuntimeError):
    pass


class OpenH264Version(Structure):
    _fields_ = [("uMajor", c_uint), ("uMinor", c_uint), ("uRevision", c_uint), ("uReserved", c_uint)]


class SSliceArgument(Structure):
    _fields_ = [("uiSliceMode", c_int), ("uiSliceNum", c_uint), ("uiSliceMbNum", c_uint * MAX_SLICES_NUM_TMP),
                ("uiSliceSizeConstraint", c_uint)]


class SSpatialLayerConfig(Structure):
    _fields_ = [("iVideoWidth", c_int), ("iVideoHeight", c_int), ("fFrameRate", c_float), ("iSpatialBitrate", c_int),
                ("iMaxSpatialBitrate", c_int), ("uiProfileIdc", c_int), ("uiLevelIdc", c_int), ("iDLayerQp", c_int),
                ("sSliceArgument", SSliceArgument),
                ("bVideoSignalTypePresent", c_bool), ("uiVideoFormat", c_ubyte), ("bFullRange", c_bool),
                ("bColorDescriptionPresent", c_bool), ("uiColorPrimaries", c_ubyte),
                ("uiTransferCharacteristics", c_ubyte), ("uiColorMatrix", c_ubyte),
                ("bAspectRatioPresent", c_bool), ("eAspectRatio", c_int),
                ("sAspectRatioExtWidth", c_ushort), ("sAspectRatioExtHeight", c_ushort)]


class SEncParamExt(Structure):
    _fields_ = [("iUsageType", c_int), ("iPicWidth", c_int), ("iPicHeight", c_int), ("iTargetBitrate", c_int),
                ("iRCMode", c_int), ("fMaxFrameRate", c_float),
                ("iTemporalLayerNum", c_int), ("iSpatialLayerNum", c_int),
                ("sSpatialLayers", SSpatialLayerConfig * MAX_SPATIAL_LAYER_NUM),
                ("iComplexityMode", c_int), ("uiIntraPeriod", c_uint), ("iNumRefFrame", c_int),
                ("eSpsPpsIdStrategy", c_int), ("bPrefixNalAddingCtrl", c_bool), ("bEnableSSEI", c_bool),
                ("bSimulcastAVC", c_bool), ("iPaddingFlag", c_int), ("iEntropyCodingModeFlag", c_int),
                ("bEnableFrameSkip", c_bool), ("iMaxBitrate", c_int), ("iMaxQp", c_int), ("iMinQp", c_int),
                ("uiMaxNalSize", c_uint), ("bEnableLongTermReference", c_bool), ("iLTRRefNum", c_int),
                ("iLtrMarkPeriod", c_uint), ("iMultipleThreadIdc", c_ushort), ("bUseLoadBalancing", c_bool),
                ("iLoopFilterDisableIdc", c_int), ("iLoopFilterAlphaC0Offset", c_int),
                ("iLoopFilterBetaOffset", c_int), ("bEnableDenoise", c_bool),
                ("bEnableBackgroundDetection", c_bool), ("bEnableAdaptiveQuant", c_bool),
                ("bEnableFrameCroppingFlag", c_bool), ("bEnableSceneChangeDetect", c_bool),
                ("bIsLosslessLink", c_bool), ("bFixRCOverShoot", c_bool), ("iIdrBitrateRatio", c_int),
                ("bPsnrY", c_bool), ("bPsnrU", c_bool), ("bPsnrV", c_bool),
                # Folga: GetDefaultParams/SetOption copiam sizeof(SEncParamExt) da
                # versão da biblioteca, que pode ter campos depois destes.
                ("_reserva", c_ubyte * 1024)]


class SSourcePicture(Structure):
    _fields_ = [("iColorFormat", c_int), ("iStride", c_int * 4), ("pData", c_void_p * 4), ("iPicWidth", c_int),
                ("iPicHeight", c_int), ("uiTimeStamp", c_longlong),
                ("bPsnrY", c_bool), ("bPsnrU", c_bool), ("bPsnrV", c_bool)]


_LAYER_HEAD = [("uiTemporalId", c_ubyte), ("uiSpatialId", c_ubyte), ("uiQualityId", c_ubyte), ("eFrameType", c_int),
               ("uiLayerType", c_ubyte), ("iSubSeqId", c_int), ("iNalCount", c_int),
               ("pNalLengthInByte", POINTER(c_int)), ("pBsBuf", POINTER(c_ubyte))]


class SLayerBSInfo(Structure):        # até a 2.5
    _fields_ = _LAYER_HEAD


class SLayerBSInfoPsnr(Structure):    # 2.6+: rPsnr no fim de cada camada
    _fields_ = _LAYER_HEAD + [("rPsnr", c_float * 3)]


def _frame_info(layer):
    class SFrameBSInfo(Structure):
        _fields_ = [("iLayerNum", c_int), ("sLayerInfo", layer * MAX_LAYER_NUM_OF_FRAME), ("eFrameType", c_int),
                    ("iFrameSizeInBytes", c_int), ("uiTimeStamp", c_longlong)]
    return SFrameBSInfo


class ISVCEncoderVtbl(Structure):
    _fields_ = [("Initialize", CFUNCTYPE(c_int, c_void_p, c_void_p)),
                ("InitializeExt", CFUNCTYPE(c_int, c_void_p, POINTER(SEncParamExt))),
                ("GetDefaultParams", CFUNCTYPE(c_int, c_void_p, POINTER(SEncParamExt))),
                ("Uninitialize", CFUNCTYPE(c_int, c_void_p)),
                ("EncodeFrame", CFUNCTYPE(c_int, c_void_p, POINTER(SSourcePicture), c_void_p)),
                ("EncodeParameterSets", CFUNCTYPE(c_int, c_void_p, c_void_p)),
                # C++: ForceIntraFrame(bool bIDR, int iLayerId = -1); o int a mais é inofensivo nas antigas
                ("ForceIntraFrame", CFUNCTYPE(c_int, c_void_p, c_bool, c_int)),
                ("SetOption", CFUNCTYPE(c_int, c_void_p, c_int, c_void_p)),
                ("GetOption", CFUNCTYPE(c_int, c_void_p, c_int, c_void_p))]


_lib = None
_version = None


def library():
    """(biblioteca, (major, minor, revision)); OpenH264Error se não houver."""
    global _lib, _version
    if _lib is None:
        errors = []
        for name in lib_candidates():
            try:
                lib = ctypes.CDLL(name)
                break
            except OSError as exc:
                errors.append(str(exc))
        else:
            raise OpenH264Error("libopenh264 não encontrada (" + "; ".join(errors[:2]) + ")")
        lib.WelsCreateSVCEncoder.argtypes = [POINTER(c_void_p)]
        lib.WelsCreateSVCEncoder.restype = c_int
        lib.WelsDestroySVCEncoder.argtypes = [c_void_p]
        lib.WelsDestroySVCEncoder.restype = None
        ver = OpenH264Version()
        if hasattr(lib, "WelsGetCodecVersionEx"):
            lib.WelsGetCodecVersionEx.argtypes = [POINTER(OpenH264Version)]
            lib.WelsGetCodecVersionEx(byref(ver))
        _lib, _version = lib, (ver.uMajor, ver.uMinor, ver.uRevision)
    return _lib, _version


def available() -> bool:
    try:
        library()
        return True
    except OpenH264Error:
        return False


class Encoder:
    """Um encoder openh264 com QP fixo e IDR sob pedido. encode(I420) -> Annex B."""
    live_qp = True  # set_qp() vale no próximo frame, sem IDR

    def __init__(self, width: int, height: int, qp: int, idr_every_frame: bool = False):
        lib, version = library()
        if version < (2, 0, 0):
            raise OpenH264Error(f"openh264 {version} antiga demais (precisa da 2.x)")
        self.version = version
        self.width, self.height = width, height
        self.idr_every_frame = idr_every_frame
        self._info_t = _frame_info(SLayerBSInfoPsnr if version >= (2, 6, 0) else SLayerBSInfo)
        self._info = self._info_t()
        self._lib = lib
        self._enc = c_void_p()
        if lib.WelsCreateSVCEncoder(byref(self._enc)) != 0 or not self._enc:
            raise OpenH264Error("WelsCreateSVCEncoder falhou")
        self._vt = cast(self._enc, POINTER(POINTER(ISVCEncoderVtbl))).contents.contents
        try:
            self._init(qp)
        except Exception:
            lib.WelsDestroySVCEncoder(self._enc)
            self._enc = None
            raise
        self._pic = SSourcePicture()
        self._pic.iColorFormat = VIDEO_FORMAT_I420
        self._pic.iPicWidth, self._pic.iPicHeight = width, height
        self._pic.iStride[0], self._pic.iStride[1], self._pic.iStride[2] = width, width // 2, width // 2
        self._n = 0
        self._checked = False

    def _init(self, qp: int) -> None:
        p = SEncParamExt()
        vt, enc = self._vt, self._enc
        if vt.GetDefaultParams(enc, byref(p)) != 0:
            raise OpenH264Error("GetDefaultParams falhou")
        # Os padrões da biblioteca (param_svc.h, FillDefault) conferem o layout:
        # campos antes, dentro e depois das camadas.
        layer1 = p.sSpatialLayers[1]
        expected = {"iSpatialLayerNum": (p.iSpatialLayerNum, 1), "iTemporalLayerNum": (p.iTemporalLayerNum, 1),
                    "uiSliceSizeConstraint": (layer1.sSliceArgument.uiSliceSizeConstraint, 1500),
                    "iMaxQp": (p.iMaxQp, 51), "iMinQp": (p.iMinQp, 0), "iNumRefFrame": (p.iNumRefFrame, -1),
                    "iLtrMarkPeriod": (p.iLtrMarkPeriod, 30), "iMultipleThreadIdc": (p.iMultipleThreadIdc, 1)}
        wrong = {k: v for k, v in expected.items() if v[0] != v[1]}
        if wrong:
            raise OpenH264Error(f"layout de SEncParamExt inesperado na openh264 {self.version}: {wrong}")
        p.iUsageType = CAMERA_VIDEO_REAL_TIME
        p.iPicWidth, p.iPicHeight = self.width, self.height
        p.iTargetBitrate = p.iMaxBitrate = BITRATE
        p.iRCMode = RC_OFF_MODE                 # QP = iDLayerQp, trocável sem IDR
        p.fMaxFrameRate = 60.0
        p.iComplexityMode = MEDIUM_COMPLEXITY
        p.uiIntraPeriod = 1 if self.idr_every_frame else 0  # 0 = só o primeiro; os outros IDR são pedidos
        p.eSpsPpsIdStrategy = CONSTANT_ID       # SPS/PPS sempre com id 0, como no stream medido
        p.bEnableFrameSkip = False
        p.bEnableAdaptiveQuant = False          # QP exato, como qp-min = qp-max no openh264enc
        p.iMultipleThreadIdc = 1
        layer = p.sSpatialLayers[0]
        layer.iVideoWidth, layer.iVideoHeight = self.width, self.height
        layer.fFrameRate = 60.0
        layer.iSpatialBitrate = layer.iMaxSpatialBitrate = BITRATE
        layer.iDLayerQp = qp
        layer.sSliceArgument.uiSliceMode = SM_SINGLE_SLICE
        layer.sSliceArgument.uiSliceNum = 1
        if vt.InitializeExt(enc, byref(p)) != 0:
            raise OpenH264Error("InitializeExt recusou os parâmetros")
        fmt = c_int(VIDEO_FORMAT_I420)
        vt.SetOption(enc, ENCODER_OPTION_DATAFORMAT, byref(fmt))
        self._params = p
        self.qp = qp

    def set_qp(self, qp: int) -> None:
        """QP novo a partir do próximo frame, sem IDR."""
        if qp == self.qp:
            return
        self._params.sSpatialLayers[0].iDLayerQp = qp
        if self._vt.SetOption(self._enc, ENCODER_OPTION_SVC_ENCODE_PARAM_EXT, byref(self._params)) != 0:
            raise OpenH264Error("SetOption(SVC_ENCODE_PARAM_EXT) recusou o QP novo")
        self.qp = qp

    def force_idr(self) -> None:
        self._vt.ForceIntraFrame(self._enc, True, -1)

    def encode(self, i420: bytes) -> bytes:
        size = self.width * self.height
        if len(i420) != size * 3 // 2:
            raise ValueError(f"I420 de {len(i420)} bytes, esperava {size * 3 // 2}")
        if self.idr_every_frame:
            self.force_idr()
        base = cast(c_char_p(i420), c_void_p).value  # sem cópia: o encoder só lê
        pic = self._pic
        pic.pData[0], pic.pData[1], pic.pData[2] = base, base + size, base + size + size // 4
        pic.uiTimeStamp = self._n * 1000 // 60
        self._n += 1
        info = self._info
        if self._vt.EncodeFrame(self._enc, byref(pic), byref(info)) != 0:
            raise OpenH264Error("EncodeFrame falhou")
        if info.eFrameType in (FRAME_TYPE_SKIP, 0):
            raise OpenH264Error(f"o encoder pulou o frame (tipo {info.eFrameType})")
        parts = []
        for i in range(info.iLayerNum):
            layer = info.sLayerInfo[i]
            n = sum(layer.pNalLengthInByte[k] for k in range(layer.iNalCount))
            parts.append(string_at(layer.pBsBuf, n))
        out = b"".join(parts)
        if len(out) != info.iFrameSizeInBytes or not out.startswith((b"\x00\x00\x00\x01", b"\x00\x00\x01")):
            # camadas no lugar errado: o layout de SLayerBSInfo não é o desta versão
            raise OpenH264Error(f"saída inconsistente na openh264 {self.version} "
                                f"({len(out)} bytes, a biblioteca diz {info.iFrameSizeInBytes})")
        return out

    def close(self) -> None:
        if self._enc:
            self._vt.Uninitialize(self._enc)
            self._lib.WelsDestroySVCEncoder(self._enc)
            self._enc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
