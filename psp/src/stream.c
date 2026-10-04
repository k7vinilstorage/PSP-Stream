/*
 * Thread de rede + troca de frames com a thread de decode.
 *
 * Três slots de JPEG: um recebendo, um pronto e um decodificando. Se chega um
 * frame novo antes de o pronto ser pego, o pronto é descartado. O decode
 * sempre pega o mais novo.
 *
 * Com prefetch, o próximo pedido sai assim que um frame chega, antes do
 * decode: rede e decode trabalham ao mesmo tempo.
 *
 * Transportes:
 *  - TCP: cabeçalho + JPEG num fluxo.
 *  - UDP: o JPEG chega em pedaços de 1400 bytes, remontados no slot. Se faltar
 *    algum, depois de CHUNK_GAP_US sem pedaço novo pedimos só os que faltam
 *    (NACK). Depois de MAX_NACKS desistimos do frame e pedimos outro. Pedido
 *    sem resposta é reenviado a cada REQ_RETRY_US (o servidor ignora
 *    duplicados).
 */
#include "stream.h"
#include "net.h"
#include "protocol.h"

#include <malloc.h>
#include <netinet/in.h>
#include <pspkernel.h>
#include <string.h>

#define NUM_SLOTS 3
/* Acima do decode (0x38) e das threads da pilha de rede (42/48): quando
 * chegam dados, o recv() roda na hora. */
#define NET_THREAD_PRIO 0x24

#define CHUNK_GAP_US (20 * 1000)   /* frame incompleto sem pedaço novo: NACK */
#define MAX_NACKS 3
#define REQ_RETRY_US (200 * 1000)  /* pedido sem resposta: reenvia */
#define STALL_US (3000 * 1000)     /* nada completo por 3 s: recomeça (HELLO); > keepalive de 1 s do servidor */

enum { SLOT_FREE, SLOT_RECV, SLOT_READY, SLOT_DECODING };

static ps_frame_t slots[NUM_SLOTS];
static int state[NUM_SLOTS];
static int ready_idx = -1;
static ps_ack_t last_ack;
static unsigned dropped, lost, nacks, completed;

static SceUID lock_sema = -1, ready_sema = -1, want_sema = -1, send_sema = -1;
static SceUID net_thid = -1;
static int g_sock = -1;
static int g_udp;
static struct sockaddr_in g_dest;
static volatile int g_prefetch;
static volatile int *g_running;
static volatile int net_error;
static volatile int stopping;

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
    r->flags = flags | (nack ? PS_REQ_NACK : 0);

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
    }
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

static void wait_want(void)
{
    /* Sem prefetch: espera o decode terminar antes de pedir o próximo. */
    while (*g_running && !stopping && !g_prefetch) {
        SceUInt timeout = 100 * 1000;
        if (sceKernelWaitSema(want_sema, 1, &timeout) == 0)
            return;
    }
}

