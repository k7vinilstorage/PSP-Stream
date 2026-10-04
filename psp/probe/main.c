/*
 * PSPStream - teste do decoder H.264 de hardware do PSP (Media Engine).
 *
 * Decodifica clipes curtos embutidos no EBOOT (tools/h264_probe_clips.py)
 * pelo mesmo caminho que o PMP Mod / PMPlayer usavam para tocar H.264 cru:
 *
 *   sceMpegCreate com um ringbuffer vazio, sceMpegBasePESpacketCopy leva o
 *   frame (Annex B) para a memória do Media Engine, sceMpegAvcDecode decodifica
 *   e já converte para RGBA 8888 no buffer que passamos.
 *
 * Responde três perguntas que só o hardware responde:
 *  1. o decoder funciona neste firmware, chamado de um app comum?
 *  2. quanto tempo leva por frame (decode + conversão de cor)?
 *  3. ele segura frames antes de entregar? Cada frame do clipe tem o próprio
 *     número desenhado em 8 blocos (branco = bit 1); lendo os blocos no frame
 *     que saiu, sabemos qual foi. Frames segurados = latência a mais no stream.
 *
 * O resultado aparece na tela e vai para resultado_h264.txt na pasta do EBOOT.
 */
#include <pspctrl.h>
#include <pspdebug.h>
#include <pspdisplay.h>
#include <pspkernel.h>
#include <pspmpeg.h>
#include <psppower.h>
#include <psputility.h>
#include <psputility_avmodules.h>
#include <psputility_modules.h>

#include <malloc.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

PSP_MODULE_INFO("PSPStreamH264", 0, 1, 0);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);
/* Heap pequeno: o mpeg.prx carregado pelo sceUtility vai para a memória de usuário. */
PSP_HEAP_SIZE_KB(6144);

/* clips.bin, embutido pelo bin2o */
extern unsigned char clips[];

/* sceMpegbase. O SceMpegLLI do pspmpegbase.h é alinhado a 64 bytes; o PMP usa
 * entradas de 16 bytes seguidas, e é isso que repetimos. */
typedef struct {
    void *src;
    void *dst;
    void *next;
    int size;
} lli_t;
int sceMpegBasePESpacketCopy(lli_t *lli);

#define DMA_BLOCK 4095    /* bytes por entrada da lista de DMA */
#define ME_AVC_BUF 0x4a000 /* destino na memória do Media Engine (o mesmo do PMP) */
#define MAX_AU (128 * 1024)
#define FB_SIZE (512 * 272 * 4)
#define VRAM ((u32 *)0x04000000)
#define VRAM_UNCACHED ((u32 *)0x44000000)

/* ---- relatório: tela + arquivo ---- */
#define MAX_LINES 40
static char report[MAX_LINES][80];
static int nlines;

static void say(const char *fmt, ...)
{
    if (nlines >= MAX_LINES)
        return;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(report[nlines], sizeof(report[0]), fmt, ap);
    va_end(ap);
    pspDebugScreenPrintf("%s\n", report[nlines]);
    nlines++;
}

static unsigned now_us(void)
{
    return sceKernelGetSystemTimeLow();
}

/* ---- decoder ---- */
typedef struct {
    SceMpeg mpeg;
    /* O SceMpegRingbuffer do pspsdk tem 44 bytes, mas a biblioteca mpeg dos
     * firmwares novos escreve 48 (um campo gp a mais) e passaria por cima do
     * campo seguinte. Folga de sobra. */
    union {
        SceMpegRingbuffer rb;
        uint8_t rb_room[128];
    };
    void *data;
    void *es;
    lli_t *lli;
    SceMpegAu au;
    int inited, rb_made, created;
} avc_t;

static void avc_close(avc_t *a)
{
    if (a->es)
        sceMpegFreeAvcEsBuf(&a->mpeg, a->es);
    if (a->created)
        sceMpegDelete(&a->mpeg);
    if (a->rb_made)
        sceMpegRingbufferDestruct(&a->rb);
    if (a->inited)
        sceMpegFinish();
    free(a->data);
    free(a->lli);
    memset(a, 0, sizeof(*a));
}

