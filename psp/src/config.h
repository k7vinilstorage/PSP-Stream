#ifndef PSPSTREAM_CONFIG_H
#define PSPSTREAM_CONFIG_H

typedef struct {
    char host[64];
    int port;
    int wifi_profile; /* perfil de rede salvo no XMB (1 = primeiro) */
    int decoder;      /* DEC_AUTO, DEC_SW ou DEC_HW */
    int vsync;        /* 1 = troca de buffer no vblank (sem rasgo, +0..16 ms) */
    int prefetch;     /* 1 = pede o próximo frame antes de decodificar o atual; 0 = depois de exibir;
                       * PREFETCH_AUTO = sim, menos com frames P */
    int overlay;      /* 1 = mostra FPS/estatísticas */
    int rcvbuf_kb;    /* buffer de recepção TCP em KB */
    int bench;        /* 1 = mede decode hw x sw com o primeiro frame */
    int input;        /* 1 = envia os controles para o PC (Marco 4) */
    int udp;          /* transporte: 0 = TCP, 1 = UDP */
    int h264p;        /* aceita H.264 com frames P (o servidor decide com --codec h264p) */
    int early_kb;     /* UDP: pede o próximo frame quando faltar isso do atual (0 = só no fim, -1 = auto) */
    int rxwait;       /* UDP: RXWAIT_AUTO, RXWAIT_SELECT ou RXWAIT_POLL */
    int h264;         /* 1 = aceita H.264 do servidor (--codec h264) */
    int exit_after;   /* testes: sai depois de exibir N frames (0 = nunca) */
    int menu_wait;    /* s até conectar sozinho com a tela de configuração aberta (0 = conecta direto) */
    int menu_shot;    /* testes: desenha a tela de configuração, tira o screenshot e sai */
} ps_config_t;

/* prefetch=auto (padrão). Com frames P, pedir o próximo só depois de exibir
 * o atual deixou o stream liso no PSP-3000 (Hollow Knight): sem fila, um
 * frame por vez, ritmo regular. No JPEG e no H.264 só com quadros completos,
 * o prefetch vale 1,2-1,7x de FPS (medido) e continua ligado. */
#define PREFETCH_AUTO (-1)

/* Como a thread de rede espera pacotes (UDP). */
enum { RXWAIT_AUTO, RXWAIT_SELECT, RXWAIT_POLL };

/* Lê `dir`/server.txt. Devolve 0 se ok; senão escreve o motivo em err (e
 * *cfg fica com os padrões e o que deu para ler, para a tela de configuração). */
int config_load(ps_config_t *cfg, const char *dir, char *err, int errlen);

/* Grava `dir`/server.txt com todas as opções (os comentários do arquivo
 * antigo não são mantidos). Devolve 0 se ok. */
int config_save(const ps_config_t *cfg, const char *dir, char *err, int errlen);

#endif
