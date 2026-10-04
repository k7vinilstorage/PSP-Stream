#ifndef PSPSTREAM_MENU_H
#define PSPSTREAM_MENU_H

#include "config.h"

typedef struct {
    /* Conecta o Wi-Fi (se preciso) e procura o servidor na rede. 0 = achou
     * (cfg->host atualizado); senão o motivo vai em msg. */
    int (*discover)(ps_config_t *cfg, char *msg, int msglen);
    /* Testes (menu_shot=1): tira o screenshot do emulador depois de desenhar. */
    void (*shot)(void);
} menu_hooks_t;

enum { MENU_CONNECT, MENU_QUIT };

/* Tela de configuração: edita *cfg e, se pedido, grava `dir`/server.txt.
 * countdown_s > 0: conecta sozinho depois disso se nenhum botão for
 * apertado (abertura com o IP já configurado). status: mensagem inicial
 * (ex.: por que o server.txt não serviu) ou NULL. */
int menu_run(ps_config_t *cfg, const char *dir, int countdown_s, const char *status, const menu_hooks_t *hooks,
             volatile int *running);

#endif
