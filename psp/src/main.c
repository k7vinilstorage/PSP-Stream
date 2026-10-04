/*
 * PSPStream - cliente PSP.
 * Marco 1: conecta no Wi-Fi e no PC, pede um frame JPEG e o exibe.
 */
#include <malloc.h>
#include <pspctrl.h>
#include <pspdisplay.h>
#include <pspiofilemgr.h>
#include <pspkernel.h>
#include <psppower.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "config.h"
#include "decode.h"
#include "display.h"
#include "net.h"
#include "protocol.h"

PSP_MODULE_INFO("PSPStream", PSP_MODULE_USER, 0, 1);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);
/* Heap fixo: os módulos de rede carregados depois precisam de RAM livre. */
PSP_HEAP_SIZE_KB(8 * 1024);

#define DEFAULT_DIR "ms0:/PSP/GAME/PSPStream/"

static volatile int g_running = 1;
static volatile int g_sock = -1;

static int exit_callback(int arg1, int arg2, void *common)
{
    g_running = 0;
    net_abort(g_sock);
    /* Se a thread principal não sair a tempo, sai daqui mesmo. */
    sceKernelDelayThread(1500 * 1000);
    sceKernelExitGame();
    return 0;
}

static int callback_thread(SceSize args, void *argp)
{
    int cbid = sceKernelCreateCallback("exit_cb", exit_callback, NULL);
    sceKernelRegisterExitCallback(cbid);
    sceKernelSleepThreadCB();
    return 0;
}

static void setup_callbacks(void)
{
    int thid = sceKernelCreateThread("callbacks", callback_thread, 0x11, 0xFA0, 0, 0);
    if (thid >= 0)
        sceKernelStartThread(thid, 0, 0);
}

static void status(const char *msg)
{
    display_console("%s", msg);
}

/* Sem retorno: mostra o erro e espera o HOME. */
static void fail(const char *fmt, int code)
{
    display_console(fmt, code);
    display_console("Aperte HOME para sair.");
    while (g_running)
        sceKernelDelayThread(100 * 1000);
    sceKernelExitGame();
}

static void app_dir(const char *argv0, char *out, int len)
{
    snprintf(out, len, "%s", DEFAULT_DIR);
    if (!argv0)
        return;
    const char *slash = strrchr(argv0, '/');
    if (!slash || !strchr(argv0, ':') || slash - argv0 + 2 > len)
        return;
    memcpy(out, argv0, slash - argv0 + 1);
    out[slash - argv0 + 1] = '\0';
}

/* PPSSPP: tira um screenshot (usado nos testes automáticos). No PSP real
 * o dispositivo "emulator:" não existe e a chamada só falha. */
static void emu_screenshot(void)
{
    sceIoDevctl("emulator:", 0x20, NULL, 0, NULL, 0);
}

static unsigned int now_us(void)
{
    return sceKernelGetSystemTimeLow();
}

int main(int argc, char *argv[])
{
    setup_callbacks();
    scePowerSetClockFrequency(333, 333, 166);
    display_init();
    status("PSPStream v0.1 - marco 1");

    char dir[192], err[128];
    app_dir(argc > 0 ? argv[0] : NULL, dir, sizeof(dir));
    ps_config_t cfg;
    if (config_load(&cfg, dir, err, sizeof(err)) < 0) {
        status(err);
        fail("Crie server.txt com o IP do PC (%d).", 0);
    }
    display_console("Servidor: %s:%d", cfg.host, cfg.port);

    int r;
    if ((r = net_init()) < 0)
        fail("Erro ao iniciar a rede: 0x%08X", r);

    display_console("Conectando ao Wi-Fi (perfil %d)...", cfg.wifi_profile);
    char ip[32];
    if ((r = net_connect_ap(cfg.wifi_profile, ip, sizeof(ip), status, &g_running)) < 0)
        fail("Falha no Wi-Fi: 0x%08X (perfil existe? WLAN ligado?)", r);
    display_console("IP do PSP: %s", ip);

    display_console("Conectando ao PC %s:%d...", cfg.host, cfg.port);
    int sock = net_connect_server(cfg.host, cfg.port);
    if (sock < 0)
        fail("Sem conexao com o PC (%d). Servidor rodando? Firewall?", sock);
    g_sock = sock;

    if (decoder_init() < 0)
        fail("Decoder falhou (%d)", -1);

    /* Alinhado a 64 bytes: o mesmo buffer vai ao decoder de hardware depois. */
    uint8_t *jpeg = memalign(64, PS_MAX_JPEG);
    if (!jpeg)
        fail("Sem memoria (%d)", PS_MAX_JPEG);

    ps_req_t req;
    memset(&req, 0, sizeof(req));
    req.magic = PS_MAGIC_REQ;
    req.lx = req.ly = 128;
    req.flags = PS_REQ_HELLO | PS_REQ_FRAME;
    unsigned int t_req = now_us();
    if (net_send_all(sock, &req, sizeof(req)) < 0)
        fail("Erro ao enviar pedido (%d)", -1);

    ps_frame_hdr_t hdr;
    if (net_recv_all(sock, &hdr, sizeof(hdr)) < 0)
        fail("Conexao caiu esperando o frame (%d)", -1);
    if (hdr.magic != PS_MAGIC_FRAME || hdr.size == 0 || hdr.size > PS_MAX_JPEG)
        fail("Cabecalho de frame invalido (magic 0x%08X)", (int)hdr.magic);
    if (net_recv_all(sock, jpeg, hdr.size) < 0)
        fail("Conexao caiu no meio do frame (%d)", -1);
    unsigned int t_recv = now_us();

    int w = 0, h = 0;
    uint32_t *dst = display_back();
    if (decoder_decode(jpeg, hdr.size, dst, &w, &h) < 0) {
        status(decoder_error());
        fail("Erro ao decodificar o frame %d", (int)hdr.frame_no);
    }
    unsigned int t_dec = now_us();
    display_writeback();
    display_text(0, 33, 0xFF00FF00, "frame %u: %ux%u %.1f KB rede %.1f ms decode(%s) %.1f ms",
                 (unsigned)hdr.frame_no, (unsigned)w, (unsigned)h, hdr.size / 1024.0f,
                 (t_recv - t_req) / 1000.0f, decoder_name(), (t_dec - t_recv) / 1000.0f);
    display_flip(1);
    unsigned int t_shown = now_us();
    printf("frame %u: %dx%d %u bytes, rede %.1f ms, decode %.1f ms\n", (unsigned)hdr.frame_no, w, h,
           (unsigned)hdr.size, (t_recv - t_req) / 1000.0f, (t_dec - t_recv) / 1000.0f);

    /* Confirma a exibição para o servidor medir a latência (sem pedir outro frame). */
    req.flags = 0;
    req.ack_frame = hdr.frame_no;
    req.echo_ts = hdr.send_ts;
    req.net_t = (t_recv - t_req) / 100;
    req.local_t = (t_shown - t_recv) / 100;
    req.decode_t = (t_dec - t_recv) / 100;
    req.since_t = (now_us() - t_shown) / 100;
    net_send_all(sock, &req, sizeof(req));

    if (cfg.exit_after > 0) {
        display_wait_vblank();
        display_wait_vblank();
        emu_screenshot();
        g_running = 0;
    }

    while (g_running)
        sceKernelDelayThread(100 * 1000);

    g_sock = -1;
    close(sock);
    decoder_term();
    net_term();
    sceKernelExitGame();
    return 0;
}
