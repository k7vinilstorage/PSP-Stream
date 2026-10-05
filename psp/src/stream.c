/*
 * Thread de rede + troca de frames com a thread de decode.
 *
 * Slots de frame: recebendo (até 2 no UDP), pronto e decodificando. Se chega um
 * frame novo antes de o pronto ser pego, o pronto é descartado. O decode
 * sempre pega o mais novo.
 *
 * Com prefetch, o próximo pedido sai assim que um frame chega, antes do
 * decode: rede e decode trabalham ao mesmo tempo.
 *
 * Frames P (H.264 IPPP, o pacote começa com um AUD): cada frame é referência
 * do seguinte, então nenhum é descartado. Os prontos ficam numa fila, em
 * ordem, e o próximo só é pedido quando o decode pega o último da fila (quem
 * pede é a thread de decode): ele chega enquanto o atual decodifica, sem
 * fila crescendo. Frame perdido = pede IDR e pula os P até ele chegar.
 *
 * Transportes:
 *  - TCP: cabeçalho + JPEG num fluxo.
 *  - UDP: o JPEG chega em pedaços de 1400 bytes, remontados no slot. Os que
 *    faltam são pedidos de novo (NACK): na hora, se o último pedaço chegou e
 *    há buracos; ou depois de um silêncio maior que o normal entre pedaços,
 *    se o fim do frame se perdeu. Depois de um NACK, esperamos uma ida e volta
 *    inteira (medida) antes do próximo. Depois de MAX_NACKS desistimos do
 *    frame e pedimos outro. Pedido sem resposta é reenviado depois de uma
 *    ida e volta medida (rto_us, 30-200 ms; o servidor ignora duplicados).
 *
 * Cabeçalho JPEG (UDP): as tabelas no início de cada JPEG (~620 bytes) só
 * mudam com a qualidade. Guardamos as duas últimas e dizemos ao servidor qual
 * temos (hdr_have); ele manda só os dados comprimidos, e o cabeçalho é
 * copiado de volta no slot antes do primeiro pedaço.
 *
 * Ida e volta pura (UDP): no início, pings pequenos com a rede parada, metade
 * esperando com select() e metade consultando o socket a cada 0,5 ms. Mostra
 * a parte fixa da rede sem frame no meio e escolhe a espera mais rápida.
 *
 * Pedido antecipado (UDP): quando faltam `early` bytes do frame atual, já
 * pedimos o próximo. O pedido leva uma ida e volta para virar o 1º pedaço do
 * próximo frame, e nesse tempo o rádio entrega ida_e_volta x vazão bytes. Com
 * early = isso, o próximo começa a chegar logo depois do último pedaço do
 * atual: sem tempo morto entre frames e sem fila. É o padrão (auto): a ida e
 * volta é o ping do início, e a vazão vem do intervalo médio entre pedaços.
 * Valores fixos maiores (6-14 KB, testados no PSP-3000 com JPEG) pediam cedo
 * demais: o frame novo esperava na fila do roteador e a latência subia. Com
 * pedido antecipado podem existir dois frames sendo remontados; quando um
 * mais novo completa, o mais velho incompleto é abandonado.
 */
#include "stream.h"
#include "config.h"
#include "net.h"
#include "protocol.h"

#include <malloc.h>
#include <netinet/in.h>
#include <pspkernel.h>
#include <string.h>

#define NUM_SLOTS 5 /* 1 decodificando + até 2 prontos (frames P) + até 2 recebendo (UDP) */
/* Acima do decode (0x38) e das threads da pilha de rede (42/48): quando
 * chegam dados, o recv() roda na hora. */
#define NET_THREAD_PRIO 0x24

/* Fim do frame perdido: sem pedaço novo por média + 4 desvios do intervalo
 * entre pedaços, nunca menos de 20 ms. Um piso menor (6 ms, testado no
 * PSP-3000) confunde as pausas normais do Wi-Fi com perda e pede de novo
 * pedaços que ainda estão a caminho. */
#define GAP_MIN_US (20 * 1000)
#define GAP_MAX_US (50 * 1000)
/* Depois de um NACK: a resposta leva uma ida e volta (média + 4 desvios do
 * pedido -> primeiro pedaço). Esperar menos só gera NACK e reenvio repetidos. */
#define RTO_MIN_US (30 * 1000)
#define RTO_MAX_US (200 * 1000)
#define RTT_SAMPLE_MAX_US (150 * 1000) /* acima disso o servidor esperou frame novo: não é a rede */
#define MAX_NACKS 3
/* Pedido sem resposta: reenvia depois de rto_us(). Eram 200 ms fixos: no
 * PSP-3000 o 1º pedaço tinha mediana de ~8 ms e média de 20-40 ms, puxada por
 * ~1 frame em 10 que perdia o pedido ou a resposta inteira (frames H.264 de
 * 2 pedaços somem juntos numa rajada de interferência) e esperava os 200 ms. */
#define STALL_US (3000 * 1000)     /* nada completo por 3 s: recomeça (HELLO); > keepalive de 1 s do servidor */
/* Frames P: um pedido perdido na subida parava o stream até o RTO (>= 30 ms)
 * mais a resposta, porque nenhum frame pode ser pulado. Agora o pedido de
 * frame novo leva o número do frame que ele vai trazer (FRAME + NACK, como o
 * pedido repetido) e vai de novo depois disto se nada chegou. O servidor
 * reconhece a cópia: frame ainda não enviado = o pedido que já está esperando;
 * enviado há menos de 15 ms = já está no ar (transports.RETRY_GUARD_S). */
#define REQ_DUP_US (6 * 1000)
#define DEFER_POLL_US 1000 /* com o pedido adiado para a thread de decode: reavalia a cada 1 ms */

enum { SLOT_FREE, SLOT_RECV, SLOT_READY, SLOT_DECODING };

static ps_frame_t slots[NUM_SLOTS];
static int state[NUM_SLOTS];
/* Frames prontos, do mais velho ao mais novo. JPEG e H.264 só IDR: fica só o
 * mais novo. Frames P: todos, em ordem (cada um é referência do seguinte). */
static int ready_q[NUM_SLOTS], ready_n;
static ps_ack_t last_ack;
static unsigned dropped, lost, nacks, completed, retries, req_dups;

