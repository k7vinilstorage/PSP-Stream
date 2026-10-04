/*
 * PSPStream - protocolo v1 (TCP ou UDP, little-endian). Ver docs/PROTOCOL.md.
 * Manter em sincronia com server/protocol.py.
 */
#ifndef PSPSTREAM_PROTOCOL_H
#define PSPSTREAM_PROTOCOL_H

#include <stdint.h>

#define PS_DEFAULT_PORT 5123

#define PS_MAGIC_REQ   0x31435350u /* "PSC1" */
#define PS_MAGIC_FRAME 0x31465350u /* "PSF1" */
#define PS_MAGIC_CHUNK 0x31555350u /* "PSU1" */

/* Maior JPEG aceito pelo cliente. O servidor nunca envia nada maior. */
#define PS_MAX_JPEG (256 * 1024)

/* flags de ps_req_t */
#define PS_REQ_FRAME 0x0001 /* pede o próximo frame */
#define PS_REQ_HELLO 0x0002 /* primeira mensagem da conexão */
#define PS_REQ_NACK 0x0004  /* UDP: seguido de ps_nack_t (pedaços que faltam) */
#define PS_REQ_BYE 0x0008   /* UDP: o PSP está saindo (não há "fechar conexão") */

/* UDP: frame em pedaços de até PS_CHUNK_PAYLOAD bytes (cabe num pacote de 1500). */
#define PS_CHUNK_PAYLOAD 1400
#define PS_MAX_CHUNKS 256

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

/* PC -> PSP, UDP (20 bytes), seguido do pedaço `chunk` do JPEG: bytes
 * [chunk * PS_CHUNK_PAYLOAD, ...) até o fim do pedaço ou do frame. */
typedef struct __attribute__((packed)) {
    uint32_t magic;    /* PS_MAGIC_CHUNK */
    uint32_t frame_no;
    uint32_t size;     /* tamanho total do JPEG */
    uint32_t send_ts;
    uint16_t chunk;    /* índice deste pedaço */
    uint16_t count;    /* total de pedaços do frame */
} ps_chunk_hdr_t;

/* PSP -> PC, UDP, logo depois de um ps_req_t com PS_REQ_NACK (36 bytes). */
typedef struct __attribute__((packed)) {
    uint32_t frame_no;
    uint32_t missing[PS_MAX_CHUNKS / 32]; /* bit i = pedaço i faltando */
} ps_nack_t;

_Static_assert(sizeof(ps_req_t) == 28, "ps_req_t deve ter 28 bytes");
_Static_assert(sizeof(ps_chunk_hdr_t) == 20, "ps_chunk_hdr_t deve ter 20 bytes");
_Static_assert(sizeof(ps_nack_t) == 36, "ps_nack_t deve ter 36 bytes");
_Static_assert(sizeof(ps_frame_hdr_t) == 16, "ps_frame_hdr_t deve ter 16 bytes");

#endif
