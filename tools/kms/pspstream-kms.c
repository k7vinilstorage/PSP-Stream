/*
 * pspstream-kms: entrega a imagem da tela (plano principal do KMS) ao
 * servidor do PSPStream como DMA-BUF, a cada quadro novo.
 *
 * Por quê: no GNOME 50, a captura pelo portal fica em ~40 fps (limitador do
 * mutter, ver docs/MEASUREMENTS.md). O KMS lê o buffer que a placa de vídeo
 * está mostrando, sem passar pelo compositor, como a captura KMS do Sunshine.
 *
 * Privilégio: drmModeGetFB2 só devolve os handles do buffer com
 * CAP_SYS_ADMIN (quem não é o "DRM master"). Este programa é a única parte
 * com essa permissão (sudo setcap cap_sys_admin+ep pspstream-kms), e só faz
 * isto: achar o plano principal, exportar o buffer como DMA-BUF (somente
 * leitura) e mandar pelo socket herdado do servidor. A redução para 480x272
 * é feita no servidor, sem privilégio, no OpenGL.
 *
 * Uso (o servidor chama): pspstream-kms <fd do socket> [--card /dev/dri/cardN] [--monitor N]
 * Protocolo (SOCK_SEQPACKET, little-endian): ver struct request/reply e server/kms.py.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#include <drm_fourcc.h>
#include <xf86drm.h>
#include <xf86drmMode.h>

enum { ST_FRAME = 0, ST_NOFRAME = 1, ST_HELLO = 2, ST_ERROR = -1 };
enum { CMD_FRAME = 1, CMD_QUIT = 2 };

struct __attribute__((packed)) request {
    char magic[4]; /* "PSKQ" */
    uint32_t cmd;
    uint32_t timeout_ms; /* CMD_FRAME: espera um quadro novo até isso */
};

struct __attribute__((packed)) reply {
    char magic[4]; /* "PSK1" */
    int32_t status;
    uint32_t fb_id, width, height, fourcc;
    uint64_t modifier;
    uint32_t n_planes;
    uint32_t pitches[4], offsets[4];
    uint32_t refresh_mhz; /* HELLO: taxa da tela em mHz */
    uint32_t crtc_id;
    char msg[160];
};

#define MAX_CANDIDATES 16
#define POLL_NS (1 * 1000 * 1000) /* 1 ms entre leituras do plano */

typedef struct {
    int fd;
    char path[32];
    uint32_t plane_id, crtc_id, refresh_mhz, width, height;
} candidate_t;

static int sock = -1;

static void send_reply(struct reply *r, const int *fds, int nfds)
{
    memcpy(r->magic, "PSK1", 4);
    struct iovec iov = {r, sizeof(*r)};
    union {
        char buf[CMSG_SPACE(sizeof(int) * 4)];
        struct cmsghdr align;
    } ctrl;
    struct msghdr m = {0};
    m.msg_iov = &iov;
    m.msg_iovlen = 1;
    if (nfds > 0) {
        memset(&ctrl, 0, sizeof(ctrl));
        m.msg_control = ctrl.buf;
        m.msg_controllen = CMSG_SPACE(sizeof(int) * nfds);
        struct cmsghdr *c = CMSG_FIRSTHDR(&m);
        c->cmsg_level = SOL_SOCKET;
        c->cmsg_type = SCM_RIGHTS;
        c->cmsg_len = CMSG_LEN(sizeof(int) * nfds);
        memcpy(CMSG_DATA(c), fds, sizeof(int) * nfds);
    }
    if (sendmsg(sock, &m, MSG_NOSIGNAL) < 0)
        exit(0); /* o servidor saiu */
}

static void send_error(const char *fmt, ...)
{
    struct reply r = {0};
    r.status = ST_ERROR;
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(r.msg, sizeof(r.msg), fmt, ap);
    va_end(ap);
    send_reply(&r, NULL, 0);
}

