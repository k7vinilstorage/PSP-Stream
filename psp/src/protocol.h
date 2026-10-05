/*
 * PSPStream - protocolo v5 (TCP ou UDP, little-endian). Ver docs/PROTOCOL.md.
 * Manter em sincronia com server/protocol.py.
 */
#ifndef PSPSTREAM_PROTOCOL_H
#define PSPSTREAM_PROTOCOL_H

#include <stdint.h>

#define PS_DEFAULT_PORT 5123

#define PS_MAGIC_REQ   0x35435350u /* "PSC5" (v5: pedido antecipado automático) */
#define PS_MAGIC_FRAME 0x31465350u /* "PSF1" */
#define PS_MAGIC_CHUNK 0x32555350u /* "PSU2" */
#define PS_MAGIC_PONG  0x314F5350u /* "PSO1" */
#define PS_MAGIC_AUDIO 0x31415350u /* "PSA1": som, UDP, empurrado pelo PC */

/* Maior JPEG aceito pelo cliente. O servidor nunca envia nada maior. */
#define PS_MAX_JPEG (256 * 1024)

/* flags de ps_req_t */
#define PS_REQ_FRAME 0x0001 /* pede o próximo frame */
#define PS_REQ_HELLO 0x0002 /* primeira mensagem da conexão */
#define PS_REQ_NACK 0x0004  /* UDP: seguido de ps_nack_t (pedaços que faltam). Com PS_REQ_FRAME (frames P):
                             * pedido repetido; se o servidor já mandou o frame do NACK, reenvia esse */
#define PS_REQ_BYE 0x0008   /* UDP: o PSP está saindo (não há "fechar conexão") */
#define PS_REQ_PING 0x0010  /* UDP: o servidor responde já com ps_pong_t (echo_ts = token) */
#define PS_REQ_IDR 0x0020   /* H.264 com frames P: a corrente de referências quebrou, mande um IDR */

/* UDP: frame em pedaços de até PS_CHUNK_PAYLOAD bytes (cabe num pacote de 1500). */
#define PS_CHUNK_PAYLOAD 1400
#define PS_MAX_CHUNKS 256

/* PSP -> PC (52 bytes). Toda mensagem leva o estado dos controles. */
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
    /* v2: onde está o tempo de rede */
    uint16_t first_t;   /* 0,1 ms: pedido -> primeiro pedaço/byte do frame (ida e volta) */
    uint16_t burst_t;   /* 0,1 ms: primeiro -> último pedaço (vazão real do enlace) */
    uint8_t signal;     /* sinal do Wi-Fi, % */
    uint8_t wflags;     /* PS_WIFI_* */
    uint16_t lost;      /* UDP: frames abandonados incompletos desde o início do stream */
    /* v3 */
    uint32_t hdr_have;    /* UDP: id do cabeçalho JPEG guardado (0 = nenhum) */
    uint16_t ping_select; /* 0,1 ms: ida e volta pura no início, esperando com select() */
    uint16_t ping_poll;   /* 0,1 ms: idem, consultando o socket a cada 0,5 ms (0 = não medido) */
    /* v4 */
    uint16_t ping_live;     /* 0,1 ms: ping a cada 1 s durante o stream, média móvel (0 = ainda não) */
    uint16_t ping_live_min; /* 0,1 ms: o menor dos últimos 8 */
    /* v5 */
    int16_t idle_t;  /* 0,1 ms: fim do frame anterior -> 1º pedaço deste (negativo = chegou antes, em fila) */
    uint16_t early_b; /* UDP: pede o próximo quando faltam estes bytes do atual (0 = só no fim) */
} ps_req_t;

#define PS_WIFI_POWER_SAVE 0x01 /* "Economia de energia WLAN" ligada no XMB */
#define PS_WIFI_RX_POLL 0x02    /* esperando pacotes por consulta, não select() */
#define PS_CAP_H264 0x04        /* decodifica H.264 (todo frame IDR) pelo hardware */
#define PS_CAP_H264P 0x08       /* ... e frames P: pacote AUD + frame + 2 cópias (server/h264.py) */
#define PS_CAP_AUDIO 0x10       /* UDP: toca o som (pacotes PS_MAGIC_AUDIO); sem este bit, o PC não manda */

