#ifndef PSPSTREAM_DECODE_H
#define PSPSTREAM_DECODE_H

#include <stdint.h>

int decoder_init(void);

/* Decodifica para dst (framebuffer com cache, stride FB_STRIDE), centralizado.
 * Devolve 0 se ok e preenche *w, *h. */
int decoder_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h);

const char *decoder_name(void);
const char *decoder_error(void);

void decoder_term(void);

#endif
