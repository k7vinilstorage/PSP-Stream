#ifndef PSPSTREAM_IMA_H
#define PSPSTREAM_IMA_H

#include <stdint.h>

/* Decodifica um bloco IMA ADPCM do WAV (o adpcmenc do GStreamer, layout dvi;
 * server/audio.py tem a mesma referência em Python). out recebe `samples`
 * frames estéreo intercalados (mono vira os dois lados). Devolve os frames
 * escritos (menos que `samples` se o bloco for curto). C puro: os testes do
 * servidor compilam este arquivo no PC e comparam com a referência. */
int ima_decode_block(const uint8_t *block, int len, int channels, int samples, int16_t *out);

#endif
