#ifndef PSPSTREAM_STREAM_H
#define PSPSTREAM_STREAM_H

#include <stdint.h>

typedef struct {
    uint8_t *data;     /* alinhado a 64 bytes, PS_MAX_JPEG bytes */
    int size;
    uint32_t frame_no;
    uint32_t send_ts;
    unsigned t_req;    /* us: pedido enviado (ou rádio livre para este frame) */
    unsigned t_first;  /* us: primeiro pedaço/byte chegou */
    unsigned t_recv;   /* us: frame recebido por inteiro */
    int16_t idle_t;    /* 0,1 ms: fim do frame anterior -> 1º pedaço deste (< 0 = em fila); PS_IDLE_NONE */
} ps_frame_t;

/* Dados do último frame exibido; vão no próximo pedido (ack + estatísticas). */
typedef struct {
    uint32_t frame_no;
    uint32_t send_ts;
    unsigned t_shown;
    uint16_t net_t, local_t, decode_t; /* 0,1 ms */
    uint16_t first_t, burst_t;         /* 0,1 ms: ida e volta, rajada */
    int16_t idle_t;                    /* 0,1 ms: tempo morto antes do frame (ps_frame_t) */
} ps_ack_t;

struct sockaddr_in;

/* Inicia a thread de rede, que pede e recebe frames sem parar.
 * udp = 1: sock é UDP e dest é o servidor; udp = 0: sock é TCP conectado.
 * early_bytes (só UDP): pede o próximo frame quando faltarem tantos bytes do
 * atual (0 = só depois de receber inteiro; STREAM_EARLY_AUTO = ida e volta x
 * vazão, medidas). rxwait: RXWAIT_* (só UDP). */
#define STREAM_EARLY_AUTO (-1)
int stream_start(int sock, int udp, const struct sockaddr_in *dest, int prefetch, int early_bytes, int rxwait,
                 volatile int *running);

/* UDP: ida e volta pura medida no início (us, 0 = não medido), se a thread
 * de rede espera consultando o socket (1) ou com select() (0), e o ping a
 * cada 1 s durante o stream (média móvel e mínimo dos últimos 8). */
void stream_ping(unsigned *select_us, unsigned *poll_us, int *polling, unsigned *live_us, unsigned *live_min_us);

/* Pega o frame pronto mais novo (frames P: o mais velho da fila), esperando
 * até timeout_us. NULL se não há. */
ps_frame_t *stream_take(unsigned timeout_us);

/* Devolve o slot depois de exibir o frame. */
void stream_release(ps_frame_t *frame, const ps_ack_t *ack);

/* Diz ao servidor (em todo pedido) se aceitamos H.264. Padrão: sim. */
void stream_set_h264(int on);
/* ... e H.264 com frames P (pacote AUD + frame + 2 cópias). Padrão: sim. */
void stream_set_h264p(int on);

/* Frames P: a partir de qual frame falta referência (um frame se perdeu ou
 * deu erro). Esses frames são pulados até chegar um IDR, que o PSP pede. */
int stream_frame_needs_idr(uint32_t frame_no);
void stream_request_idr(uint32_t from_frame);
void stream_idr_done(uint32_t frame_no);
unsigned stream_idr_requests(void);

/* Estado do Wi-Fi (sinal %, PS_WIFI_*), enviado em todo pedido para o log do servidor. */
void stream_set_wifi(int signal, int flags);

/* Estado dos controles, enviado em todo pedido. */
void stream_set_input(uint32_t buttons, uint8_t lx, uint8_t ly);

/* Envia só os controles agora, sem pedir frame (Marco 4: menor latência de entrada). */
int stream_send_input(void);

/* prefetch: 1, 0 ou PREFETCH_AUTO (config.h: sim, menos com frames P). */
void stream_set_prefetch(int mode);
/* O prefetch está valendo agora? (com auto, depende de o stream ter frames P) */
int stream_prefetch_on(void);

/* != 0 quando a thread de rede parou (conexão caiu). */
int stream_error(void);

/* Frames recebidos e descartados por ficarem velhos antes do decode. */
unsigned stream_dropped(void);

/* UDP: frames abandonados incompletos e NACKs enviados. */
unsigned stream_lost(void);
unsigned stream_nacks(void);
/* UDP: pedidos repetidos por falta de resposta (pedido ou resposta perdidos). */
unsigned stream_retries(void);

/* UDP: bytes que faltam no frame atual quando o próximo é pedido (0 = só no fim). */
unsigned stream_early(void);

/* Frames completos recebidos nesta conexão. */
unsigned stream_completed(void);

void stream_stop(void);

#endif