static SceUID lock_sema = -1, ready_sema = -1, want_sema = -1, send_sema = -1;
static SceUID net_thid = -1;
static int g_sock = -1;
static int g_udp;
static struct sockaddr_in g_dest;
static volatile int g_prefetch;
static int g_early; /* bytes que faltam no frame atual para pedir o próximo (0 = desligado, < 0 = auto) */
static volatile int early_cur; /* valor em uso (auto muda com a vazão) */
static volatile int *g_running;
static volatile int net_error;
static volatile int stopping;

static volatile int wifi_signal, wifi_flags;
static volatile int cap_h264 = 1, cap_h264p = 1;
/* Frames P (o pacote começa com AUD): nenhum frame pode ser pulado. Quando
 * um se perde, todo frame P a partir de need_idr_from fica sem referência
 * até chegar um IDR; o PSP pede (PS_REQ_IDR) e pula os P até lá. */
static volatile int pmode;
static volatile uint32_t need_idr_from; /* 0 = não precisa */
static uint32_t last_pub;               /* último frame publicado (buraco na numeração = perda) */
static unsigned idr_reqs;
/* Frames P: sem o descarte do JPEG, frame a mais na fila de prontos é
 * latência a mais (e com a fila cheia um frame se perde e vira IDR). Então o
 * próximo só é pedido quando o decode pega o último pronto: chega enquanto o
 * atual decodifica. Quem pede é a thread de decode (stream_take). */
static int ask_deferred;                  /* com o lock */
static volatile unsigned dec_asks, dec_ask_t; /* pedidos feitos pela thread de decode e o horário do último */
static volatile uint32_t dec_ask_frame;       /* ... e o frame que ele pediu (0 = pedido simples) */
static volatile uint32_t seen_max;            /* maior frame visto (completo, abandonado ou chegando) */
/* Estimativas (us), como o RTO do TCP: média móvel e desvio médio. */
static int gap_avg = 3000, gap_dev = 3000;   /* entre pedaços seguidos de um frame */
static int rtt_avg = 30000, rtt_dev = 10000; /* pedido -> primeiro pedaço */

/* Cabeçalhos JPEG guardados; hdrs[0] é o mais recente. Os ids são CRC32 do
 * conteúdo, então valem entre conexões e servidores. */
typedef struct {
    uint32_t id; /* 0 = vazio */
    int len;
    uint8_t data[PS_MAX_JPEG_HEADER];
} hdr_entry_t;
static hdr_entry_t hdrs[2];
static volatile uint32_t hdr_have;

#define POLL_US 500            /* espera por consulta: dorme isso entre tentativas */
#define PINGS 16               /* metade com select(), metade por consulta */
#define PING_TIMEOUT_US (300 * 1000)
static int g_rxwait;
static volatile int rx_poll;
static volatile unsigned ping_sel_us, ping_poll_us;
/* Ping durante o stream: separa o tempo do rádio/roteador sob o tráfego do
 * stream do tempo específico das respostas com frame (o "1º pedaço"). */
#define LIVE_PING_EVERY_US (1000 * 1000)
#define LIVE_PING_TAG 0x80000000u /* token = horário de envio com este bit (não colide com a fase inicial) */
static volatile unsigned live_avg_us, live_min_us;
static unsigned live_hist[8];
static int live_n;

static volatile uint32_t in_buttons;
static volatile uint8_t in_lx = 128, in_ly = 128;

static unsigned now_us(void)
{
    return sceKernelGetSystemTimeLow();
}

static void lock(void)
{
    sceKernelWaitSema(lock_sema, 1, NULL);
}

static void unlock(void)
{
    sceKernelSignalSema(lock_sema, 1);
}

static uint16_t clamp_u16(unsigned v)
{
    return v > 0xFFFF ? 0xFFFF : (uint16_t)v;
}

static int send_req(uint16_t flags, const ps_nack_t *nack)
{
    struct __attribute__((packed)) {
        ps_req_t r;
        ps_nack_t n;
    } msg;
    ps_req_t *r = &msg.r;
    memset(r, 0, sizeof(*r));
    r->magic = PS_MAGIC_REQ;
    r->buttons = in_buttons;
    r->lx = in_lx;
    r->ly = in_ly;
    r->flags = flags | (nack ? PS_REQ_NACK : 0) | (need_idr_from ? PS_REQ_IDR : 0);

    lock();
    ps_ack_t a = last_ack;
    unlock();
    if (a.frame_no) {
        r->ack_frame = a.frame_no;
        r->echo_ts = a.send_ts;
        r->net_t = a.net_t;
        r->local_t = a.local_t;
        r->decode_t = a.decode_t;
        r->since_t = clamp_u16((now_us() - a.t_shown) / 100);
        r->first_t = a.first_t;
        r->burst_t = a.burst_t;
        r->idle_t = a.idle_t;
    } else {
        r->idle_t = PS_IDLE_NONE;
    }
    r->early_b = clamp_u16(early_cur);
    r->signal = wifi_signal;
    r->wflags = wifi_flags | (rx_poll ? PS_WIFI_RX_POLL : 0) | (cap_h264 ? PS_CAP_H264 : 0) |
                (cap_h264 && cap_h264p ? PS_CAP_H264P : 0);
    r->lost = lost > 0xFFFF ? 0xFFFF : lost;
    r->hdr_have = hdr_have;
    r->ping_select = clamp_u16(ping_sel_us / 100);
    r->ping_poll = clamp_u16(ping_poll_us / 100);
    r->ping_live = clamp_u16(live_avg_us / 100);
    r->ping_live_min = clamp_u16(live_min_us / 100);
    int len = sizeof(ps_req_t);
    if (nack) {
        msg.n = *nack;
        len += sizeof(ps_nack_t);
    }

    sceKernelWaitSema(send_sema, 1, NULL);
    int rc = g_udp ? net_sendto(g_sock, &g_dest, &msg, len) : net_send_all(g_sock, &msg, len);
    sceKernelSignalSema(send_sema, 1);
    return rc;
}

/* Pede um frame novo. Frames P no UDP: com o número dele (o seguinte ao
 * maior já visto), para o pedido repetido ser reconhecido. *want = esse
 * número, ou 0 (pedido simples). */
