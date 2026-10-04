#ifndef PSPSTREAM_NET_H
#define PSPSTREAM_NET_H

typedef void (*net_status_fn)(const char *msg);

/* Carrega os módulos de rede e inicia a pilha TCP/IP. */
int net_init(void);

/* Conecta ao ponto de acesso salvo no perfil `profile` do XMB e espera o IP.
 * Para se *running virar 0. */
int net_connect_ap(int profile, char *ip_out, int ip_len, net_status_fn status, volatile int *running);

/* Abre a conexão TCP com o servidor. Devolve o socket ou < 0. */
int net_connect_server(const char *host, int port);

int net_send_all(int sock, const void *buf, int len);
int net_recv_all(int sock, void *buf, int len);

/* Desbloqueia um recv() parado em outra thread. */
void net_abort(int sock);

void net_term(void);

#endif