static int plane_is_primary(int fd, uint32_t plane_id)
{
    drmModeObjectPropertiesPtr props = drmModeObjectGetProperties(fd, plane_id, DRM_MODE_OBJECT_PLANE);
    int primary = 0;
    for (uint32_t i = 0; props && i < props->count_props; i++) {
        drmModePropertyPtr p = drmModeGetProperty(fd, props->props[i]);
        if (p && !strcmp(p->name, "type"))
            primary = props->prop_values[i] == DRM_PLANE_TYPE_PRIMARY;
        drmModeFreeProperty(p);
    }
    drmModeFreeObjectProperties(props);
    return primary;
}

/* Planos principais com imagem, em todas as placas: um por monitor ligado. */
static int find_candidates(const char *only_card, candidate_t *c, int max)
{
    int n = 0;
    for (int card = 0; card < 16 && n < max; card++) {
        char path[32];
        snprintf(path, sizeof(path), "/dev/dri/card%d", card);
        if (only_card && strcmp(only_card, path))
            continue;
        int fd = open(path, O_RDWR | O_CLOEXEC);
        if (fd < 0)
            continue;
        drmSetClientCap(fd, DRM_CLIENT_CAP_UNIVERSAL_PLANES, 1);
        drmModePlaneResPtr planes = drmModeGetPlaneResources(fd);
        int used = 0;
        for (uint32_t i = 0; planes && i < planes->count_planes && n < max; i++) {
            drmModePlanePtr p = drmModeGetPlane(fd, planes->planes[i]);
            if (p && p->fb_id && p->crtc_id && plane_is_primary(fd, p->plane_id)) {
                drmModeCrtcPtr crtc = drmModeGetCrtc(fd, p->crtc_id);
                if (crtc && crtc->mode_valid) {
                    candidate_t *x = &c[n++];
                    x->fd = fd;
                    snprintf(x->path, sizeof(x->path), "%s", path);
                    x->plane_id = p->plane_id;
                    x->crtc_id = p->crtc_id;
                    x->width = crtc->mode.hdisplay;
                    x->height = crtc->mode.vdisplay;
                    uint64_t pixels = (uint64_t)crtc->mode.htotal * crtc->mode.vtotal;
                    x->refresh_mhz = pixels ? (uint32_t)((uint64_t)crtc->mode.clock * 1000000 / pixels) : 0;
                    used = 1;
                }
                drmModeFreeCrtc(crtc);
            }
            drmModeFreePlane(p);
        }
        drmModeFreePlaneResources(planes);
        if (!used)
            close(fd);
    }
    return n;
}

static void gem_close(int fd, uint32_t handle)
{
    struct drm_gem_close args = {.handle = handle};
    drmIoctl(fd, DRM_IOCTL_GEM_CLOSE, &args);
}

/* Exporta o framebuffer como DMA-BUF e manda. 0 = mandou, 1 = sumiu (troca de modo), -1 = erro fatal. */
static int send_frame(int fd, uint32_t fb_id)
{
    drmModeFB2Ptr fb = drmModeGetFB2(fd, fb_id);
    if (!fb)
        return 1;
    if (!fb->handles[0]) {
        drmModeFreeFB2(fb);
        send_error("sem permissão para ler a tela: rode sudo setcap cap_sys_admin+ep neste programa "
                   "(e de novo depois de cada make)");
        return -1;
    }
    struct reply r = {0};
    int fds[4], n = 0;
    r.status = ST_FRAME;
    r.fb_id = fb_id;
    r.width = fb->width;
    r.height = fb->height;
    r.fourcc = fb->pixel_format;
    r.modifier = (fb->flags & DRM_MODE_FB_MODIFIERS) ? fb->modifier : DRM_FORMAT_MOD_INVALID;
    int ok = 1;
    for (int i = 0; i < 4 && fb->handles[i]; i++) {
        if (drmPrimeHandleToFD(fd, fb->handles[i], DRM_CLOEXEC, &fds[n]) < 0) {
            ok = 0;
            break;
        }
        r.pitches[n] = fb->pitches[i];
        r.offsets[n] = fb->offsets[i];
        n++;
    }
    r.n_planes = n;
    for (int i = 0; i < 4 && fb->handles[i]; i++) { /* planos podem repetir o mesmo handle */
        int seen = 0;
        for (int j = 0; j < i; j++)
            seen |= fb->handles[j] == fb->handles[i];
        if (!seen)
            gem_close(fd, fb->handles[i]);
    }
    drmModeFreeFB2(fb);
    if (ok)
        send_reply(&r, fds, n);
    for (int i = 0; i < n; i++)
        close(fds[i]);
    if (!ok) {
        send_error("não consegui exportar o buffer da tela (drmPrimeHandleToFD: %s)", strerror(errno));
        return -1;
    }
    return 0;
}

