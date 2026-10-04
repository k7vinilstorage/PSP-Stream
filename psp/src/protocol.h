/*
 * PSPStream - protocolo v1 (TCP, little-endian). Ver docs/PROTOCOL.md.
 * Manter em sincronia com server/protocol.py.
 */
#ifndef PSPSTREAM_PROTOCOL_H
#define PSPSTREAM_PROTOCOL_H

#include <stdint.h>

#define PS_DEFAULT_PORT 5123

#define PS_MAGIC_REQ   0x31435350u /* "PSC1" */
#define PS_MAGIC_FRAME 0x31465350u /* "PSF1" */

/* Maior JPEG aceito pelo cliente. O servidor nunca envia nada maior. */
#define PS_MAX_JPEG (256 * 1024)

/* flags de ps_req_t */
#define PS_REQ_FRAME 0x0001 /* pede o próximo frame */
#define PS_REQ_HELLO 0x0002 /* primeira mensagem da conexão */

/* PSP -> PC (28 bytes). Toda mensagem leva o estado dos controles. */
typedef struct __attribute__((packed)) {
    uint32_t magic;     /* PS_MAGIC_REQ */
    uint32_t buttons;   /* máscara PSP_CTRL_* */
    uint8_t lx, ly;     /* analógico, 0..255 (128 = centro) */
    uint16_t flags;     /* PS_REQ_* */
    uint32_t ack_frame; /* último frame exibido (0 = nenhum) */
    uint32_t echo_ts;   /* send_ts desse frame, devolvido sem alteração */
    uint16_t net_t;     /* 0,1 ms: pedido enviado -> frame recebido inteiro */
    uint16_t local_t;   /* 0,1 ms: frame recebido -> exibido (fila + decode + flip) */
    uint16_t since_t;   /* 0,1 ms: frame exibido -> envio desta mensagem */
    uint16_t decode_t;  /* 0,1 ms: só o decode */
} ps_req_t;

/* PC -> PSP (16 bytes), seguido de `size` bytes de JPEG. */
typedef struct __attribute__((packed)) {
    uint32_t magic;    /* PS_MAGIC_FRAME */
    uint32_t frame_no; /* começa em 1, cresce a cada envio */
    uint32_t size;     /* bytes de JPEG que seguem */
    uint32_t send_ts;  /* relógio do servidor em ms (32 bits, dá a volta) */
} ps_frame_hdr_t;

_Static_assert(sizeof(ps_req_t) == 28, "ps_req_t deve ter 28 bytes");
_Static_assert(sizeof(ps_frame_hdr_t) == 16, "ps_frame_hdr_t deve ter 16 bytes");

#endif
