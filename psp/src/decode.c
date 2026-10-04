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
 */
#include "decode.h"
#include "display.h"

#include <malloc.h>
#include <pspjpeg.h>
#include <pspkernel.h>
#include <psputility.h>
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
    if (want == DEC_HW && hw_init() == 0)
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
    if (hw_ready) {
        sceJpegDeleteMJpeg();
        sceJpegFinishMJpeg();
        sceUtilityUnloadAvModule(PSP_AV_MODULE_AVCODEC);
        hw_ready = 0;
    }
    free(hw_bounce);
    hw_bounce = NULL;
    if (tj)
        tj3Destroy(tj);
    tj = NULL;
}
