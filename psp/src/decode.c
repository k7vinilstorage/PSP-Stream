/*
 * Dois decoders:
 *  - sw: libjpeg-turbo (TurboJPEG v3) no CPU, direto no framebuffer.
 *  - hw: sceJpeg (módulo avcodec do firmware), só JPEG baseline 4:2:0.
 *
 * sceJpegCreateMJpeg(512, 272): no PPSSPP (que segue testes feitos no
 * hardware) a largura passada aqui é o stride da saída, então dá para
 * decodificar direto no framebuffer de 512 pixels. Se o PSP real recusar
 * escrever na VRAM, decodificamos num buffer em RAM e copiamos.
 * A VALIDAR NO HARDWARE: stride, escrita na VRAM e tempo de decode.
 *
 * H.264 (servidor com --codec h264): todo frame é IDR. Vai para o decoder de
 * hardware pelo caminho do PMP Mod (sceMpegBasePESpacketCopy + sceMpegAvcDecode)
 * e sai com sceMpegAvcDecodeStop: sem o Stop o decoder segura o frame. Medido
 * no PSP-3000 (psp/probe): ~3,7 ms direto na VRAM, atraso 0. O sceJpeg e o
 * H.264 usam o mesmo avcodec do firmware; um é desligado quando o outro entra.
 */
#include "decode.h"
#include "display.h"

#include <malloc.h>
#include <pspjpeg.h>
#include <pspkernel.h>
#include <pspmpeg.h>
#include <psputility.h>
#include <psputility_avmodules.h>
#include <psputility_modules.h>
#include <stdio.h>
#include <string.h>
#include <turbojpeg.h>

#define ERR_ALREADY_LOADED ((int)0x80020139)

static tjhandle tj;
static int kind = DEC_SW;
static int hw_ready;
static int hw_direct = 1;  /* 1 = sceJpeg escreve direto na VRAM */
static uint32_t *hw_bounce; /* RAM (64 bytes alinhados) quando hw_direct == 0 */
static char last_error[96];
static int last_h264; /* o último frame era H.264 (2 = com frames P) */

static int hw_init(void)
{
    if (hw_ready)
        return 0;
    int r = sceUtilityLoadAvModule(PSP_AV_MODULE_AVCODEC);
    if (r < 0 && r != ERR_ALREADY_LOADED) {
        snprintf(last_error, sizeof(last_error), "avcodec: 0x%08X", r);
        return r;
    }
    if ((r = sceJpegInitMJpeg()) < 0) {
        snprintf(last_error, sizeof(last_error), "sceJpegInitMJpeg: 0x%08X", r);
        return r;
    }
    if ((r = sceJpegCreateMJpeg(FB_STRIDE, SCR_H)) < 0) {
        snprintf(last_error, sizeof(last_error), "sceJpegCreateMJpeg: 0x%08X", r);
        sceJpegFinishMJpeg();
        return r;
    }
    hw_ready = 1;
    return 0;
}

static int hw_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h)
{
    /* O sceJpeg lê/escreve a memória sem passar pelo cache do CPU: grava o
     * que o recv() deixou no cache e descarta linhas velhas do destino. */
    sceKernelDcacheWritebackInvalidateAll();

    int r = -1;
    if (hw_direct) {
        r = sceJpegDecodeMJpeg((u8 *)jpeg, size, (u8 *)dst, 0);
        if (r < 0 && r != (int)SCE_JPEG_ERROR_UNSUPPORT_SAMPLING) {
            /* talvez a VRAM não seja aceita: tenta via RAM daqui em diante */
            hw_direct = 0;
            printf("sceJpeg na VRAM falhou (0x%08X), usando buffer em RAM\n", r);
        }
    }
    if (!hw_direct) {
        if (!hw_bounce && !(hw_bounce = memalign(64, FB_BYTES))) {
            snprintf(last_error, sizeof(last_error), "sem memoria para o buffer do sceJpeg");
            return -1;
        }
        r = sceJpegDecodeMJpeg((u8 *)jpeg, size, (u8 *)hw_bounce, 0);
    }
    if (r < 0) {
        snprintf(last_error, sizeof(last_error), "sceJpegDecodeMJpeg: 0x%08X%s", r,
                 r == (int)SCE_JPEG_ERROR_UNSUPPORT_SAMPLING ? " (precisa 4:2:0)" : "");
        return -1;
    }
    int jw = (r >> 16) & 0xFFFF, jh = r & 0xFFFF;
    if (jw > SCR_W || jh > SCR_H) {
        snprintf(last_error, sizeof(last_error), "tamanho %dx%d invalido", jw, jh);
        return -1;
    }
    if (!hw_direct) {
        sceKernelDcacheInvalidateRange(hw_bounce, FB_STRIDE * jh * 4);
        for (int y = 0; y < jh; y++)
            memcpy(dst + y * FB_STRIDE, hw_bounce + y * FB_STRIDE, jw * 4);
        sceKernelDcacheWritebackAll();
    }
    *w = jw;
    *h = jh;
    return 0;
}