/* 0 = ok; senão diz em que passo parou e o código. */
static int avc_open(avc_t *a, const char **step)
{
    memset(a, 0, sizeof(*a));
    int r;
    *step = "sceMpegInit";
    if ((r = sceMpegInit()) != 0)
        return r;
    a->inited = 1;
    *step = "sceMpegQueryMemSize";
    int size = sceMpegQueryMemSize(0);
    if (size <= 0)
        return size ? size : -1;
    *step = "memalign";
    if (!(a->data = memalign(64, size)))
        return -1;
    *step = "sceMpegRingbufferConstruct";
    if ((r = sceMpegRingbufferConstruct(&a->rb, 0, NULL, 0, NULL, NULL)) != 0)
        return r;
    a->rb_made = 1;
    *step = "sceMpegCreate";
    if ((r = sceMpegCreate(&a->mpeg, a->data, size, &a->rb, 512, 0, 0)) != 0)
        return r;
    a->created = 1;
    *step = "sceMpegAvcDecodeMode";
    SceMpegAvcMode mode = {-1, SCE_MPEG_AVC_FORMAT_8888};
    if ((r = sceMpegAvcDecodeMode(&a->mpeg, &mode)) != 0)
        return r;
    *step = "sceMpegMallocAvcEsBuf";
    if (!(a->es = sceMpegMallocAvcEsBuf(&a->mpeg)))
        return -1;
    *step = "memalign lli";
    if (!(a->lli = memalign(64, sizeof(lli_t) * (MAX_AU / DMA_BLOCK + 1))))
        return -1;
    memset(&a->au, 0xFF, sizeof(a->au));
    a->au.iEsBuffer = 1; /* como o PMP: o primeiro ES buffer */
    return 0;
}

/* au: alinhado a 64 bytes. dest: RGBA 8888, largura 512. */
static int avc_decode(avc_t *a, void *au, int size, void *dest, SceInt32 *status)
{
    uint8_t *src = au;
    uint8_t *dst = (uint8_t *)ME_AVC_BUF;
    int i = 0;
    for (;;) {
        a->lli[i].src = src;
        a->lli[i].dst = dst;
        if (size > DMA_BLOCK) {
            a->lli[i].size = DMA_BLOCK;
            a->lli[i].next = &a->lli[i + 1];
            src += DMA_BLOCK;
            dst += DMA_BLOCK;
            size -= DMA_BLOCK;
            i++;
        } else {
            a->lli[i].size = size;
            a->lli[i].next = NULL;
            break;
        }
    }
    sceKernelDcacheWritebackInvalidateAll(); /* o DMA lê a RAM, não o cache */
    int r = sceMpegBasePESpacketCopy(a->lli);
    if (r != 0)
        return r;
    a->au.iAuSize = (src - (uint8_t *)au) + size;
    *status = 0;
    return sceMpegAvcDecode(&a->mpeg, &a->au, 512, &dest, status);
}

/* Número do frame desenhado nos blocos; -1 se os blocos não forem preto/branco. */
static int read_marker(const u32 *fb)
{
    int value = 0;
    for (int k = 0; k < 8; k++) {
        u32 p = fb[16 * 512 + 16 + 32 * k];
        int lum = ((p & 0xFF) + (p >> 8 & 0xFF) + (p >> 16 & 0xFF)) / 3;
        if (lum > 192)
            value |= 1 << k;
        else if (lum > 64)
            return -1;
    }
    return value;
}

/* ---- clipes ---- */
typedef struct {
    char name[25];
    int frames;
    const uint8_t *sizes; /* u32 LE; lido com memcpy */
    const uint8_t *data;
} clip_t;

static int clip_size(const clip_t *cl, int i)
{
    u32 v;
    memcpy(&v, cl->sizes + 4 * i, 4);
    return v;
}

static int load_clips(clip_t *out, int max)
{
    const uint8_t *p = clips;
    if (memcmp(p, "H264PRB1", 8))
        return 0;
    u32 n;
    memcpy(&n, p + 8, 4);
    p += 12;
    int count = 0;
    for (u32 c = 0; c < n && count < max; c++, count++) {
        clip_t *cl = &out[count];
        memcpy(cl->name, p, 24);
        cl->name[24] = 0;
        u32 frames, bytes;
        memcpy(&frames, p + 24, 4);
        memcpy(&bytes, p + 28, 4);
        cl->frames = frames;
        cl->sizes = p + 32;
        cl->data = p + 32 + 4 * frames;
        p = cl->data + bytes;
        p += (4 - ((p - clips) & 3)) & 3; /* o gerador alinha cada clipe a 4 bytes */
    }
    return count;
}

