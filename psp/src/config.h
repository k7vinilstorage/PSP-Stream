#ifndef PSPSTREAM_CONFIG_H
#define PSPSTREAM_CONFIG_H

typedef struct {
    char host[64];
    int port;
    int wifi_profile; /* perfil de rede salvo no XMB (1 = primeiro) */
    int decoder;      /* DEC_AUTO, DEC_SW ou DEC_HW */
    int vsync;        /* 1 = troca de buffer no vblank (sem rasgo, +0..16 ms) */
    int prefetch;     /* 1 = pede o próximo frame antes de decodificar o atual */
    int overlay;      /* 1 = mostra FPS/estatísticas */
    int rcvbuf_kb;    /* buffer de recepção TCP em KB */
    int bench;        /* 1 = mede decode hw x sw com o primeiro frame */
    int exit_after;   /* testes: sai depois de exibir N frames (0 = nunca) */
} ps_config_t;

/* Lê `dir`/server.txt. Devolve 0 se ok; senão escreve o motivo em err. */
int config_load(ps_config_t *cfg, const char *dir, char *err, int errlen);

#endif