#define PS_IDLE_NONE (-0x8000) /* idle_t: não medido (TCP) */

/* PC -> PSP (16 bytes), seguido de `size` bytes de JPEG. */
typedef struct __attribute__((packed)) {
    uint32_t magic;    /* PS_MAGIC_FRAME */
    uint32_t frame_no; /* começa em 1, cresce a cada envio */
    uint32_t size;     /* bytes de JPEG que seguem */
    uint32_t send_ts;  /* relógio do servidor em ms (32 bits, dá a volta) */
} ps_frame_hdr_t;

/* PC -> PSP, UDP (24 bytes), seguido do pedaço `chunk` do payload: bytes
 * [chunk * PS_CHUNK_PAYLOAD, ...) até o fim do pedaço ou do frame. */
typedef struct __attribute__((packed)) {
    uint32_t magic;    /* PS_MAGIC_CHUNK */
    uint32_t frame_no;
    uint32_t size;     /* tamanho total do payload */
    uint32_t send_ts;
    uint16_t chunk;    /* índice deste pedaço */
    uint16_t count;    /* total de pedaços do frame */
    uint32_t hdr;      /* bits 0-30: id do cabeçalho deste JPEG; PS_HDR_STRIPPED: payload sem ele */
} ps_chunk_hdr_t;

/* O cabeçalho JPEG (início até o fim do SOS, ~620 bytes) é igual em todo frame
 * da mesma qualidade. O PSP guarda o último e diz qual tem (hdr_have); o
 * servidor então manda só os dados comprimidos. */
#define PS_HDR_STRIPPED 0x80000000u
#define PS_HDR_ID_MASK 0x7FFFFFFFu
#define PS_MAX_JPEG_HEADER 2048

/* PC -> PSP, UDP: resposta a PS_REQ_PING (8 bytes). */
typedef struct __attribute__((packed)) {
    uint32_t magic; /* PS_MAGIC_PONG */
    uint32_t token; /* echo_ts do ping */
} ps_pong_t;

/* PC -> PSP, UDP (20 bytes), seguido de um bloco IMA ADPCM do WAV (server/audio.py).
 * O PC manda um a cada ~20 ms enquanto o PSP pede som (PS_CAP_AUDIO). */
typedef struct __attribute__((packed)) {
    uint32_t magic;    /* PS_MAGIC_AUDIO */
    uint32_t seq;      /* +1 por pacote: buraco = pacote perdido */
    uint32_t pos;      /* amostra (por canal) do início do bloco */
    uint16_t rate;     /* Hz: 22050, 32000, 44100 ou 48000 */
    uint8_t channels;  /* 1 ou 2 */
    uint8_t codec;     /* PS_AUDIO_IMA */
    uint16_t samples;  /* amostras por canal no bloco */
    uint16_t reserved;
} ps_audio_hdr_t;

#define PS_AUDIO_IMA 1

/* PSP -> PC, UDP, logo depois de um ps_req_t com PS_REQ_NACK (36 bytes). */
typedef struct __attribute__((packed)) {
    uint32_t frame_no;
    uint32_t missing[PS_MAX_CHUNKS / 32]; /* bit i = pedaço i faltando */
} ps_nack_t;

_Static_assert(sizeof(ps_req_t) == 52, "ps_req_t deve ter 52 bytes");
_Static_assert(sizeof(ps_chunk_hdr_t) == 24, "ps_chunk_hdr_t deve ter 24 bytes");
_Static_assert(sizeof(ps_pong_t) == 8, "ps_pong_t deve ter 8 bytes");
_Static_assert(sizeof(ps_nack_t) == 36, "ps_nack_t deve ter 36 bytes");
_Static_assert(sizeof(ps_frame_hdr_t) == 16, "ps_frame_hdr_t deve ter 16 bytes");
_Static_assert(sizeof(ps_audio_hdr_t) == 20, "ps_audio_hdr_t deve ter 20 bytes");

#endif