/* Slot livre para receber (sempre há um: no máx. 1 pronto + 1 decodificando). */
static int claim_slot(void)
{
    lock();
    int idx = 0;
    while (idx < NUM_SLOTS && state[idx] != SLOT_FREE)
        idx++;
    if (idx == NUM_SLOTS) { /* não deveria acontecer */
        idx = ready_idx;
        ready_idx = -1;
        dropped++;
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

/* Frame completo: vira o "pronto" (o anterior não decodificado é descartado). */
static void publish_slot(int idx)
{
    lock();
    if (ready_idx >= 0) {
        state[ready_idx] = SLOT_FREE;
        dropped++;
    }
    state[idx] = SLOT_READY;
    ready_idx = idx;
    completed++;
    unlock();
    sceKernelSignalSema(ready_sema, 1);
}

static int net_thread_tcp(void)
{
    unsigned t_req = now_us();
    if (send_req(PS_REQ_HELLO | PS_REQ_FRAME, NULL) < 0)
        return -1;

    while (*g_running && !stopping) {
        int idx = claim_slot();
        ps_frame_hdr_t hdr;
        if (net_recv_all(g_sock, &hdr, sizeof(hdr)) < 0)
            return -1;
        if (hdr.magic != PS_MAGIC_FRAME || hdr.size == 0 || hdr.size > PS_MAX_JPEG)
            return -2;
        ps_frame_t *f = &slots[idx];
        if (net_recv_all(g_sock, f->data, hdr.size) < 0)
            return -1;
        f->size = hdr.size;
        f->frame_no = hdr.frame_no;
        f->send_ts = hdr.send_ts;
        f->t_req = t_req;
        f->t_recv = now_us();
        publish_slot(idx);

        if (!g_prefetch)
            wait_want();
        t_req = now_us();
        if (send_req(PS_REQ_FRAME, NULL) < 0)
            return -1;
    }
    return 0;
}

static int net_thread_udp(void)
{
    static uint8_t pkt[sizeof(ps_chunk_hdr_t) + PS_CHUNK_PAYLOAD + 64];
    uint32_t have[PS_MAX_CHUNKS / 32];
    uint32_t cur = 0;    /* frame sendo remontado (ou o último completo) */
    int idx = -1;        /* slot desse frame, enquanto incompleto */
    int complete = 1, got = 0, count = 0, frame_nacks = 0;
    unsigned t_req = now_us(), last_req = t_req, last_rx = t_req, last_done = t_req;

    if (send_req(PS_REQ_HELLO | PS_REQ_FRAME, NULL) < 0)
        return -1;

    while (*g_running && !stopping) {
        unsigned now = now_us();
        int timeout = complete ? (int)(REQ_RETRY_US - (now - last_req)) : (int)(CHUNK_GAP_US - (now - last_rx));
        if (timeout <= 0) {
            if (!complete && frame_nacks < MAX_NACKS) {
                ps_nack_t nk;
                memset(&nk, 0, sizeof(nk));
                nk.frame_no = cur;
                for (int i = 0; i < count; i++)
                    if (!(have[i / 32] >> (i % 32) & 1))
                        nk.missing[i / 32] |= 1u << (i % 32);
                frame_nacks++;
                nacks++;
                last_rx = now_us();
                if (send_req(0, &nk) < 0)
                    return -1;
            } else {
                if (!complete) { /* desiste do frame */
                    free_slot(idx);
                    idx = -1;
                    complete = 1;
                    lost++;
                    t_req = now_us();
                }
                int stalled = now_us() - last_done > STALL_US;
                last_req = now_us();
                if (send_req(PS_REQ_FRAME | (stalled ? PS_REQ_HELLO : 0), NULL) < 0)
                    return -1;
            }
            continue;
        }

        int r = net_wait_readable(g_sock, timeout);
        if (r < 0)
            return -1;
        if (r == 0)
            continue;
        int n = net_recv_dgram(g_sock, pkt, sizeof(pkt));
        if (n < 0)
            return -1;
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
        if (h.frame_no < cur && now_us() - last_done > STALL_US) {
            if (!complete) {
                free_slot(idx);
                idx = -1;
                complete = 1;
            }
            cur = 0;
        }
        if (h.frame_no < cur || (h.frame_no == cur && complete))
            continue; /* atrasado ou duplicado */
        if (h.frame_no > cur) {
            if (!complete) { /* o servidor já mandou outro: abandona o incompleto */
                free_slot(idx);
                lost++;
            }
            idx = claim_slot();
            cur = h.frame_no;
            count = h.count;
            got = 0;
            frame_nacks = 0;
            complete = 0;
            memset(have, 0, sizeof(have));
            slots[idx].size = h.size;
            slots[idx].frame_no = h.frame_no;
            slots[idx].send_ts = h.send_ts;
        }
        if (!(have[h.chunk / 32] >> (h.chunk % 32) & 1)) {
            have[h.chunk / 32] |= 1u << (h.chunk % 32);
            memcpy(slots[idx].data + h.chunk * PS_CHUNK_PAYLOAD, pkt + sizeof(h), plen);
            got++;
        }
        last_rx = now_us();

        if (got == count) {
            ps_frame_t *f = &slots[idx];
            f->t_req = t_req;
            f->t_recv = last_done = now_us();
            publish_slot(idx);
            idx = -1;
            complete = 1;
            if (!g_prefetch)
                wait_want();
            t_req = last_req = now_us();
            if (send_req(PS_REQ_FRAME, NULL) < 0)
                return -1;
        }
    }
    return 0;
}

static int net_thread(SceSize args, void *argp)
{
    int r = g_udp ? net_thread_udp() : net_thread_tcp();
    if (r < 0 && !net_error)
        net_error = r;
    sceKernelSignalSema(ready_sema, 1); /* acorda a thread de decode */
    return 0;
}

int stream_start(int sock, int udp, const struct sockaddr_in *dest, int prefetch, volatile int *running)
{
    g_sock = sock;
    g_udp = udp;
    if (udp)
        g_dest = *dest;
    g_prefetch = prefetch;
    g_running = running;
    net_error = 0;
    stopping = 0;
    dropped = lost = nacks = completed = 0;
    ready_idx = -1;
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
    int idx = ready_idx;
    ready_idx = -1;
    if (idx >= 0)
        state[idx] = SLOT_DECODING;
    unlock();
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
