#ifndef PSPSTREAM_NET_H
#define PSPSTREAM_NET_H

typedef void (*net_status_fn)(const char *msg);

/* Carrega os módulos de rede e inicia a pilha TCP/IP. */
int net_init(void);

/* Conecta ao ponto de acesso salvo no perfil `profile` do XMB e espera o IP.
 * Para se *running virar 0. */
int net_connect_ap(int profile, char *ip_out, int ip_len, net_status_fn status, volatile int *running);

typedef struct {
    int strength;   /* sinal, % */
    int channel;
    int power_save; /* "Economia de energia WLAN" do XMB: 1 = ligada (mais latência) */
} net_ap_info_t;

void net_ap_info(net_ap_info_t *info);

/* 1 se ainda está conectado ao ponto de acesso (com IP). */
int net_ap_connected(void);

/* Abre a conexão TCP com o servidor. rcvbuf_kb = buffer de recepção (janela
 * TCP). Devolve o socket ou < 0. */
int net_connect_server(const char *host, int port, int rcvbuf_kb);

int net_send_all(int sock, const void *buf, int len);
int net_recv_all(int sock, void *buf, int len);

/* UDP: socket sem conexão; *dest recebe o endereço do servidor. */
struct sockaddr_in;
int net_open_udp(const char *host, int port, int rcvbuf_kb, struct sockaddr_in *dest);
int net_sendto(int sock, const struct sockaddr_in *dest, const void *buf, int len);
/* Espera dados por até timeout_us: 1 = há dados, 0 = tempo esgotado, < 0 = erro. */
int net_wait_readable(int sock, unsigned timeout_us);
/* Um datagrama sem esperar: devolve o tamanho, 0 se a fila está vazia, < 0 se erro. */
int net_recv_dgram(int sock, void *buf, int len);

/* Desbloqueia um recv() parado em outra thread. */
void net_abort(int sock);

void net_term(void);

#endif