static uint64_t now_ns(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000u + t.tv_nsec;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "uso: %s <fd> [--card /dev/dri/cardN] [--monitor N] (chamado pelo servidor)\n", argv[0]);
        return 2;
    }
    sock = atoi(argv[1]);
    const char *card = NULL;
    int monitor = 0;
    for (int i = 2; i + 1 < argc; i += 2) {
        if (!strcmp(argv[i], "--card"))
            card = argv[i + 1];
        else if (!strcmp(argv[i], "--monitor"))
            monitor = atoi(argv[i + 1]);
    }
    /* Só abre placas de vídeo: o programa tem CAP_SYS_ADMIN. */
    if (card && (strncmp(card, "/dev/dri/card", 13) || !card[13] || strspn(card + 13, "0123456789") != strlen(card + 13))) {
        send_error("--kms-card precisa ser /dev/dri/cardN");
        return 1;
    }

    candidate_t c[MAX_CANDIDATES];
    int n = find_candidates(card, c, MAX_CANDIDATES);
    if (n == 0) {
        send_error("nenhum monitor ligado encontrado em %s", card ? card : "/dev/dri/card*");
        return 1;
    }
    if (monitor < 0 || monitor >= n) {
        send_error("--kms-monitor %d não existe: há %d monitor(es) (0 a %d)", monitor, n, n - 1);
        return 1;
    }
    candidate_t *m = &c[monitor];
    for (int i = 0; i < n; i++)
        if (c[i].fd != m->fd) {
            int shared = 0; /* outro candidato da mesma placa já fechou? */
            for (int j = 0; j < i; j++)
                shared |= c[j].fd == c[i].fd;
            if (!shared)
                close(c[i].fd);
        }

    struct reply hello = {0};
    hello.status = ST_HELLO;
    hello.width = m->width;
    hello.height = m->height;
    hello.refresh_mhz = m->refresh_mhz;
    hello.crtc_id = m->crtc_id;
    snprintf(hello.msg, sizeof(hello.msg), "%s, monitor %d de %d: %ux%u a %.3f Hz", m->path, monitor, n, m->width,
             m->height, m->refresh_mhz / 1000.0);
    send_reply(&hello, NULL, 0);

    uint32_t last_fb = 0;
    for (;;) {
        struct request q;
        ssize_t got = recv(sock, &q, sizeof(q), 0);
        if (got != (ssize_t)sizeof(q) || memcmp(q.magic, "PSKQ", 4) || q.cmd == CMD_QUIT)
            return 0;
        uint64_t deadline = now_ns() + (uint64_t)q.timeout_ms * 1000000u;
        for (;;) {
            drmModePlanePtr p = drmModeGetPlane(m->fd, m->plane_id);
            uint32_t fb = p ? p->fb_id : 0;
            drmModeFreePlane(p);
            /* O compositor troca de buffer a cada quadro novo; o mesmo fb_id = nada mudou. */
            if (fb && fb != last_fb) {
                int r = send_frame(m->fd, fb);
                if (r < 0)
                    return 1;
                if (r == 0) {
                    last_fb = fb;
                    break;
                }
            }
            if (now_ns() >= deadline) {
                struct reply none = {0};
                none.status = ST_NOFRAME;
                send_reply(&none, NULL, 0);
                break;
            }
            struct timespec ts = {0, POLL_NS};
            nanosleep(&ts, NULL);
        }
    }
}
