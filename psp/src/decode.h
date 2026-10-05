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
 * centralizado. JPEG ou H.264 (todo frame IDR), detectado pelos primeiros
 * bytes. Os dados precisam estar alinhados a 64 bytes.
 * Devolve 0 se ok e preenche *w, *h. */
int decoder_decode(const uint8_t *jpeg, int size, uint32_t *dst, int *w, int *h);

/* 1 se os dados são H.264 Annex B (começam com start code); senão JPEG. */
int decoder_is_h264(const uint8_t *data, int size);

/* Pacote de frames P do servidor (AUD + frame + 2 cópias): 0 = não é (JPEG
 * ou H.264 só IDR), H264_P = frame P, H264_P_IDR = começa com IDR. */
enum { H264_P = 1, H264_P_IDR = 2 };
int decoder_h264_packet(const uint8_t *data, int size);

const char *decoder_name(void);
const char *decoder_error(void);

void decoder_term(void);

#endif
