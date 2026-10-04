#include "net.h"

#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pspkernel.h>
#include <pspnet.h>
#include <pspnet_apctl.h>
#include <pspnet_inet.h>
#include <psputility.h>
#include <stdio.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>

#define AP_TIMEOUT_US (30 * 1000 * 1000)

int net_init(void)
{
    int err;
    if ((err = sceUtilityLoadNetModule(PSP_NET_MODULE_COMMON)) < 0)
        return err;
    if ((err = sceUtilityLoadNetModule(PSP_NET_MODULE_INET)) < 0)
        return err;
    /* Pool de 256 KB para a pilha (as amostras usam 128 KB); um pool maior
     * deixa a janela TCP crescer. A validar no hardware. */
    if ((err = sceNetInit(256 * 1024, 42, 4 * 1024, 42, 4 * 1024)) < 0)
        return err;
    if ((err = sceNetInetInit()) < 0)
        return err;
    if ((err = sceNetApctlInit(0x8000, 48)) < 0)
        return err;
    return 0;
}

static const char *state_name(int state)
{
    switch (state) {
    case PSP_NET_APCTL_STATE_DISCONNECTED: return "desconectado";
    case PSP_NET_APCTL_STATE_SCANNING: return "procurando a rede";
    case PSP_NET_APCTL_STATE_JOINING: return "associando";
    case PSP_NET_APCTL_STATE_GETTING_IP: return "obtendo IP";
    case PSP_NET_APCTL_STATE_GOT_IP: return "conectado";
    case PSP_NET_APCTL_STATE_EAP_AUTH: return "autenticando (EAP)";
    case PSP_NET_APCTL_STATE_KEY_EXCHANGE: return "trocando chaves";
    default: return "?";
    }
}

int net_connect_ap(int profile, char *ip_out, int ip_len, net_status_fn status, volatile int *running)
{
    int err = sceNetApctlConnect(profile);
    if (err < 0)
        return err;

    char msg[64];
    int last = -1, started = 0;
    unsigned int t0 = sceKernelGetSystemTimeLow();
    while (*running) {
        int state = 0;
        if ((err = sceNetApctlGetState(&state)) < 0)
            return err;
        if (state != last) {
            snprintf(msg, sizeof(msg), "  Wi-Fi: %s", state_name(state));
            status(msg);
        }
        if (state == PSP_NET_APCTL_STATE_GOT_IP)
            break;
        if (state != PSP_NET_APCTL_STATE_DISCONNECTED)
            started = 1;
        else if (started) /* passou de "conectando" para "desconectado" */
            return -1;
        if (sceKernelGetSystemTimeLow() - t0 > AP_TIMEOUT_US)
            return -2;
        last = state;
        sceKernelDelayThread(50 * 1000);
    }
    if (!*running)
        return -3;

    SceNetApctlInfo info;
    if (sceNetApctlGetInfo(PSP_NET_APCTL_INFO_IP, &info) == 0)
        snprintf(ip_out, ip_len, "%s", info.ip);
    else
        snprintf(ip_out, ip_len, "?");
    return 0;
}

void net_ap_info(net_ap_info_t *info)
{
    SceNetApctlInfo v;
    info->strength = sceNetApctlGetInfo(PSP_NET_APCTL_INFO_STRENGTH, &v) == 0 ? v.strength : -1;
    info->channel = sceNetApctlGetInfo(PSP_NET_APCTL_INFO_CHANNEL, &v) == 0 ? v.channel : -1;
    info->power_save = sceNetApctlGetInfo(PSP_NET_APCTL_INFO_POWER_SAVE, &v) == 0 ? v.powerSave : -1;
}

int net_ap_connected(void)
{
    int state = 0;
    return sceNetApctlGetState(&state) == 0 && state == PSP_NET_APCTL_STATE_GOT_IP;
}

