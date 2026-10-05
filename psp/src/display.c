/*
 * Escrita direta no framebuffer, 32 bits (8888), três buffers na VRAM
 * (3 x 544 KB de 2 MB). O decoder escreve direto no buffer de desenho, sem
 * cópia intermediária.
 */
#include "display.h"

#include <pspdebug.h>
#include <pspdisplay.h>
#include <pspkernel.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define VRAM_CACHED 0x04000000u
#define UNCACHED(p) ((void *)((uint32_t)(p) | 0x40000000u))
#define PHYS(p) ((uint32_t)(p) & 0x0FFFFFFFu)

#define NUM_FB 3

static uint32_t *fb[NUM_FB];
static int draw_idx;    /* buffer onde o próximo frame é desenhado */
static int console_row;

void display_init(void)
{
    for (int i = 0; i < NUM_FB; i++) {
        fb[i] = (uint32_t *)(VRAM_CACHED + i * FB_BYTES);
        memset(UNCACHED(fb[i]), 0, FB_BYTES);
    }
    sceDisplaySetMode(0, SCR_W, SCR_H);
    sceDisplaySetFrameBuf(fb[0], FB_STRIDE, PSP_DISPLAY_PIXEL_FORMAT_8888, PSP_DISPLAY_SETBUF_NEXTFRAME);
    draw_idx = 1;

    pspDebugScreenInitEx(UNCACHED(fb[0]), PSP_DISPLAY_PIXEL_FORMAT_8888, 0);
    pspDebugScreenEnableBackColor(1);
    pspDebugScreenSetBackColor(0xFF000000);
    console_row = 0;
}

void display_console(const char *fmt, ...)
{
    char buf[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    if (console_row >= 33) { /* 272 / 8 = 34 linhas */
        memset(UNCACHED(fb[0]), 0, FB_BYTES);
        console_row = 0;
    }
    pspDebugScreenSetBase(UNCACHED(fb[0]));
    pspDebugScreenSetTextColor(0xFFFFFFFF);
    pspDebugScreenSetXY(0, console_row++);
    pspDebugScreenPuts(buf);
    printf("%s\n", buf); /* aparece no console do PPSSPP / psplink */

    /* O console sempre fica visível, mesmo se o stream já tinha começado. */
    sceDisplaySetFrameBuf(fb[0], FB_STRIDE, PSP_DISPLAY_PIXEL_FORMAT_8888, PSP_DISPLAY_SETBUF_NEXTFRAME);
    if (draw_idx == 0)
        draw_idx = 1;
}

void display_console_clear(void)
{
    memset(UNCACHED(fb[0]), 0, FB_BYTES);
    console_row = 0;
}

uint32_t *display_back(void)
{
    return fb[draw_idx];
}

void display_text(int x, int y, uint32_t color, const char *fmt, ...)
{
    char buf[96];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);

    pspDebugScreenSetBase(UNCACHED(fb[draw_idx]));
    pspDebugScreenSetTextColor(color);
    pspDebugScreenSetXY(x, y);
    pspDebugScreenPuts(buf);
}

void display_writeback(void)
{
    /* O dcache tem 16 KB: gravar tudo é mais barato que percorrer 544 KB. */
    sceKernelDcacheWritebackAll();
}

void display_clear_back(void)
{
    /* Sem isso, linhas velhas no cache (de um frame anterior neste buffer)
     * poderiam ser gravadas por cima dos zeros nas bordas da imagem. */
    sceKernelDcacheWritebackInvalidateAll();
    memset(UNCACHED(fb[draw_idx]), 0, FB_BYTES);
}

void display_flip(int vsync)
{
    int pending = draw_idx;
    sceDisplaySetFrameBuf(fb[pending], FB_STRIDE, PSP_DISPLAY_PIXEL_FORMAT_8888,
                          vsync ? PSP_DISPLAY_SETBUF_NEXTFRAME : PSP_DISPLAY_SETBUF_IMMEDIATE);

    /* Próximo alvo: nem o que acabou de ser enviado, nem o que está na tela.
     * Se um vblank acontecer agora, a tela só pode passar a mostrar o
     * pendente, então o alvo escolhido continua livre. */
    void *shown = NULL;
    int bw, fmt;
    sceDisplayGetFrameBuf(&shown, &bw, &fmt, PSP_DISPLAY_SETBUF_IMMEDIATE);
    for (int i = 0; i < NUM_FB; i++) {
        if (i != pending && PHYS(fb[i]) != PHYS(shown)) {
            draw_idx = i;
            break;
        }
    }
}

void display_wait_vblank(void)
{
    sceDisplayWaitVblankStart();
}
