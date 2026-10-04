/*
 * Decoder em software: libjpeg-turbo (API TurboJPEG v3), escrevendo direto no
 * framebuffer com pitch de 512 pixels.
 */
#include "decode.h"
#include "display.h"

#include <stdio.h>
#include <turbojpeg.h>

static tjhandle tj;
static char last_error[96];

int decoder_init(void)
{
    tj = tj3Init(TJINIT_DECOMPRESS);
    if (!tj) {
        snprintf(last_error, sizeof(last_error), "tj3Init falhou");
        return -1;
    }
    /* Mais rápido, perda de qualidade quase invisível em 480x272. */
    tj3Set(tj, TJPARAM_FASTDCT, 1);
    tj3Set(tj, TJPARAM_FASTUPSAMPLE, 1);
    return 0;
}

int decoder_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h)
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

const char *decoder_name(void)
{
    return "sw";
}

const char *decoder_error(void)
{
    return last_error;
}

void decoder_term(void)
{
    if (tj)
        tj3Destroy(tj);
    tj = NULL;
}
