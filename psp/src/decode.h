#ifndef PSPSTREAM_DECODE_H
#define PSPSTREAM_DECODE_H

#include <stdint.h>

enum { DEC_AUTO = 0, DEC_SW = 1, DEC_HW = 2 };

/* mode: DEC_AUTO tenta o hardware e cai para software se ele falhar. */
int decoder_init(int mode);

/* Troca o decoder em tempo de execução. Devolve o que ficou ativo. */
int decoder_select(int kind);
int decoder_kind(void);

/* Decodifica para dst (framebuffer, endereço com cache, stride FB_STRIDE),
 * centralizado. Os dados do jpeg precisam estar alinhados a 64 bytes.
 * Devolve 0 se ok e preenche *w, *h. */
int decoder_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h);

const char *decoder_name(void);
const char *decoder_error(void);

void decoder_term(void);

#endif