static int send_new_req(uint32_t *want)
{
    *want = 0;
    if (!pmode || !g_udp) /* o TCP lê só o pedido, sem NACK */
        return send_req(PS_REQ_FRAME, NULL);
    ps_nack_t nk;
    memset(&nk, 0xFF, sizeof(nk));
    nk.frame_no = seen_max + 1;
    *want = nk.frame_no;
    return send_req(PS_REQ_FRAME, &nk);
}

static void wait_want(void)
{
    /* Sem prefetch: espera o decode terminar antes de pedir o próximo. */
    while (*g_running && !stopping && !g_prefetch) {
        SceUInt timeout = 100 * 1000;
        if (sceKernelWaitSema(want_sema, 1, &timeout) == 0)
            return;
    }
}

/* Chamar com o lock: a thread de rede marca perdas e a de decode marca o IDR
 * que chegou; sem o lock, uma limparia o que a outra acabou de marcar. */
static void need_idr(uint32_t from)
{
    if (!need_idr_from)
        idr_reqs++;
    if (from > need_idr_from)
        need_idr_from = from;
}

static void need_idr_locked(uint32_t from)
{
    lock();
    need_idr(from);
    unlock();
}

static int is_aud(const uint8_t *p, int len)
{
    return len >= 5 && p[0] == 0 && p[1] == 0 && p[2] == 0 && p[3] == 1 && (p[4] & 0x1F) == 9;
}

/* Slot livre para receber (sempre há um: no máx. 2 prontos + 1 decodificando).
 * -1 só se algum slot vazou (bug): o chamador descarta o frame. */
static int claim_slot(void)
{
    lock();
    int idx = 0;
    while (idx < NUM_SLOTS && state[idx] != SLOT_FREE)
        idx++;
    if (idx == NUM_SLOTS && ready_n > 0) { /* não deveria acontecer: tira o pronto mais velho */
        idx = ready_q[0];
        ready_n--;
        memmove(ready_q, ready_q + 1, ready_n * sizeof(ready_q[0]));
        dropped++;
        if (pmode)
            need_idr(slots[idx].frame_no + 1);
    } else if (idx == NUM_SLOTS) { /* nem pronto para tirar: um slot vazou (bug) */
        dropped++;
        unlock();
        return -1;
    }
    state[idx] = SLOT_RECV;
    unlock();
    return idx;
}

static void free_slot(int idx)
{
    lock();
    state[idx] = SLOT_FREE;
    unlock();
}

/* Frame completo vai para a fila de prontos. JPEG e H.264 só IDR: o anterior
 * ainda não decodificado é descartado (só o mais novo interessa). Frames P:
 * entra no fim da fila; um buraco na numeração é um frame perdido. */
static void publish_slot(int idx)
{
    uint32_t no = slots[idx].frame_no;
    lock();
    if (pmode && last_pub && no != last_pub + 1)
        need_idr(no);
    last_pub = no;
    int was_empty = ready_n == 0;
    if (!pmode) {
        for (int i = 0; i < ready_n; i++) {
            state[ready_q[i]] = SLOT_FREE;
            dropped++;
        }
        ready_n = 0;
    }
    state[idx] = SLOT_READY;
    ready_q[ready_n++] = idx;
    completed++;
    unlock();
    if (was_empty)
        sceKernelSignalSema(ready_sema, 1);
}

/* Frames P com frame pronto na fila: o pedido do próximo fica para quando o
 * decode pegar o último (1). Senão o chamador pede já (0). */
static int defer_ask(void)
{
    lock();
    int defer = pmode && ready_n > 0;
    if (defer)
        ask_deferred = 1;
    unlock();
    return defer;
}

static int net_thread_tcp(void)
{
    unsigned t_req = now_us();
    int deferred = 0;
    if (send_req(PS_REQ_HELLO | PS_REQ_FRAME, NULL) < 0)
        return -1;

    while (*g_running && !stopping) {
        int idx = claim_slot();
        if (idx < 0)
            return -3; /* bug: sem slot; a conexão recomeça */
        ps_frame_hdr_t hdr;
        if (net_recv_all(g_sock, &hdr, sizeof(hdr)) < 0)
            return -1;
        unsigned t_first = now_us();
        if (deferred) /* o pedido foi da thread de decode */
            t_req = dec_ask_t;
        if (hdr.magic != PS_MAGIC_FRAME || hdr.size == 0 || hdr.size > PS_MAX_JPEG)
            return -2;
        ps_frame_t *f = &slots[idx];
        if (net_recv_all(g_sock, f->data, hdr.size) < 0)
            return -1;
        f->size = hdr.size;
        f->frame_no = hdr.frame_no;
        f->send_ts = hdr.send_ts;
        f->t_req = t_req;
        f->t_first = t_first;
        f->t_recv = now_us();
        f->idle_t = PS_IDLE_NONE;
        pmode = is_aud(f->data, f->size);
        publish_slot(idx);

        if (!g_prefetch)
            wait_want();
        else if ((deferred = defer_ask()))
            continue;
        t_req = now_us();
        if (send_req(PS_REQ_FRAME, NULL) < 0)
            return -1;
    }
    return 0;
}

/* Um frame sendo remontado (UDP). */
typedef struct {
    int idx;           /* slot; -1 = vazio */
    uint32_t frame_no;
    int count, got, nacks;
    int hi;            /* maior pedaço recebido + 1: got < hi = há buraco (chegam em ordem) */
    int base;          /* bytes de cabeçalho copiados do cache antes do payload */
    uint32_t hdr;      /* campo hdr do pedaço */
    int nack_last;     /* maior pedaço pedido no último NACK (o último do reenvio) */
    int held;          /* frames P: completo, esperando o mais velho chegar (reenvio) */
    int asked_next;    /* já pediu o próximo antecipado */
    unsigned last_rx;  /* último pedaço recebido */
    unsigned t_nack;   /* último NACK */
    unsigned deadline; /* sem pedaço novo até aqui: NACK ou desistência */
    unsigned t_req;    /* quando saiu o pedido que gerou este frame */
    unsigned t_first;  /* primeiro pedaço */
    uint32_t have[PS_MAX_CHUNKS / 32];
} asm_t;

