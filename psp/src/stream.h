#ifndef PSPSTREAM_STREAM_H
#define PSPSTREAM_STREAM_H

#include <stdint.h>

typedef struct {
    uint8_t *data;     /* alinhado a 64 bytes, PS_MAX_JPEG bytes */
    int size;
    uint32_t frame_no;
    uint32_t send_ts;
    unsigned t_req;    /* us: pedido enviado */
    unsigned t_recv;   /* us: frame recebido por inteiro */
} ps_frame_t;

/* Dados do último frame exibido; vão no próximo pedido (ack + estatísticas). */
typedef struct {
    uint32_t frame_no;
    uint32_t send_ts;
    unsigned t_shown;
    uint16_t net_t, local_t, decode_t; /* 0,1 ms */
} ps_ack_t;

/* Inicia a thread de rede, que pede e recebe frames sem parar. */
int stream_start(int sock, int prefetch, volatile int *running);

/* Pega o frame pronto mais novo, esperando até timeout_us. NULL se não há. */
ps_frame_t *stream_take(unsigned timeout_us);

/* Devolve o slot depois de exibir o frame. */
void stream_release(ps_frame_t *frame, const ps_ack_t *ack);

/* Estado dos controles, enviado em todo pedido. */
void stream_set_input(uint32_t buttons, uint8_t lx, uint8_t ly);

/* Envia só os controles agora, sem pedir frame (Marco 4: menor latência de entrada). */
int stream_send_input(void);

void stream_set_prefetch(int on);

/* != 0 quando a thread de rede parou (conexão caiu). */
int stream_error(void);

/* Frames recebidos e descartados por ficarem velhos antes do decode. */
unsigned stream_dropped(void);

void stream_stop(void);

#endif