static void hw_term(void)
{
    if (hw_ready) {
        sceJpegDeleteMJpeg();
        sceJpegFinishMJpeg();
        sceUtilityUnloadAvModule(PSP_AV_MODULE_AVCODEC);
        hw_ready = 0;
    }
}

/* ---------------- H.264 ---------------- */

/* sceMpegbase. O SceMpegLLI do pspmpegbase.h é alinhado a 64 bytes; o PMP usa
 * entradas de 16 bytes seguidas. */
typedef struct {
    void *src;
    void *dst;
    void *next;
    int size;
} lli_t;
int sceMpegBasePESpacketCopy(lli_t *lli);

#define DMA_BLOCK 4095     /* bytes por entrada da lista de DMA */
#define ME_AVC_BUF 0x4a000 /* destino na memória do Media Engine (o mesmo do PMP) */
#define AVC_MAX_AU (256 * 1024)

static struct {
    SceMpeg mpeg;
    /* o pspsdk declara 44 bytes; os firmwares novos escrevem 48 */
    union {
        SceMpegRingbuffer rb;
        uint8_t rb_room[128];
    };
    void *data;
    void *es;
    lli_t *lli;
    SceMpegAu au;
    int inited, rb_made, created, mods;
} avc;
static int avc_ready;
static unsigned avc_retry_at; /* init falhou: não tenta de novo a cada frame */
static int avc_retry_wait;

static void avc_term(void)
{
    if (avc.es)
        sceMpegFreeAvcEsBuf(&avc.mpeg, avc.es);
    if (avc.created)
        sceMpegDelete(&avc.mpeg);
    if (avc.rb_made)
        sceMpegRingbufferDestruct(&avc.rb);
    if (avc.inited)
        sceMpegFinish();
    free(avc.data);
    free(avc.lli);
    if (avc.mods) {
        sceUtilityUnloadModule(PSP_MODULE_AV_MPEGBASE);
        sceUtilityUnloadModule(PSP_MODULE_AV_AVCODEC);
    }
    memset(&avc, 0, sizeof(avc));
    avc_ready = 0;
}

static int avc_fail(const char *what, int r)
{
    snprintf(last_error, sizeof(last_error), "h264 %s: 0x%08X", what, r);
    avc_term();
    return r < 0 ? r : -1;
}

static int avc_init(void)
{
    if (avc_ready)
        return 0;
    if (avc_retry_wait && (int)(sceKernelGetSystemTimeLow() - avc_retry_at) < 0)
        return -1; /* last_error ainda diz por que falhou */
    avc_retry_wait = 1;
    avc_retry_at = sceKernelGetSystemTimeLow() + 3 * 1000 * 1000;
    hw_term(); /* o sceJpeg usa o mesmo avcodec */
    /* como no teste do PSP-3000 (e como os jogos): 0x300 + 0x303 */
    int r = sceUtilityLoadModule(PSP_MODULE_AV_AVCODEC);
    if (r < 0 && r != ERR_ALREADY_LOADED)
        return avc_fail("avcodec", r);
    r = sceUtilityLoadModule(PSP_MODULE_AV_MPEGBASE);
    if (r < 0 && r != ERR_ALREADY_LOADED)
        return avc_fail("mpegbase", r);
    avc.mods = 1;
    if ((r = sceMpegInit()) != 0)
        return avc_fail("sceMpegInit", r);
    avc.inited = 1;
    int size = sceMpegQueryMemSize(0);
    if (size <= 0)
        return avc_fail("sceMpegQueryMemSize", size);
    if (!(avc.data = memalign(64, size)) || !(avc.lli = memalign(64, sizeof(lli_t) * (AVC_MAX_AU / DMA_BLOCK + 1))))
        return avc_fail("memoria", -1);
    if ((r = sceMpegRingbufferConstruct(&avc.rb, 0, NULL, 0, NULL, NULL)) != 0)
        return avc_fail("ringbuffer", r);
    avc.rb_made = 1;
    if ((r = sceMpegCreate(&avc.mpeg, avc.data, size, &avc.rb, FB_STRIDE, 0, 0)) != 0)
        return avc_fail("sceMpegCreate", r);
    avc.created = 1;
    SceMpegAvcMode mode = {-1, SCE_MPEG_AVC_FORMAT_8888};
    if ((r = sceMpegAvcDecodeMode(&avc.mpeg, &mode)) != 0)
        return avc_fail("sceMpegAvcDecodeMode", r);
    if (!(avc.es = sceMpegMallocAvcEsBuf(&avc.mpeg)))
        return avc_fail("sceMpegMallocAvcEsBuf", -1);
    memset(&avc.au, 0xFF, sizeof(avc.au));
    avc.au.iEsBuffer = 1; /* como o PMP: o primeiro ES buffer */
    avc_ready = 1;
    avc_retry_wait = 0;
    return 0;
}

