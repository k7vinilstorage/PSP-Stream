#ifndef PSPSTREAM_DISPLAY_H
#define PSPSTREAM_DISPLAY_H

#include <stdint.h>

#define SCR_W 480
#define SCR_H 272
#define FB_STRIDE 512 /* pixels por linha do framebuffer */
#define FB_BYTES (FB_STRIDE * SCR_H * 4)

void display_init(void);

/* Modo console: texto de status na tela (antes do stream começar). */
void display_console(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Buffer onde desenhar o próximo frame (endereço com cache, stride 512). */
uint32_t *display_back(void);

/* Texto sobre o buffer de desenho (escrita sem cache: chamar depois de
 * display_writeback()). x, y em células de 8x8. */
void display_text(int x, int y, uint32_t color, const char *fmt, ...) __attribute__((format(printf, 4, 5)));

/* Grava no VRAM o que o CPU escreveu via cache. */
void display_writeback(void);

/* Pinta de preto o buffer de desenho. */
void display_clear_back(void);

/* Mostra o buffer de desenho. vsync=1: troca no próximo vblank sem bloquear
 * (triple buffering). vsync=0: troca imediata (pode rasgar). */
void display_flip(int vsync);

/* Espera o próximo vblank. */
void display_wait_vblank(void);

#endif