/* ---- um teste: decodifica o clipe inteiro ---- */
static const char *g_argv0;
static void write_report(const char *argv0);

enum {
    MODE_PLAIN, /* uma chamada por AU; lê o número depois de cada uma */
    MODE_GROUP, /* o clipe tem `group` AUs por frame (o frame + cópias); lê no último */
    MODE_EMPTY, /* depois de cada AU, `group - 1` chamadas com um AU só com o AUD */
    MODE_STOP,  /* depois de cada AU, sceMpegAvcDecodeStop (solta o que está preso) */
};

typedef struct {
    const char *label;
    int mode;
    int group;  /* chamadas por frame mostrado */
    int to_vram;
    int skip;   /* AU não entregue (simula perda); -1 = nenhum */
} pass_t;

static u32 *g_stop_bufs[4]; /* sceMpegAvcDecodeStop escreve até 4 imagens */

static int run(const clip_t *cl, const pass_t *ps, uint8_t *stage, u32 *ram_fb)
{
    /* cinza: os blocos do número ficam ilegíveis até o decoder escrever algo */
    memset(ps->to_vram ? (void *)VRAM_UNCACHED : (void *)((u32)ram_fb | 0x40000000), 0x80, FB_SIZE);
    avc_t a;
    const char *step = "";
    int r = avc_open(&a, &step);
    if (r != 0) {
        say("%s: falhou em %s (%08x)", ps->label, step, r);
        avc_close(&a);
        write_report(g_argv0);
        return -1;
    }
    static const uint8_t aud_only[64] __attribute__((aligned(64))) = {0, 0, 0, 1, 0x09, 0xF0};
    int calls = 0, ok = 0, shown = 0, errors = 0, first_err = 0, first_err_at = -1, no_picture = 0;
    int delay_min = 99, delay_max = -99, unreadable = 0, readable = 0, held_first = -1, stop_imgs = 0;
    unsigned t_sum = 0, t_max = 0, t_min = ~0u, t_frame = 0, bytes = 0, extra_sum = 0, extra_n = 0;
    const uint8_t *src = cl->data;
    void *dest = ps->to_vram ? (void *)VRAM : (void *)ram_fb;
    const u32 *fb = ps->to_vram ? VRAM_UNCACHED : (const u32 *)((u32)ram_fb | 0x40000000);
    for (int i = 0; i < cl->frames && errors < 5; i++) {
        int size = clip_size(cl, i);
        if (size <= 0 || size > MAX_AU)
            break;
        memcpy(stage, src, size);
        src += size;
        if (i == ps->skip)
            continue;
        bytes += size;
        SceInt32 status = 0;
        unsigned t0 = now_us();
        r = avc_decode(&a, stage, size, dest, &status);
        unsigned dt = now_us() - t0;
        calls++;
        if (r != 0) {
            if (!errors++) {
                first_err = r;
                first_err_at = i;
            }
            continue;
        }
        ok++;
        if (!status)
            no_picture++;
        int last = 1;       /* esta chamada fecha um frame mostrado? */
        int expect = i;     /* número que deveria aparecer agora */
        const u32 *out = fb;
        if (ps->mode == MODE_GROUP) {
            last = i % ps->group == ps->group - 1;
            expect = i / ps->group;
            if (i % ps->group) {
                extra_sum += dt;
                extra_n++;
            }
        } else if (ps->mode == MODE_EMPTY) {
            for (int k = 1; k < ps->group && r == 0; k++) {
                SceInt32 st = 0;
                unsigned te = now_us();
                r = avc_decode(&a, (void *)aud_only, 6, dest, &st);
                unsigned de = now_us() - te;
                calls++;
                dt += de;
                extra_sum += de;
                extra_n++;
                if (r == 0)
                    ok++;
                else if (!errors++) {
                    first_err = r;
                    first_err_at = i;
                }
            }
        } else if (ps->mode == MODE_STOP) {
            static u32 *vram_bufs[4] = {VRAM, VRAM, VRAM, VRAM};
            SceInt32 n = 0;
            unsigned ts = now_us();
            r = sceMpegAvcDecodeStop(&a.mpeg, 512, ps->to_vram ? vram_bufs : g_stop_bufs, &n);
            unsigned ds = now_us() - ts;
            dt += ds;
            extra_sum += ds;
            extra_n++;
            if (r != 0 && !errors++) {
                first_err = r;
                first_err_at = i;
            }
            if (r == 0 && n > 0 && n <= 4) {
                stop_imgs += n;
                out = ps->to_vram ? VRAM_UNCACHED : (const u32 *)((u32)g_stop_bufs[n - 1] | 0x40000000);
            }
        }
        t_frame += dt;
        if (!last)
            continue;
        shown++;
        if (shown > 1) { /* o 1º (IDR, decoder começando) fica fora da média */
            t_sum += t_frame;
            if (t_frame > t_max)
                t_max = t_frame;
            if (t_frame < t_min)
                t_min = t_frame;
        }
        t_frame = 0;
        int idx = read_marker(out);
        if (idx < 0) {
            unreadable++;
        } else {
            if (held_first < 0)
                held_first = expect;
            readable++;
            int d = expect - idx;
            if (d < delay_min)
                delay_min = d;
            if (d > delay_max)
                delay_max = d;
        }
        if (out != VRAM_UNCACHED)
            memcpy(VRAM_UNCACHED, out, FB_SIZE); /* mostra o progresso */
    }
    say("%s: %d/%d chamadas ok, %.1f KB por frame", ps->label, ok, calls, bytes / 1024.0f / (shown ? shown : 1));
    if (errors)
        say("  erro %08x no AU %d (%d erros)", first_err, first_err_at, errors);
    if (no_picture)
        say("  %d chamadas sem imagem (status 0)", no_picture);
    if (shown > 1)
        say("  por frame mostrado %.2f ms (min %.2f, max %.2f)", t_sum / 1000.0f / (shown - 1), t_min / 1000.0f,
            t_max / 1000.0f);
    if (extra_n)
        say("  chamadas extras: %.2f ms cada (%d)", extra_sum / 1000.0f / extra_n, extra_n);
    if (ps->mode == MODE_STOP)
        say("  Stop soltou %d imagens", stop_imgs);
    if (readable)
        say("  frames de atraso: %d a %d (1o legivel no frame %d)", delay_min, delay_max, held_first);
    else
        say("  nenhum frame legivel saiu (%d ilegiveis)", unreadable);
    if (unreadable && readable)
        say("  %d frames ilegiveis", unreadable);
    avc_close(&a);
    write_report(g_argv0); /* a cada passo: se o próximo travar o PSP, este fica gravado */
    return ok;
}