static void asm_drop(asm_t *a)
{
    if (a->idx >= 0) {
        free_slot(a->idx);
        a->idx = -1;
    }
}

static int asm_missing(const asm_t *a, int i)
{
    return !(a->have[i / 32] >> (i % 32) & 1);
}

static void est_update(int *avg, int *dev, int sample)
{
    int err = sample - *avg;
    *avg += err / 8;
    *dev += ((err < 0 ? -err : err) - *dev) / 4;
}

static int clampi(int v, int lo, int hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

static int gap_timeout_us(void)
{
    return clampi(gap_avg + 4 * gap_dev, GAP_MIN_US, GAP_MAX_US);
}

static int rto_us(void)
{
    return clampi(rtt_avg + 4 * rtt_dev, RTO_MIN_US, RTO_MAX_US);
}

static int send_nack(asm_t *a)
{
    ps_nack_t nk;
    memset(&nk, 0, sizeof(nk));
    nk.frame_no = a->frame_no;
    for (int i = 0; i < a->count; i++)
        if (asm_missing(a, i)) {
            nk.missing[i / 32] |= 1u << (i % 32);
            a->nack_last = i;
        }
    a->nacks++;
    nacks++;
    a->t_nack = now_us();
    a->deadline = a->t_nack + rto_us();
    return send_req(0, &nk);
}

/* Bytes do início do JPEG até o fim do SOS (mesma regra do servidor). 0 se não achar. */
static int jpeg_header_len(const uint8_t *p, int n)
{
    if (n < 4 || p[0] != 0xFF || p[1] != 0xD8)
        return 0;
    int i = 2;
    while (i + 4 <= n) {
        if (p[i] != 0xFF)
            return 0;
        int m = p[i + 1];
        if (m == 0xFF) {
            i++;
            continue;
        }
        if (m == 0x01 || (m >= 0xD0 && m <= 0xD7)) {
            i += 2;
            continue;
        }
        int end = i + 2 + (p[i + 2] << 8 | p[i + 3]);
        if (m == 0xDA)
            return end <= PS_MAX_JPEG_HEADER && end < n ? end : 0;
        i = end;
    }
    return 0;
}

/* CRC-32 (o mesmo do zlib.crc32 do servidor); só roda quando o cabeçalho muda. */
static uint32_t crc32_of(const uint8_t *p, int n)
{
    uint32_t c = 0xFFFFFFFFu;
    while (n-- > 0) {
        c ^= *p++;
        for (int k = 0; k < 8; k++)
            c = c >> 1 ^ (0xEDB88320u & -(c & 1));
    }
    return ~c;
}

static const hdr_entry_t *hdr_find(uint32_t id)
{
    for (int k = 0; k < 2; k++)
        if (hdrs[k].id == id && hdrs[k].len > 0)
            return &hdrs[k];
    return NULL;
}

/* Frame completo que veio com cabeçalho: guarda para os próximos. */
static void hdr_learn(uint32_t id, const uint8_t *jpeg, int size)
{
    if (!id || hdrs[0].id == id)
        return;
    if (hdrs[1].id == id) { /* voltou para a qualidade anterior */
        hdr_entry_t t = hdrs[0];
        hdrs[0] = hdrs[1];
        hdrs[1] = t;
    } else {
        int len = jpeg_header_len(jpeg, size);
        uint32_t crc = len ? crc32_of(jpeg, len) & PS_HDR_ID_MASK : 0;
        if (!len || (crc ? crc : 1) != id)
            return; /* cortaríamos o cabeçalho num lugar diferente do servidor: não guarda */
        hdrs[1] = hdrs[0];
        hdrs[0].id = id;
        hdrs[0].len = len;
        memcpy(hdrs[0].data, jpeg, len);
    }
    hdr_have = id;
}

static int wait_rx(int timeout_us)
{
    if (rx_poll) {
        sceKernelDelayThread(timeout_us < POLL_US ? timeout_us : POLL_US);
        return 0;
    }
    return net_wait_readable(g_sock, timeout_us) < 0 ? -1 : 0;
}

/* Um ping: 1 = respondido (rtt em us), 0 = sem resposta, -1 = erro de rede. */
static int send_ping(uint32_t token)
{
    ps_req_t r;
    memset(&r, 0, sizeof(r));
    r.magic = PS_MAGIC_REQ;
    r.buttons = in_buttons;
    r.lx = in_lx;
    r.ly = in_ly;
    r.flags = PS_REQ_PING;
    r.echo_ts = token;
    sceKernelWaitSema(send_sema, 1, NULL);
    int rc = net_sendto(g_sock, &g_dest, &r, sizeof(r));
    sceKernelSignalSema(send_sema, 1);
    return rc;
}

static int ping_once(int poll, uint32_t token, unsigned *rtt)
{
    static uint8_t buf[sizeof(ps_chunk_hdr_t) + PS_CHUNK_PAYLOAD + 64];
    unsigned t0 = now_us();
    if (send_ping(token) < 0)
        return -1;
    for (;;) {
        int n = net_recv_dgram(g_sock, buf, sizeof(buf));
        if (n < 0)
            return -1;
        if (n == sizeof(ps_pong_t)) {
            ps_pong_t pong;
            memcpy(&pong, buf, sizeof(pong));
            if (pong.magic == PS_MAGIC_PONG && pong.token == token) {
                *rtt = now_us() - t0;
                return 1;
            }
        }
        if (n > 0)
            continue; /* pedaço velho de uma conexão anterior */
        int left = PING_TIMEOUT_US - (int)(now_us() - t0);
        if (left <= 0 || !*g_running || stopping)
            return 0;
        if (poll)
            sceKernelDelayThread(left < POLL_US ? left : POLL_US);
        else if (net_wait_readable(g_sock, left) < 0)
            return -1;
    }
}

static unsigned median(unsigned *v, int n)
{
    for (int i = 1; i < n; i++) /* n <= 8: inserção */
        for (int j = i; j > 0 && v[j - 1] > v[j]; j--) {
            unsigned t = v[j];
            v[j] = v[j - 1];
            v[j - 1] = t;
        }
    return n ? v[n / 2] : 0;
}

/* Mede a ida e volta pura com select() e por consulta, e escolhe a espera. */
static int ping_phase(void)
{
    unsigned rtt[2][PINGS / 2];
    int got[2] = {0, 0}, misses = 0;
    for (int i = 0; i < PINGS && misses < 3 && *g_running && !stopping; i++) {
        int poll = i & 1;
        unsigned t;
        int r = ping_once(poll, now_us() ^ (uint32_t)i << 28, &t);
        if (r < 0)
            return -1;
        if (r == 0) {
            misses++; /* servidor antigo ou ping perdido */
            continue;
        }
        misses = 0;
        rtt[poll][got[poll]++] = t;
        sceKernelDelayThread(5 * 1000);
    }
    ping_sel_us = median(rtt[0], got[0]);
    ping_poll_us = median(rtt[1], got[1]);
    if (g_rxwait == RXWAIT_AUTO)
        /* a consulta acorda a CPU 2000x/s: só vale se select() demorar 1 ms a mais */
        rx_poll = got[0] && got[1] && ping_poll_us + 1000 < ping_sel_us;
    else
        rx_poll = g_rxwait == RXWAIT_POLL;
    return 0;
}

static void live_pong(const uint8_t *buf)
{
    ps_pong_t pong;
    memcpy(&pong, buf, sizeof(pong));
    if (pong.magic != PS_MAGIC_PONG || !(pong.token & LIVE_PING_TAG))
        return;
    unsigned rtt = (now_us() - pong.token) & ~LIVE_PING_TAG;
    if (rtt > 2000 * 1000)
        return; /* pong de outra conexão */
    live_avg_us = live_avg_us ? (live_avg_us * 3 + rtt) / 4 : rtt;
    live_hist[live_n++ % 8] = rtt;
    unsigned m = ~0u;
    for (int i = 0; i < 8 && i < live_n; i++)
        if (live_hist[i] < m)
            m = live_hist[i];
    live_min_us = m;
}

/* Pedido antecipado automático: ida e volta x vazão = ping / intervalo entre
 * pedaços x tamanho do pedaço. O ping é a mediana do início (rede parada, no
 * modo de espera em uso): sob carga a ida e volta é maior, mas pedir um pouco
 * tarde só deixa sobrar tempo morto, enquanto pedir cedo cria fila. */
#define EARLY_AUTO_MAX (8 * 1024) /* no PSP-3000 a conta dá ~2-4 KB; 6-14 KB fixos criavam fila */
#define EARLY_PING_DEFAULT_US 6000 /* sem ping medido (todos perdidos) */
static int early_threshold(void)
{
    int e = g_early;
    if (e < 0) {
        unsigned ping = rx_poll ? ping_poll_us : ping_sel_us;
        if (!ping)
            ping = EARLY_PING_DEFAULT_US;
        int gap = gap_avg > 200 ? gap_avg : 200;
        e = clampi((int)((unsigned long long)ping * PS_CHUNK_PAYLOAD / (unsigned)gap), 0, EARLY_AUTO_MAX);
    }
    early_cur = e;
    return e;
}

static int net_thread_udp(void)
{
    static uint8_t pkt[sizeof(ps_chunk_hdr_t) + PS_CHUNK_PAYLOAD + 64];
    asm_t as[2];                  /* as[0] mais velho, as[1] mais novo */
    as[0].idx = as[1].idx = -1;
    uint32_t done = 0;            /* frames <= done: completos ou abandonados */
    unsigned req_q[2];            /* horários dos pedidos ainda sem resposta */
    int pending = 0;
    unsigned now = now_us(), last_req = now, last_done = now, link_free = now;

    uint32_t dup_frame = 0;       /* frames P: pedido a repetir em dup_at se nada chegar (0 = nenhum) */
    unsigned dup_at = 0;

#define ASK(flags)                                                                                                    \
    do {                                                                                                              \
        if (pending < 2)                                                                                              \
            req_q[pending++] = now_us();                                                                              \
        last_req = now_us();                                                                                          \
        if ((flags) == PS_REQ_FRAME) {                                                                                \
            uint32_t want_;                                                                                           \
            if (send_new_req(&want_) < 0)                                                                             \
                return -1;                                                                                            \
            if (want_) {                                                                                              \
                dup_frame = want_;                                                                                    \
                dup_at = last_req + REQ_DUP_US;                                                                       \
            }                                                                                                         \
        } else if (send_req((flags), NULL) < 0)                                                                       \
            return -1;                                                                                                \
    } while (0)

    if (ping_phase() < 0)
        return -1;
    now = last_req = last_done = link_free = now_us();
    unsigned next_ping = now + LIVE_PING_EVERY_US;
    unsigned dec_seen = dec_asks;
    ASK(PS_REQ_HELLO | PS_REQ_FRAME);

    while (*g_running && !stopping) {
        if (dec_asks != dec_seen) { /* frames P: a thread de decode pediu o próximo */
            dec_seen = dec_asks;
            if (pending < 2)
                req_q[pending++] = dec_ask_t;
            last_req = dec_ask_t;
            if (dec_ask_frame) {
                dup_frame = dec_ask_frame;
                dup_at = dec_ask_t + REQ_DUP_US;
            }
        }
        now = now_us();
        if ((int)(now - next_ping) >= 0) {
            next_ping = now + LIVE_PING_EVERY_US;
            if (send_ping(now | LIVE_PING_TAG) < 0)
                return -1;
        }
        int timeout = 100 * 1000; /* teto: reavalia pelo menos a cada 100 ms */
        int acted = 0;

        /* ---- frames P: o pedido de frame novo vai de novo se nada chegou ---- */
        if (dup_frame) {
            int left = (int)(dup_at - now);
            if (left <= 0) {
                ps_nack_t nk;
                memset(&nk, 0xFF, sizeof(nk));
                nk.frame_no = dup_frame;
                dup_frame = 0;
                req_dups++;
                if (send_req(PS_REQ_FRAME, &nk) < 0)
                    return -1;
            } else if (left < timeout) {
                timeout = left;
            }
        }

        /* ---- frames incompletos sem pedaço novo: NACK ou desistência ---- */
        for (int k = 0; k < 2; k++) {
            asm_t *a = &as[k];
            if (a->idx < 0 || a->held)
                continue;
            int left = (int)(a->deadline - now);
            if (left > 0) {
                if (left < timeout)
                    timeout = left;
                continue;
            }
            acted = 1;
            /* Um frame mais novo já está chegando: o reenvio ficaria na fila
             * atrás dele e chegaria depois que ele completasse, quando este já
             * teria sido descartado. Descarta já e não gasta o ar. */
            /* Frames P: o mais novo depende deste, então sempre pede o reenvio. */
            int newer = !pmode && k == 0 && as[1].idx >= 0;
            if (a->nacks < MAX_NACKS && !newer) {
                if (send_nack(a) < 0)
                    return -1;
            } else { /* desiste do frame */
                if (a->frame_no > done)
                    done = a->frame_no;
                if (pmode) { /* sem ele, os P seguintes não têm referência */
                    need_idr_locked(a->frame_no + 1);
                    asm_t *o = &as[1 - k];
                    if (o->idx >= 0 && o->held) {
                        if (o->frame_no > done)
                            done = o->frame_no;
                        asm_drop(o);
                        o->held = 0;
                        lost++;
                    }
                }
                asm_drop(a);
                lost++;
            }
        }

        /* ---- nada chegando ---- */
        if (as[0].idx < 0 && as[1].idx < 0) {
            if (pending == 0 && !ask_deferred) { /* ninguém pediu o próximo (ex.: desistiu de um frame) */
                ASK(PS_REQ_FRAME);
                continue;
            }
            /* Com o pedido adiado, ninguém pediu ainda: a thread de decode
             * pede quando pegar o frame pronto. Repetir agora era um pedido a
             * mais, e contava como perda no overlay ("repet"). Só volta logo
             * para ver o pedido dela (dec_asks) e marcar a repetição a tempo:
             * sem pacote chegando, o select() dormiria até o teto. */
            if (ask_deferred) {
                if (DEFER_POLL_US < timeout)
                    timeout = DEFER_POLL_US;
            } else {
                int left = rto_us() - (int)(now_us() - last_req);
                if (left <= 0) {
                    /* o pedido (ou a resposta) se perdeu, ou a tela está parada */
                    int stalled = now_us() - last_done > STALL_US;
                    last_req = now_us();
                    retries++;
                    if (pmode && done && !stalled) {
                        /* Frames P: um frame pequeno vem num pacote só e, se ele
                         * some, nada chega. O pedido repetido leva um NACK do
                         * frame esperado: se o servidor já o mandou, reenvia o
                         * mesmo (a corrente continua, sem IDR); senão o pedido
                         * é que se perdeu e vale como pedido normal. */
                        ps_nack_t nk;
                        memset(&nk, 0xFF, sizeof(nk));
                        nk.frame_no = done + 1;
                        if (send_req(PS_REQ_FRAME, &nk) < 0)
                            return -1;
                    } else if (send_req(PS_REQ_FRAME | (stalled ? PS_REQ_HELLO : 0), NULL) < 0)
                        return -1;
                    continue;
                }
                if (left < timeout)
                    timeout = left;
            }
        }
        if (acted)
            continue;

        /* Lê o que já chegou sem esperar; select() só com a fila vazia. */
        int n = net_recv_dgram(g_sock, pkt, sizeof(pkt));
        if (n < 0)
            return -1;
        if (n == 0) {
            if (wait_rx(timeout) < 0)
                return -1;
            continue;
        }
        if (n == (int)sizeof(ps_pong_t)) {
            live_pong(pkt);
            continue;
        }
        if (n < (int)sizeof(ps_chunk_hdr_t))
            continue;

        ps_chunk_hdr_t h;
        memcpy(&h, pkt, sizeof(h));
        int plen = n - (int)sizeof(h);
        if (h.magic != PS_MAGIC_CHUNK || h.size == 0 || h.size > PS_MAX_JPEG || h.count > PS_MAX_CHUNKS ||
            h.count != (h.size + PS_CHUNK_PAYLOAD - 1) / PS_CHUNK_PAYLOAD || h.chunk >= h.count)
            continue;
        int expect = h.chunk == h.count - 1 ? (int)(h.size - h.chunk * PS_CHUNK_PAYLOAD) : PS_CHUNK_PAYLOAD;
        if (plen != expect)
            continue;

        /* Depois de STALL_US parado, aceita numeração menor: o servidor reiniciou. */
        if (h.frame_no <= done && now_us() - last_done > STALL_US) {
            asm_drop(&as[0]);
            asm_drop(&as[1]);
            as[0].held = as[1].held = 0;
            done = 0;
            last_pub = 0;
            seen_max = 0;
        }
        if (h.frame_no <= done)
            continue; /* atrasado ou duplicado */
        if (h.frame_no > seen_max)
            seen_max = h.frame_no;
        if (dup_frame && h.frame_no >= dup_frame)
            dup_frame = 0; /* a resposta já está chegando */

        asm_t *a = NULL;
        for (int k = 0; k < 2; k++)
            if (as[k].idx >= 0 && as[k].frame_no == h.frame_no)
                a = &as[k];
        if (!a) { /* frame novo (o servidor manda em ordem crescente): sempre em as[1] */
            if (as[0].idx >= 0 && as[1].idx >= 0) { /* dois em andamento: o mais velho sai */
                if (as[0].frame_no > done)
                    done = as[0].frame_no;
                asm_drop(&as[0]);
                lost++;
                if (pmode) { /* frames P: o que esperava por ele também não serve */
                    need_idr_locked(as[0].frame_no + 1);
                    if (as[1].held) {
                        if (as[1].frame_no > done)
                            done = as[1].frame_no;
                        asm_drop(&as[1]);
                        as[1].held = 0;
                        lost++;
                    }
                }
            }
            if (as[1].idx >= 0) {
                as[0] = as[1];
                as[1].idx = -1;
            }
            const hdr_entry_t *cached = NULL;
            if (h.hdr & PS_HDR_STRIPPED) {
                cached = hdr_find(h.hdr & PS_HDR_ID_MASK);
                if (!cached || cached->len + h.size > PS_MAX_JPEG) {
                    /* sem o cabeçalho (não deveria acontecer): pula o frame; o
                     * próximo pedido diz qual temos, e ele vem inteiro */
                    if (h.frame_no > done)
                        done = h.frame_no;
                    if (pending > 0) {
                        req_q[0] = req_q[1];
                        pending--;
                    }
                    continue;
                }
            }
            a = &as[1];
            memset(a, 0, sizeof(*a));
            a->idx = claim_slot();
            if (a->idx < 0) { /* bug: sem slot; descarta o frame */
                if (h.frame_no > done)
                    done = h.frame_no;
                if (pmode)
                    need_idr_locked(h.frame_no + 1);
                lost++;
                continue;
            }
            a->frame_no = h.frame_no;
            a->count = h.count;
            a->hdr = h.hdr;
            if (cached) {
                a->base = cached->len;
                memcpy(slots[a->idx].data, cached->data, cached->len);
            }
            a->last_rx = a->t_first = now_us();
            if (pending > 0) { /* responde ao pedido mais antigo */
                a->t_req = req_q[0];
                req_q[0] = req_q[1];
                pending--;
                int rtt = (int)(a->t_first - a->t_req);
                if (rtt < RTT_SAMPLE_MAX_US)
                    est_update(&rtt_avg, &rtt_dev, rtt);
            } else {
                a->t_req = now_us();
            }
            slots[a->idx].size = a->base + h.size;
            slots[a->idx].frame_no = h.frame_no;
            slots[a->idx].send_ts = h.send_ts;
        }

        if (a->held)
            continue; /* completo, esperando o mais velho: pedaço repetido */
        unsigned t_rx = now_us();
        if (h.chunk == 0) /* o pacote de frames P começa com um AUD */
            pmode = is_aud(pkt + sizeof(h), plen);
        if (asm_missing(a, h.chunk)) {
            if (a->got > 0 && a->nacks == 0) { /* intervalo entre pedaços seguidos do mesmo frame */
                unsigned dt = t_rx - a->last_rx;
                if (dt < 100 * 1000)
                    est_update(&gap_avg, &gap_dev, (int)dt);
            }
            a->have[h.chunk / 32] |= 1u << (h.chunk % 32);
            memcpy(slots[a->idx].data + a->base + h.chunk * PS_CHUNK_PAYLOAD, pkt + sizeof(h), plen);
            a->got++;
            if (h.chunk >= a->hi)
                a->hi = h.chunk + 1;
        }
        a->last_rx = t_rx;

        if (a->got == a->count) {
            ps_frame_t *f = &slots[a->idx];
            unsigned t = now_us();
            /* "rede" conta a partir de quando o rádio ficou livre para este frame */
            f->t_req = (int)(a->t_req - link_free) > 0 ? a->t_req : link_free;
            f->t_first = a->t_first;
            f->t_recv = t;
            f->idle_t = clampi((int)(a->t_first - link_free) / 100, -0x7FFF, 0x7FFF);
            link_free = last_done = t;
            int asked = a->asked_next;
            if (!(a->hdr & PS_HDR_STRIPPED))
                hdr_learn(a->hdr & PS_HDR_ID_MASK, f->data, f->size);
            asm_t *other = (a == &as[1]) ? &as[0] : &as[1];
            if (pmode && other->idx >= 0 && !other->held && other->frame_no < f->frame_no) {
                /* Frames P: este depende do mais velho, que ainda pode chegar
                 * pelo reenvio. Espera a vez com o slot guardado. */
                a->held = 1;
            } else {
                if (a->frame_no > done)
                    done = a->frame_no;
                publish_slot(a->idx);
                a->idx = -1;
                if (other->idx >= 0 && other->held && other->frame_no > f->frame_no) {
                    /* o mais novo já estava completo esperando este; o pedido
                     * do seguinte fica por conta dele (ele não pediu antes) */
                    if (other->frame_no > done)
                        done = other->frame_no;
                    publish_slot(other->idx);
                    asked = other->asked_next;
                    other->idx = -1;
                    other->held = 0;
                } else if (other->idx >= 0 && other->frame_no < f->frame_no) {
                    /* JPEG / só IDR: um mais velho ainda incompleto perdeu a vez */
                    asm_drop(other);
                    lost++;
                }
            }
            if (a->held) {
                /* frames P: o próximo só é pedido quando o mais velho se
                 * resolver, senão um terceiro frame chegaria sem espaço */
            } else if (!g_prefetch) {
                wait_want();
                ASK(PS_REQ_FRAME);
            } else if (!asked && pending == 0 && !defer_ask()) {
                ASK(PS_REQ_FRAME);
            }
        } else if (a->nacks == 0 ? h.chunk == a->count - 1
                                 : h.chunk >= a->nack_last && (int)(t_rx - a->t_nack) > rtt_avg / 2) {
            /* O último pedaço do envio (ou do reenvio) chegou e ainda há
             * buracos. Os pedaços vêm em ordem, então os que faltam se
             * perderam: age já, sem esperar o silêncio. Um pedaço que chega
             * antes de meia ida e volta não é resposta ao NACK: ignora. */
            a->deadline = t_rx;
        } else { /* ainda chegando: adia (sem antecipar a espera de um NACK) */
            unsigned d = t_rx + gap_timeout_us();
            if ((a->got == 1 && a->nacks == 0) || (int)(d - a->deadline) > 0)
                a->deadline = d;
        }
        /* Com buraco no frame, não antecipa: o próximo entraria na fila na
         * frente do reenvio, e este frame seria descartado. Frames P: nem com
         * buraco num mais velho (o seguinte depende dele), nem com frame
         * esperando o decode (quem pede é a thread de decode). */
        asm_t *older = (a == &as[1]) ? &as[0] : &as[1];
        int blocked = pmode && ((older->idx >= 0 && older->frame_no < a->frame_no) || ready_n > 0);
        if (a->idx >= 0 && a->got < a->count && a->got == a->hi && !blocked && g_prefetch && g_early &&
            !a->asked_next && pending == 0 &&
            (int)slots[a->idx].size - a->base - a->got * PS_CHUNK_PAYLOAD <= early_threshold()) {
            a->asked_next = 1; /* pedido antecipado */
            ASK(PS_REQ_FRAME);
        }
    }
    return 0;
#undef ASK
}

static int net_thread(SceSize args, void *argp)
{
    int r = g_udp ? net_thread_udp() : net_thread_tcp();
    if (r < 0 && !net_error)
        net_error = r;
    sceKernelSignalSema(ready_sema, 1); /* acorda a thread de decode */
    return 0;
}

int stream_start(int sock, int udp, const struct sockaddr_in *dest, int prefetch, int early_bytes, int rxwait,
                 volatile int *running)
{
    g_sock = sock;
    g_udp = udp;
    g_early = early_bytes;
    early_cur = early_bytes > 0 ? early_bytes : 0;
    g_rxwait = rxwait;
    rx_poll = 0;
    ping_sel_us = ping_poll_us = 0;
    live_avg_us = live_min_us = 0;
    live_n = 0;
    if (udp)
        g_dest = *dest;
    g_prefetch = prefetch;
    g_running = running;
    net_error = 0;
    stopping = 0;
    dropped = lost = nacks = completed = retries = req_dups = idr_reqs = 0;
    ready_n = 0;
    pmode = 0;
    last_pub = 0;
    need_idr_from = 0;
    ask_deferred = 0;
    seen_max = 0;
    dec_ask_frame = 0;
    memset(&last_ack, 0, sizeof(last_ack));
    for (int i = 0; i < NUM_SLOTS; i++) {
        if (!slots[i].data && !(slots[i].data = memalign(64, PS_MAX_JPEG)))
            return -1;
        state[i] = SLOT_FREE;
    }
    lock_sema = sceKernelCreateSema("ps_lock", 0, 1, 1, NULL);
    ready_sema = sceKernelCreateSema("ps_ready", 0, 0, 1, NULL);
    want_sema = sceKernelCreateSema("ps_want", 0, 0, 1, NULL);
    send_sema = sceKernelCreateSema("ps_send", 0, 1, 1, NULL);
    net_thid = sceKernelCreateThread("ps_net", net_thread, NET_THREAD_PRIO, 32 * 1024, PSP_THREAD_ATTR_USER, NULL);
    if (net_thid < 0)
        return net_thid;
    return sceKernelStartThread(net_thid, 0, NULL);
}

ps_frame_t *stream_take(unsigned timeout_us)
{
    SceUInt timeout = timeout_us;
    if (sceKernelWaitSema(ready_sema, 1, &timeout) < 0)
        return NULL;
    lock();
    int idx = -1;
    if (ready_n > 0) {
        idx = ready_q[0];
        ready_n--;
        memmove(ready_q, ready_q + 1, ready_n * sizeof(ready_q[0]));
        state[idx] = SLOT_DECODING;
    }
    int more = ready_n > 0;
    int ask = ask_deferred && !more;
    if (ask)
        ask_deferred = 0;
    unlock();
    if (more) /* o semáforo é binário: avisa de novo que ainda há prontos */
        sceKernelSignalSema(ready_sema, 1);
    if (ask && !net_error && !stopping) { /* frames P: o próximo chega enquanto este decodifica */
        uint32_t want;
        dec_ask_t = now_us();
        send_new_req(&want);
        dec_ask_frame = want;
        dec_asks++;
    }
    return idx >= 0 ? &slots[idx] : NULL;
}

void stream_release(ps_frame_t *frame, const ps_ack_t *ack)
{
    lock();
    state[frame - slots] = SLOT_FREE;
    if (ack)
        last_ack = *ack;
    unlock();
    if (!g_prefetch)
        sceKernelSignalSema(want_sema, 1);
}

void stream_ping(unsigned *select_us, unsigned *poll_us, int *polling, unsigned *live_us, unsigned *live_min_us_out)
{
    *select_us = ping_sel_us;
    *poll_us = ping_poll_us;
    *polling = rx_poll;
    *live_us = live_avg_us;
    *live_min_us_out = live_min_us;
}

void stream_set_h264(int on)
{
    cap_h264 = on;
}

void stream_set_h264p(int on)
{
    cap_h264p = on;
}

int stream_frame_needs_idr(uint32_t frame_no)
{
    uint32_t from = need_idr_from;
    return from && frame_no >= from;
}

void stream_request_idr(uint32_t from)
{
    need_idr_locked(from);
}

void stream_idr_done(uint32_t frame_no)
{
    lock();
    if (need_idr_from && frame_no >= need_idr_from)
        need_idr_from = 0;
    unlock();
}

unsigned stream_idr_requests(void)
{
    return idr_reqs;
}

void stream_set_wifi(int signal, int flags)
{
    wifi_signal = signal < 0 ? 0 : signal > 100 ? 100 : signal;
    wifi_flags = flags;
}

void stream_set_input(uint32_t buttons, uint8_t lx, uint8_t ly)
{
    in_buttons = buttons;
    in_lx = lx;
    in_ly = ly;
}

int stream_send_input(void)
{
    if (net_error)
        return -1;
    return send_req(0, NULL);
}

void stream_set_prefetch(int on)
{
    g_prefetch = on;
    if (on)
        sceKernelSignalSema(want_sema, 1);
}

int stream_error(void)
{
    return net_error;
}

unsigned stream_dropped(void)
{
    return dropped;
}

unsigned stream_lost(void)
{
    return lost;
}

unsigned stream_nacks(void)
{
    return nacks;
}

unsigned stream_retries(void)
{
    return retries;
}

unsigned stream_early(void)
{
    return early_cur;
}

unsigned stream_completed(void)
{
    return completed;
}

void stream_stop(void)
{
    stopping = 1;
    if (!g_udp)
        net_abort(g_sock); /* desbloqueia o recv(); no UDP o select() volta sozinho em <= 200 ms */
    if (net_thid >= 0) {
        SceUInt timeout = 2 * 1000 * 1000;
        if (sceKernelWaitThreadEnd(net_thid, &timeout) < 0)
            sceKernelTerminateThread(net_thid);
        sceKernelDeleteThread(net_thid);
        net_thid = -1;
    }
    /* Depois da thread de rede parar, para nenhum pedido sair depois do BYE. */
    if (g_udp && send_sema >= 0)
        send_req(PS_REQ_BYE, NULL); /* o servidor solta as teclas e encerra já */
    SceUID *semas[] = {&lock_sema, &ready_sema, &want_sema, &send_sema};
    for (unsigned i = 0; i < sizeof(semas) / sizeof(semas[0]); i++) {
        if (*semas[i] >= 0)
            sceKernelDeleteSema(*semas[i]);
        *semas[i] = -1;
    }
}
