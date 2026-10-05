/*
 * Som do PC: o PC empurra um bloco IMA ADPCM a cada ~20 ms (UDP, sem
 * pedido; server/audio.py). A thread de rede decodifica o bloco num anel de
 * PCM estéreo, e a thread de som entrega pedaços de OUT_SAMPLES ao
 * sceAudioSRC, o canal com conversão de taxa (22,05-48 kHz). A saída
 * bloqueante dá o ritmo: o relógio é o do PSP.
 *
 * Dois buffers de saída, alternados: no PSP, o sceAudioSRCOutputBlocking
 * volta quando o pedaço entra na fila, e o hardware o lê (DMA) enquanto toca.
 * Com um buffer só, o pedaço seguinte era escrito por cima do que ainda
 * tocava, e o fim de cada pedaço saía estragado: um zumbido de ~125 Hz (um a
 * cada 8 ms), "de abelha", no PSP-3000. O PPSSPP copia na hora da chamada,
 * então lá não aparecia. Antes de entregar, o pedaço sai do cache para a RAM,
 * que é de onde o DMA lê.
 *
 * O anel absorve o vai e vem do Wi-Fi: começa a tocar quando tem o alvo
 * (40 ms), e o alvo se ajusta sozinho: +10 ms cada vez que o anel esvazia,
 * -5 ms a cada 10 s sem faltar, entre 30 e 120 ms. Pacote perdido vira
 * silêncio do mesmo tamanho (o tempo continua certo). Som demais no anel
 * (rajada depois de um atraso, ou o relógio do PC um pouco mais rápido que o
 * do PSP) é descartado até o alvo, para o atraso não crescer.
 */
#include "audio.h"
#include "ima.h"
#include "protocol.h"

#include <pspaudio.h>
#include <pspkernel.h>
#include <string.h>

#define AUDIO_PRIO 0x20       /* acima da rede (0x24): o canal não pode ficar sem amostras */
#define OUT_SAMPLES 256       /* por chamada (o sceAudioSRC aceita 17-4111): 8 ms a 32 kHz */
#define RING_FRAMES 16384     /* potência de 2: 0,34 s a 48 kHz */
#define MAX_BLOCK_SAMPLES 2048
#define MAX_GAP 5             /* até 5 pacotes perdidos seguidos viram silêncio; mais que isso recomeça */
#define TARGET_START_MS 40
#define TARGET_MIN_MS 30
#define TARGET_MAX_MS 120
#define TARGET_UP_MS 10
#define TARGET_DOWN_MS 5
#define EXTRA_MS 40           /* acima de alvo + isto, descarta até o alvo */
#define CALM_US (10 * 1000 * 1000)

static int16_t ring[RING_FRAMES * 2];
static unsigned r_wr, r_rd; /* contadores livres, em frames; com o lock */
static int16_t decoded[MAX_BLOCK_SAMPLES * 2];
static int16_t out[2][OUT_SAMPLES * 2] __attribute__((aligned(64)));

static SceUID lock_sema = -1, thid = -1;
static volatile int running, enabled;
static int rate, channels, packet_frames; /* do último pacote */
static uint32_t last_seq;
static int have_seq, playing, target;    /* target em frames */
static unsigned calm_since;
static unsigned packets, lost, underruns, skips;
static volatile int last_error;

static void lock(void)
{
    sceKernelWaitSema(lock_sema, 1, NULL);
}

static void unlock(void)
{
    sceKernelSignalSema(lock_sema, 1);
}

static int ms_frames(int ms)
{
    return rate * ms / 1000;
}

/* com o lock */
static void reset_ring(void)
{
    r_rd = r_wr;
    playing = 0;
}

static void ring_write(const int16_t *src, int frames)
{
    unsigned level = r_wr - r_rd;
    if (level + frames > RING_FRAMES) { /* não deveria acontecer: o alvo é bem menor */
        r_rd += level + frames - RING_FRAMES;
        skips++;
    }
    for (int i = 0; i < frames; i++) {
        unsigned k = (r_wr + i) & (RING_FRAMES - 1);
        if (src) {
            ring[2 * k] = src[2 * i];
            ring[2 * k + 1] = src[2 * i + 1];
        } else {
            ring[2 * k] = ring[2 * k + 1] = 0;
        }
    }
    r_wr += frames;
}

void audio_packet(const uint8_t *pkt, int len)
{
    if (!running || !enabled || len < (int)sizeof(ps_audio_hdr_t))
        return;
    ps_audio_hdr_t h;
    memcpy(&h, pkt, sizeof(h));
    if (h.codec != PS_AUDIO_IMA || h.channels < 1 || h.channels > 2 || h.samples < 1 ||
        h.samples > MAX_BLOCK_SAMPLES || h.rate < 8000 || h.rate > 48000)
        return;
    /* fora do lock: ~1300 amostras com somas e deslocamentos */
    int n = ima_decode_block(pkt + sizeof(h), len - (int)sizeof(h), h.channels, h.samples, decoded);
    if (n <= 0)
        return;
    lock();
    if (h.rate != rate || h.channels != channels) { /* formato novo (servidor reiniciou com outra taxa) */
        rate = h.rate;
        channels = h.channels;
        have_seq = 0;
        target = ms_frames(TARGET_START_MS);
        reset_ring();
    }
    packet_frames = n;
    if (have_seq) {
        int d = (int)(h.seq - last_seq - 1);
        if (d < 0 && d > -100) { /* atrasado ou repetido */
            unlock();
            return;
        }
        if (d > 0 && d <= MAX_GAP) { /* perdidos: silêncio do mesmo tamanho */
            lost += d;
            for (int i = 0; i < d; i++)
                ring_write(NULL, n);
        } else if (d != 0) { /* buraco grande ou o servidor recomeçou a contagem */
            if (d > 0)
                lost += d;
            reset_ring();
        }
    }
    last_seq = h.seq;
    have_seq = 1;
    packets++;
    ring_write(decoded, n);
    unlock();
}