static void write_report(const char *argv0)
{
    char path[256];
    snprintf(path, sizeof(path), "%s", argv0);
    char *slash = strrchr(path, '/');
    if (!slash)
        return;
    strcpy(slash + 1, "resultado_h264.txt");
    SceUID fd = sceIoOpen(path, PSP_O_WRONLY | PSP_O_CREAT | PSP_O_TRUNC, 0777);
    if (fd < 0) {
        pspDebugScreenPrintf("nao consegui gravar %s (%08x)\n", path, fd);
        return;
    }
    for (int i = 0; i < nlines; i++) {
        sceIoWrite(fd, report[i], strlen(report[i]));
        sceIoWrite(fd, "\r\n", 2);
    }
    sceIoClose(fd);
}

static int exit_cb(int arg1, int arg2, void *common)
{
    sceKernelExitGame();
    return 0;
}

static int callback_thread(SceSize args, void *argp)
{
    int cbid = sceKernelCreateCallback("exit", exit_cb, NULL);
    sceKernelRegisterExitCallback(cbid);
    sceKernelSleepThreadCB();
    return 0;
}

int main(int argc, char *argv[])
{
    SceUID cb = sceKernelCreateThread("cb", callback_thread, 0x11, 0x1000, 0, NULL);
    if (cb >= 0)
        sceKernelStartThread(cb, 0, NULL);
    scePowerSetClockFrequency(333, 333, 166);

    memset(VRAM_UNCACHED, 0, FB_SIZE);
    sceDisplaySetMode(0, 480, 272);
    sceDisplaySetFrameBuf(VRAM, 512, PSP_DISPLAY_PIXEL_FORMAT_8888, PSP_DISPLAY_SETBUF_NEXTFRAME);
    pspDebugScreenInitEx(VRAM, PSP_DISPLAY_PIXEL_FORMAT_8888, 1);

    g_argv0 = argc > 0 ? argv[0] : "";
    say("PSPStream - teste do decoder H.264 (v3)");
    /* Como os jogos fazem nos firmwares novos (0x300 = codecs do ME, 0x303 =
     * mpeg.prx); o sceUtilityLoadAvModule antigo fica de reserva. 0x80020139 =
     * já carregado. */
    int m1 = sceUtilityLoadModule(PSP_MODULE_AV_AVCODEC);
    int m2 = sceUtilityLoadModule(PSP_MODULE_AV_MPEGBASE);
    say("modulos: avcodec %08x, mpegbase %08x", m1, m2);
    if ((m1 < 0 && m1 != (int)0x80020139) || (m2 < 0 && m2 != (int)0x80020139)) {
        m1 = sceUtilityLoadAvModule(PSP_AV_MODULE_AVCODEC);
        m2 = sceUtilityLoadAvModule(PSP_AV_MODULE_MPEGBASE);
        say("modulos (metodo antigo): avcodec %08x, mpegbase %08x", m1, m2);
    }
    write_report(g_argv0);

    clip_t cl[4];
    int n = load_clips(cl, 4);
    uint8_t *stage = memalign(64, MAX_AU);
    u32 *ram_fb = memalign(64, FB_SIZE);
    if (!n || !stage || !ram_fb) {
        say("clipes ou memoria indisponiveis (%d clipes)", n);
    } else {
        sceKernelDelayThread(500 * 1000);
        pspDebugScreenClear();
        /* v1/v2 (PSP-3000, 6.61): decode de ~4 ms, mas o decoder segura 2
         * frames. 2 cópias depois do frame: atraso 0, 12 ms. Stop: solta na hora
         * (1,1 ms), mas zera as referências e os P seguintes saem errados.
         * v3: só IDR (sem P) + Stop. No PC, o H.264 intra tem metade dos bytes do
         * JPEG na mesma SSIM. */
        static const pass_t passes[] = {
            {"intra, 1 chamada", MODE_PLAIN, 1, 0, -1},
            {"intra + Stop", MODE_STOP, 1, 0, -1},
            {"intra CABAC + Stop", MODE_STOP, 1, 0, -1},
            {"intra + Stop sem o frame 10", MODE_STOP, 1, 0, 10},
            {"intra + Stop na VRAM", MODE_STOP, 1, 1, -1},
            {"IPPP, 1 chamada (v1)", MODE_PLAIN, 1, 0, -1},
        };
        static const int clip_of[] = {1, 1, 2, 1, 1, 0};
        for (int k = 0; k < 4; k++)
            g_stop_bufs[k] = memalign(64, FB_SIZE);
        _Static_assert(sizeof(passes) / sizeof(passes[0]) == sizeof(clip_of) / sizeof(clip_of[0]), "um clipe por passo");
        for (unsigned p = 0; p < sizeof(passes) / sizeof(passes[0]); p++) {
            if (clip_of[p] >= n || (passes[p].mode == MODE_STOP && !g_stop_bufs[3])) {
                say("%s: clipe ou memoria indisponivel", passes[p].label);
                continue;
            }
            run(&cl[clip_of[p]], &passes[p], stage, ram_fb);
        }
    }

    /* resultado por cima da última imagem */
    pspDebugScreenSetXY(0, 0);
    pspDebugScreenSetBackColor(0xFF000000);
    pspDebugScreenEnableBackColor(1);
    pspDebugScreenClear();
    for (int i = 0; i < nlines; i++)
        pspDebugScreenPrintf("%s\n", report[i]);
    write_report(g_argv0);
    pspDebugScreenPrintf("\nGravado em resultado_h264.txt. X ou O para sair.\n");

    SceCtrlData pad;
    unsigned t0 = now_us();
    do {
        sceCtrlReadBufferPositive(&pad, 1);
        sceKernelDelayThread(50 * 1000);
    } while (!(pad.Buttons & (PSP_CTRL_CROSS | PSP_CTRL_CIRCLE)) && now_us() - t0 < 300u * 1000 * 1000);
    sceKernelExitGame();
    return 0;
}