/* Um AU para o decoder: cópia por DMA para o Media Engine + sceMpegAvcDecode.
 * A imagem que sai (se sair: o decoder segura 2) vai para dst. */
static int avc_feed(const uint8_t *data, int size, uint32_t *dst, SceInt32 *got)
{
    const uint8_t *src = data;
    uint8_t *me = (uint8_t *)ME_AVC_BUF;
    int left = size, i = 0;
    for (;;) {
        avc.lli[i].src = (void *)src;
        avc.lli[i].dst = me;
        if (left > DMA_BLOCK) {
            avc.lli[i].size = DMA_BLOCK;
            avc.lli[i].next = &avc.lli[i + 1];
            src += DMA_BLOCK;
            me += DMA_BLOCK;
            left -= DMA_BLOCK;
            i++;
        } else {
            avc.lli[i].size = left;
            avc.lli[i].next = NULL;
            break;
        }
    }
    /* o DMA lê a RAM, não o cache; e o ME escreve na VRAM por baixo do cache */
    sceKernelDcacheWritebackInvalidateAll();
    int r = sceMpegBasePESpacketCopy(avc.lli);
    if (r != 0) {
        snprintf(last_error, sizeof(last_error), "h264 PESpacketCopy: 0x%08X", r);
        return -1;
    }
    avc.au.iAuSize = size;
    *got = 0;
    void *out = dst;
    if ((r = sceMpegAvcDecode(&avc.mpeg, &avc.au, FB_STRIDE, &out, got)) != 0) {
        snprintf(last_error, sizeof(last_error), "h264 sceMpegAvcDecode: 0x%08X", r);
        return -1;
    }
    return 0;
}

static int is_aud(const uint8_t *p, int left)
{
    return left >= 5 && p[0] == 0 && p[1] == 0 && p[2] == 0 && p[3] == 1 && (p[4] & 0x1F) == 9;
}

int decoder_h264_packet(const uint8_t *data, int size)
{
    if (!is_aud(data, size))
        return 0;
    for (int i = 5; i + 3 < size && i < 64; i++) /* a NAL depois do AUD */
        if (data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1) {
            int type = data[i + 3] & 0x1F;
            return type == 7 || type == 5 ? H264_P_IDR : H264_P;
        }
    return H264_P;
}

/* Frames P: o pacote traz o frame e 2 cópias dele, cada um começando com um
 * AUD. O decoder do PSP só solta a imagem de 2 chamadas atrás, então a última
 * chamada (a 2ª cópia) solta o frame real. Sem Stop: ele zeraria as
 * referências que o próximo frame P usa. Como no teste v2 do psp/probe (o
 * único caminho medido no PSP-3000), cada AU vai com o AUD e sai de um buffer
 * alinhado a 64 bytes: o 1º já está no início do slot; as cópias (~20-30
 * bytes) passam pelo `stage`. */
#define AVC_STAGE 8192
static uint8_t stage[AVC_STAGE] __attribute__((aligned(64)));

static int avc_decode_packet(const uint8_t *data, int size, uint32_t *dst)
{
    int starts[8], n = 0;
    for (int i = 0; i + 5 <= size && n < 8; i++)
        if (is_aud(data + i, size - i)) {
            starts[n++] = i;
            i += 4;
        }
    SceInt32 got = 0;
    for (int k = 0; k < n; k++) {
        int b = starts[k], len = (k + 1 < n ? starts[k + 1] : size) - b;
        const uint8_t *au = data + b;
        if ((uintptr_t)au & 63) {
            if (len > AVC_STAGE) {
                snprintf(last_error, sizeof(last_error), "h264p: AU %d de %d bytes", k, len);
                return -1;
            }
            memcpy(stage, au, len);
            au = stage;
        }
        if (avc_feed(au, len, dst, &got) < 0)
            return -1;
    }
    if (!got) {
        snprintf(last_error, sizeof(last_error), "h264p: o decoder nao devolveu imagem");
        return -1;
    }
    return 0;
}