/* Enche `buf` com o próximo pedaço (ou silêncio). Com o lock. */
static void fill_out(int16_t *buf, unsigned now)
{
    unsigned level = r_wr - r_rd;
    int min_target = ms_frames(TARGET_MIN_MS);
    if (min_target < packet_frames + OUT_SAMPLES)
        min_target = packet_frames + OUT_SAMPLES; /* o anel tem de aguentar o intervalo entre pacotes */
    if (target < min_target)
        target = min_target;
    if (!playing && level >= (unsigned)target) {
        playing = 1;
        calm_since = now;
    }
    if (!playing) {
        memset(buf, 0, OUT_SAMPLES * 4);
        return;
    }
    if (level > (unsigned)(target + ms_frames(EXTRA_MS))) {
        r_rd += level - target;
        level = target;
        skips++;
    }
    unsigned n = level < OUT_SAMPLES ? level : OUT_SAMPLES;
    for (unsigned i = 0; i < n; i++) {
        unsigned k = (r_rd + i) & (RING_FRAMES - 1);
        buf[2 * i] = ring[2 * k];
        buf[2 * i + 1] = ring[2 * k + 1];
    }
    r_rd += n;
    if (n < OUT_SAMPLES) { /* o anel esvaziou: espera encher de novo, com um alvo maior */
        memset(buf + 2 * n, 0, (OUT_SAMPLES - n) * 4);
        underruns++;
        playing = 0;
        target += ms_frames(TARGET_UP_MS);
        if (target > ms_frames(TARGET_MAX_MS))
            target = ms_frames(TARGET_MAX_MS);
        calm_since = now;
    } else if (now - calm_since > CALM_US) {
        target -= ms_frames(TARGET_DOWN_MS);
        calm_since = now;
    }
}

static int audio_thread(SceSize args, void *argp)
{
    int reserved = 0, cur = 0;
    while (running) {
        lock();
        int want = enabled ? rate : 0;
        unlock();
        if (want != reserved) {
            if (reserved)
                sceAudioSRCChRelease();
            reserved = 0;
            if (want) {
                int r = sceAudioSRCChReserve(OUT_SAMPLES, want, 2);
                if (r < 0) {
                    last_error = r;
                    sceKernelDelayThread(1000 * 1000);
                    continue;
                }
                last_error = 0;
                reserved = want;
            }
        }
        if (!reserved) {
            sceKernelDelayThread(10 * 1000);
            continue;
        }
        lock();
        fill_out(out[cur], sceKernelGetSystemTimeLow());
        unlock();
        sceKernelDcacheWritebackRange(out[cur], sizeof(out[cur]));
        /* volta quando o canal aceita mais; este buffer continua tocando,
         * então o próximo pedaço vai no outro */
        sceAudioSRCOutputBlocking(PSP_AUDIO_VOLUME_MAX, out[cur]);
        cur ^= 1;
    }
    if (reserved)
        sceAudioSRCChRelease();
    return 0;
}

int audio_start(int on)
{
    if (lock_sema < 0 && (lock_sema = sceKernelCreateSema("ps_audio", 0, 1, 1, NULL)) < 0)
        return lock_sema;
    lock();
    rate = channels = packet_frames = 0;
    have_seq = playing = 0;
    target = 0;
    r_rd = r_wr = 0;
    packets = lost = underruns = skips = 0;
    unlock();
    last_error = 0;
    enabled = on;
    running = 1;
    thid = sceKernelCreateThread("ps_audio", audio_thread, AUDIO_PRIO, 8 * 1024, PSP_THREAD_ATTR_USER, NULL);
    if (thid < 0) {
        running = 0;
        return thid;
    }
    return sceKernelStartThread(thid, 0, NULL);
}

void audio_stop(void)
{
    if (thid < 0)
        return;
    running = 0;
    SceUInt timeout = 500 * 1000;
    sceKernelWaitThreadEnd(thid, &timeout);
    sceKernelDeleteThread(thid);
    thid = -1;
}

void audio_set_enabled(int on)
{
    if (lock_sema < 0) { /* antes do primeiro stream */
        enabled = on;
        return;
    }
    lock();
    enabled = on;
    have_seq = 0;
    reset_ring();
    unlock();
}

int audio_enabled(void)
{
    return enabled;
}

void audio_get_stats(audio_stats_t *st)
{
    memset(st, 0, sizeof(*st));
    if (lock_sema < 0)
        return;
    lock();
    st->rate = rate;
    st->channels = channels;
    if (rate) {
        st->buffered_ms = (int)((r_wr - r_rd) * 1000u / rate);
        st->target_ms = target * 1000 / rate;
    }
    st->packets = packets;
    st->lost = lost;
    st->underruns = underruns;
    st->skips = skips;
    unlock();
    st->error = last_error;
}
