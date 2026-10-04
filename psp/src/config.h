#ifndef PSPSTREAM_CONFIG_H
#define PSPSTREAM_CONFIG_H

typedef struct {
    char host[64];
    int port;
    int wifi_profile; /* perfil de rede salvo no XMB (1 = primeiro) */
    int exit_after;   /* testes: sai depois de exibir N frames (0 = nunca) */
} ps_config_t;

/* Lê `dir`/server.txt. Devolve 0 se ok; senão escreve o motivo em err. */
int config_load(ps_config_t *cfg, const char *dir, char *err, int errlen);

#endif