int net_connect_server(const char *host, int port, int rcvbuf_kb)
{
    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0)
        return sock;

    /* Sem Nagle: o pedido de 28 bytes sai na hora. */
    int one = 1;
    setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    /* Buffer de recepção maior = janela TCP maior (o frame chega numa rajada). */
    int rcvbuf = rcvbuf_kb * 1024;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_len = sizeof(addr);
    addr.sin_family = AF_INET;
    addr.sin_port = htons(port);
    addr.sin_addr.s_addr = inet_addr(host);

    if (connect(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(sock);
        return -1;
    }
    return sock;
}

/* O PPSSPP não implementa socket bloqueante: devolve EAGAIN no lugar de
 * esperar. No PSP real um socket bloqueante não devolve EAGAIN, então tratar
 * como "tente de novo" é inofensivo lá. A pausa curta deixa as outras
 * threads rodarem. */
static int retry_later(void)
{
    if (errno != EAGAIN && errno != EWOULDBLOCK)
        return 0;
    sceKernelDelayThread(200);
    return 1;
}

int net_send_all(int sock, const void *buf, int len)
{
    const char *p = buf;
    while (len > 0) {
        int n = send(sock, p, len, 0);
        if (n < 0 && retry_later())
            continue;
        if (n <= 0)
            return -1;
        p += n;
        len -= n;
    }
    return 0;
}

int net_recv_all(int sock, void *buf, int len)
{
    char *p = buf;
    while (len > 0) {
        int n = recv(sock, p, len, 0);
        if (n < 0 && retry_later())
            continue;
        if (n <= 0)
            return -1;
        p += n;
        len -= n;
    }
    return 0;
}

int net_open_udp(const char *host, int port, int rcvbuf_kb, struct sockaddr_in *dest)
{
    int sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0)
        return sock;
    /* Um frame inteiro (até ~40 pedaços) pode chegar numa rajada. */
    int rcvbuf = rcvbuf_kb * 1024;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, &rcvbuf, sizeof(rcvbuf));

    memset(dest, 0, sizeof(*dest));
    dest->sin_len = sizeof(*dest);
    dest->sin_family = AF_INET;
    dest->sin_port = htons(port);
    dest->sin_addr.s_addr = inet_addr(host);
    return sock;
}

int net_sendto(int sock, const struct sockaddr_in *dest, const void *buf, int len)
{
    for (;;) {
        int n = sendto(sock, buf, len, 0, (const struct sockaddr *)dest, sizeof(*dest));
        if (n < 0 && retry_later())
            continue;
        return n == len ? 0 : -1;
    }
}

int net_wait_readable(int sock, unsigned timeout_us)
{
    fd_set rd;
    FD_ZERO(&rd);
    FD_SET(sock, &rd);
    struct timeval tv = {timeout_us / 1000000, timeout_us % 1000000};
    int r = select(sock + 1, &rd, NULL, NULL, &tv);
    if (r < 0 && (errno == EAGAIN || errno == EINTR))
        return 0;
    return r < 0 ? r : (r > 0 && FD_ISSET(sock, &rd));
}

int net_recv_dgram(int sock, void *buf, int len)
{
    /* Endereço de origem de verdade em vez de NULL: o PPSSPP devolve EFAULT
     * com NULL (e não custa nada no PSP). */
    struct sockaddr_in from;
    socklen_t fromlen = sizeof(from);
    int n = recvfrom(sock, buf, len, MSG_DONTWAIT, (struct sockaddr *)&from, &fromlen);
    if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
        return 0;
    return n;
}

void net_abort(int sock)
{
    if (sock >= 0)
        shutdown(sock, SHUT_RDWR);
}

void net_term(void)
{
    sceNetApctlDisconnect();
    sceNetApctlTerm();
    sceNetInetTerm();
    sceNetTerm();
    sceUtilityUnloadNetModule(PSP_NET_MODULE_INET);
    sceUtilityUnloadNetModule(PSP_NET_MODULE_COMMON);
}
