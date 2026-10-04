/*
 * Thread de rede + troca de frames com a thread de decode.
 *
 * Três slots de JPEG: um recebendo, um pronto e um decodificando. Se chega um
 * frame novo antes de o pronto ser pego, o pronto é descartado. O decode
 * sempre pega o mais novo.
 *
 * Com prefetch, o próximo pedido sai assim que um frame chega, antes do
 * decode: rede e decode trabalham ao mesmo tempo.
 */
#include "stream.h"
#include "net.h"
#include "protocol.h"

#include <malloc.h>
#include <pspkernel.h>
#include <string.h>

#define NUM_SLOTS 3
/* Acima do decode (0x38) e das threads da pilha de rede (42/48): quando
 * chegam dados, o recv() roda na hora. */
#define NET_THREAD_PRIO 0x24

enum { SLOT_FREE, SLOT_RECV, SLOT_READY, SLOT_DECODING };

static ps_frame_t slots[NUM_SLOTS];
static int state[NUM_SLOTS];
static int ready_idx = -1;
static ps_ack_t last_ack;
static unsigned dropped;

static SceUID lock_sema = -1, ready_sema = -1, want_sema = -1, send_sema = -1;
static SceUID net_thid = -1;
static int g_sock = -1;
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

static int send_req(uint16_t flags)
{
    ps_req_t r;
    memset(&r, 0, sizeof(r));
    r.magic = PS_MAGIC_REQ;
    r.buttons = in_buttons;
    r.lx = in_lx;
    r.ly = in_ly;
    r.flags = flags;

    lock();
    ps_ack_t a = last_ack;
    unlock();
    if (a.frame_no) {
        r.ack_frame = a.frame_no;
        r.echo_ts = a.send_ts;
        r.net_t = a.net_t;
        r.local_t = a.local_t;
        r.decode_t = a.decode_t;
        r.since_t = clamp_u16((now_us() - a.t_shown) / 100);
    }

    sceKernelWaitSema(send_sema, 1, NULL);
    int rc = net_send_all(g_sock, &r, sizeof(r));
    sceKernelSignalSema(send_sema, 1);
    return rc;
}

static int wait_want(void)
{
    /* Sem prefetch: espera o decode terminar antes de pedir o próximo. */
    while (*g_running && !stopping && !g_prefetch) {
        SceUInt timeout = 100 * 1000;
        if (sceKernelWaitSema(want_sema, 1, &timeout) == 0)
            return 0;
    }
    return 0;
}

static int net_thread(SceSize args, void *argp)
{
    unsigned t_req = now_us();
    if (send_req(PS_REQ_HELLO | PS_REQ_FRAME) < 0)
        goto fail;

    while (*g_running && !stopping) {
        lock();
        int idx = 0;
        while (idx < NUM_SLOTS && state[idx] != SLOT_FREE)
            idx++;
        if (idx == NUM_SLOTS) { /* não deveria acontecer: no máx. 1 pronto + 1 decodificando */
            idx = ready_idx;
            ready_idx = -1;
            dropped++;
        }
        state[idx] = SLOT_RECV;
        unlock();

        ps_frame_hdr_t hdr;
        if (net_recv_all(g_sock, &hdr, sizeof(hdr)) < 0)
            goto fail;
        if (hdr.magic != PS_MAGIC_FRAME || hdr.size == 0 || hdr.size > PS_MAX_JPEG) {
            net_error = -2;
            goto fail;
        }
        ps_frame_t *f = &slots[idx];
        if (net_recv_all(g_sock, f->data, hdr.size) < 0)
            goto fail;
        f->size = hdr.size;
        f->frame_no = hdr.frame_no;
        f->send_ts = hdr.send_ts;
        f->t_req = t_req;
        f->t_recv = now_us();

        lock();
        if (ready_idx >= 0) {
            state[ready_idx] = SLOT_FREE;
            dropped++;
        }
        state[idx] = SLOT_READY;
        ready_idx = idx;
        unlock();
        sceKernelSignalSema(ready_sema, 1);

        if (!g_prefetch)
            wait_want();
        t_req = now_us();
        if (send_req(PS_REQ_FRAME) < 0)
            goto fail;
    }
    return 0;

fail:
    if (!net_error)
        net_error = -1;
    sceKernelSignalSema(ready_sema, 1); /* acorda a thread de decode */
    return 0;
}

int stream_start(int sock, int prefetch, volatile int *running)
{
    g_sock = sock;
    g_prefetch = prefetch;
    g_running = running;
    net_error = 0;
    stopping = 0;
    dropped = 0;
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
    return send_req(0);
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

void stream_stop(void)
{
    stopping = 1;
    net_abort(g_sock);
    if (net_thid >= 0) {
        SceUInt timeout = 2 * 1000 * 1000;
        if (sceKernelWaitThreadEnd(net_thid, &timeout) < 0)
            sceKernelTerminateThread(net_thid);
        sceKernelDeleteThread(net_thid);
        net_thid = -1;
    }
    SceUID *semas[] = {&lock_sema, &ready_sema, &want_sema, &send_sema};
    for (unsigned i = 0; i < sizeof(semas) / sizeof(semas[0]); i++) {
        if (*semas[i] >= 0)
            sceKernelDeleteSema(*semas[i]);
        *semas[i] = -1;
    }
}