/* data: alinhado a 64 bytes (slot do stream). Sai em dst (VRAM, largura 512). */
static int avc_decode(const uint8_t *data, int size, uint32_t *dst, int *w, int *h)
{
    if (size > AVC_MAX_AU) {
        snprintf(last_error, sizeof(last_error), "h264: frame de %d KB grande demais", size / 1024);
        return -1;
    }
    if (avc_init() < 0)
        return -1;
    *w = SCR_W;
    *h = SCR_H;
    if (decoder_h264_packet(data, size))
        return avc_decode_packet(data, size, dst);
    SceInt32 got = 0;
    if (avc_feed(data, size, dst, &got) < 0)
        return -1;
    int r;
    /* No PSP o frame só sai com o Stop (que escreve em bufs[0]); no PPSSPP já
     * sai no decode (got = 1) e o Stop não devolve nada. */
    void *bufs[4] = {dst, dst, dst, dst};
    SceInt32 n = 0;
    if ((r = sceMpegAvcDecodeStop(&avc.mpeg, FB_STRIDE, bufs, &n)) != 0) {
        snprintf(last_error, sizeof(last_error), "h264 sceMpegAvcDecodeStop: 0x%08X", r);
        return -1;
    }
    if (!got && n <= 0) {
        snprintf(last_error, sizeof(last_error), "h264: o decoder nao devolveu imagem");
        return -1;
    }
    return 0;
}

int decoder_is_h264(const uint8_t *data, int size)
{
    return size > 4 && data[0] == 0 && data[1] == 0 && (data[2] == 1 || (data[2] == 0 && data[3] == 1));
}

static int sw_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h)
{
    if (tj3DecompressHeader(tj, jpeg, size) < 0) {
        snprintf(last_error, sizeof(last_error), "%s", tj3GetErrorStr(tj));
        return -1;
    }
    int jw = tj3Get(tj, TJPARAM_JPEGWIDTH);
    int jh = tj3Get(tj, TJPARAM_JPEGHEIGHT);
    if (jw <= 0 || jh <= 0 || jw > SCR_W || jh > SCR_H) {
        snprintf(last_error, sizeof(last_error), "tamanho %dx%d invalido", jw, jh);
        return -1;
    }
    uint32_t *out = dst + ((SCR_H - jh) / 2) * FB_STRIDE + (SCR_W - jw) / 2;
    if (tj3Decompress8(tj, jpeg, size, (unsigned char *)out, FB_STRIDE * 4, TJPF_RGBA) < 0) {
        snprintf(last_error, sizeof(last_error), "%s", tj3GetErrorStr(tj));
        return -1;
    }
    *w = jw;
    *h = jh;
    return 0;
}

int decoder_init(int mode)
{
    tj = tj3Init(TJINIT_DECOMPRESS);
    if (!tj) {
        snprintf(last_error, sizeof(last_error), "tj3Init falhou");
        return -1;
    }
    /* Mais rápido; a perda de qualidade é quase invisível em 480x272. */
    tj3Set(tj, TJPARAM_FASTDCT, 1);
    tj3Set(tj, TJPARAM_FASTUPSAMPLE, 1);

    kind = DEC_SW;
    if (mode != DEC_SW)
        decoder_select(DEC_HW);
    return 0;
}

int decoder_select(int want)
{
    /* com o H.264 ativo, o sceJpeg só volta quando chegar um JPEG */
    if (want == DEC_HW && (avc_ready || hw_init() == 0))
        kind = DEC_HW;
    else
        kind = DEC_SW;
    return kind;
}

int decoder_kind(void)
{
    return kind;
}

int decoder_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h)
{
    if (decoder_is_h264(jpeg, size)) {
        last_h264 = decoder_h264_packet(jpeg, size) ? 2 : 1;
        return avc_decode(jpeg, size, dst, w, h);
    }
    last_h264 = 0;
    if (avc_ready) /* o servidor voltou para JPEG: devolve o avcodec ao sceJpeg */
        avc_term();
    if (kind == DEC_HW && !hw_ready && hw_init() < 0) /* o H.264 desligou o sceJpeg */
        kind = DEC_SW;
    if (kind == DEC_HW) {
        /* Ler o cabeçalho é barato. O hardware só recebe o que aceita
         * (4:2:0, tela cheia); o resto vai para o software, que também
         * centraliza imagens menores. */
        if (tj3DecompressHeader(tj, jpeg, size) == 0 && tj3Get(tj, TJPARAM_SUBSAMP) == TJSAMP_420 &&
            tj3Get(tj, TJPARAM_JPEGWIDTH) == SCR_W && tj3Get(tj, TJPARAM_JPEGHEIGHT) == SCR_H)
            return hw_decode(jpeg, size, dst, w, h);
    }
    return sw_decode(jpeg, size, dst, w, h);
}

const char *decoder_name(void)
{
    if (last_h264)
        return last_h264 == 2 ? "h264p" : "h264";
    if (kind == DEC_HW)
        return hw_direct ? "hw" : "hw-ram";
    return "sw";
}

const char *decoder_error(void)
{
    return last_error;
}

void decoder_term(void)
{
    if (avc_ready)
        avc_term();
    hw_term();
    free(hw_bounce);
    hw_bounce = NULL;
    if (tj)
        tj3Destroy(tj);
    tj = NULL;
}
