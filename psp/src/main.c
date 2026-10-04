/*
 * PSPStream - cliente PSP.
 *
 * Thread principal (prioridade baixa): decode + exibição.
 * Thread de rede (stream.c, prioridade alta): pede e recebe frames.
 * Thread de controles: lê o direcional a 60 Hz e manda mudanças na hora, sem
 * esperar o próximo frame (latência de entrada baixa mesmo com FPS baixo).
 *
 * Atalhos locais (segure SELECT + START e aperte):
 *   triângulo = overlay    quadrado = decoder hw/sw
 *   círculo   = vsync      X        = prefetch
 *   L         = transporte TCP/UDP (reconecta)
 */
#include <netinet/in.h>
#include <pspctrl.h>
#include <pspdisplay.h>
#include <pspiofilemgr.h>
#include <pspkernel.h>
#include <pspnet_apctl.h>
#include <psppower.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "config.h"
#include "decode.h"
#include "display.h"
#include "net.h"
#include "protocol.h"
#include "stream.h"

PSP_MODULE_INFO("PSPStream", PSP_MODULE_USER, 0, 3);
PSP_MAIN_THREAD_ATTR(THREAD_ATTR_USER | THREAD_ATTR_VFPU);
/* Heap fixo: os módulos de rede/avcodec carregados depois precisam de RAM livre. */
PSP_HEAP_SIZE_KB(8 * 1024);

#define DEFAULT_DIR "ms0:/PSP/GAME/PSPStream/"
/* Abaixo das threads da pilha de rede (42/48) e da nossa (0x24): um decode
 * longo não impede o TCP de receber o próximo frame. */
#define DECODE_PRIO 0x38

static volatile int g_running = 1;
static volatile int g_sock = -1;

static int exit_callback(int arg1, int arg2, void *common)
{
    g_running = 0;
    net_abort(g_sock);
    sceKernelDelayThread(1500 * 1000);
    sceKernelExitGame(); /* se a thread principal não saiu a tempo */
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

static void wait_exit(void)
{
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

/* PPSSPP: tira um screenshot (testes automáticos). No PSP real só falha. */
static void emu_screenshot(void)
{
    sceIoDevctl("emulator:", 0x20, NULL, 0, NULL, 0);
}

static unsigned now_us(void)
{
    return sceKernelGetSystemTimeLow();
}

static uint16_t tenth_ms(unsigned us)
{
    us /= 100;
    return us > 0xFFFF ? 0xFFFF : us;
}

/* ---- estatísticas exibidas no overlay (janela de 1 s) ---- */
typedef struct {
    unsigned t0;
    int frames;
    unsigned bytes, dec_us, net_us, local_us;
    float fps, kb, kbps, dec_ms, net_ms, local_ms;
} stats_t;

static void stats_add(stats_t *s, const ps_frame_t *f, unsigned dec_us, unsigned local_us)
{
    s->frames++;
    s->bytes += f->size;
    s->dec_us += dec_us;
    s->net_us += f->t_recv - f->t_req;
    s->local_us += local_us;
    unsigned now = now_us();
    unsigned dt = now - s->t0;
    if (dt >= 1000 * 1000) {
        float n = s->frames ? s->frames : 1;
        s->fps = s->frames * 1e6f / dt;
        s->kb = s->bytes / n / 1024.0f;
        s->kbps = s->bytes * 1e6f / dt / 1024.0f;
        s->dec_ms = s->dec_us / n / 1000.0f;
        s->net_ms = s->net_us / n / 1000.0f;
        s->local_ms = s->local_us / n / 1000.0f;
        printf("%.1f fps %.1f KB %.0f KB/s dec %.1f ms (%s) rede %.1f ms local %.1f ms drop %u\n", s->fps, s->kb,
               s->kbps, s->dec_ms, decoder_name(), s->net_ms, s->local_ms, stream_dropped());
        s->t0 = now;
        s->frames = 0;
        s->bytes = s->dec_us = s->net_us = s->local_us = 0;
    }
}

/* ---- controles e atalhos locais ---- */
#define MENU_COMBO (PSP_CTRL_SELECT | PSP_CTRL_START)
#define FORWARD_MASK (PSP_CTRL_SELECT | PSP_CTRL_START | PSP_CTRL_UP | PSP_CTRL_RIGHT | PSP_CTRL_DOWN | \
                      PSP_CTRL_LEFT | PSP_CTRL_LTRIGGER | PSP_CTRL_RTRIGGER | PSP_CTRL_TRIANGLE | \
                      PSP_CTRL_CIRCLE | PSP_CTRL_CROSS | PSP_CTRL_SQUARE)
#define INPUT_PRIO 0x28
#define STICK_DEADZONE 20 /* analógicos gastos repousam longe de 128 */

enum { ACT_OVERLAY, ACT_DECODER, ACT_VSYNC, ACT_PREFETCH, ACT_TRANSPORT, ACT_COUNT };
static const uint32_t act_button[ACT_COUNT] = {PSP_CTRL_TRIANGLE, PSP_CTRL_SQUARE, PSP_CTRL_CIRCLE, PSP_CTRL_CROSS,
                                               PSP_CTRL_LTRIGGER};
/* Só a thread de controles escreve; a principal só lê: sem lock. */
static volatile unsigned act_count[ACT_COUNT];
static volatile int input_run;
static int input_enabled = 1;
static int input_udp; /* UDP pode perder um pacote: mandamos redundante */

static int stick(int v)
{
    return (v > 128 - STICK_DEADZONE && v < 128 + STICK_DEADZONE) ? 128 : v;
}

static int input_thread(SceSize args, void *argp)
{
    uint32_t prev_raw = 0, sent = 0;
    int sent_lx = 128, sent_ly = 128;
    int repeat = 0, tick = 0;
    while (input_run) {
        SceCtrlData pad;
        if (sceCtrlReadBufferPositive(&pad, 1) < 0) { /* espera a próxima amostra (vblank) */
            sceKernelDelayThread(16 * 1000);
            continue;
        }
        uint32_t raw = pad.Buttons & FORWARD_MASK;
        uint32_t b = raw;
        int lx = stick(pad.Lx), ly = stick(pad.Ly);
        if ((raw & MENU_COMBO) == MENU_COMBO) {
            uint32_t pressed = raw & ~prev_raw;
            for (int i = 0; i < ACT_COUNT; i++)
                if (pressed & act_button[i])
                    act_count[i]++;
            b = 0; /* nada vai para o PC enquanto o atalho está segurado */
            lx = ly = 128;
        }
        prev_raw = raw;
        if (!input_enabled)
            continue;
        if (b != sent || abs(lx - sent_lx) > 2 || abs(ly - sent_ly) > 2) {
            stream_set_input(b, lx, ly);
            stream_send_input();
            sent = b;
            sent_lx = lx;
            sent_ly = ly;
            repeat = input_udp; /* no UDP, repete a mudança na próxima amostra */
            tick = 0;
        } else if (repeat) {
            stream_send_input();
            repeat = 0;
        } else if ((sent || sent_lx != 128 || sent_ly != 128) && ++tick >= 6) {
            /* Algo segurado: reafirma o estado a cada ~100 ms. O servidor solta
             * tudo se ficar 500 ms sem notícia, então tecla presa dura no
             * máximo isso mesmo se a rede travar. */
            stream_send_input();
            tick = 0;
        }
    }
    return 0;
}

typedef struct {
    int overlay, vsync, prefetch, udp;
    int switch_transport; /* atalho L: reconectar com o outro transporte */
    char toast[48];
    unsigned toast_until;
    int clear; /* buffers a limpar (texto antigo do overlay) */
} ui_t;

static void toast(ui_t *ui, const char *msg)
{
    snprintf(ui->toast, sizeof(ui->toast), "%s", msg);
    ui->toast_until = now_us() + 2 * 1000 * 1000;
    ui->clear = 3;
}

static void apply_menu(ui_t *ui, unsigned seen[ACT_COUNT])
{
    char msg[48];
    for (int i = 0; i < ACT_COUNT; i++) {
        while (seen[i] != act_count[i]) {
            seen[i]++;
            switch (i) {
            case ACT_OVERLAY:
                ui->overlay = !ui->overlay;
                ui->clear = 3;
                break;
            case ACT_DECODER: {
                int k = decoder_select(decoder_kind() == DEC_HW ? DEC_SW : DEC_HW);
                snprintf(msg, sizeof(msg), "decoder: %s%s", decoder_name(),
                         k == DEC_SW && decoder_error()[0] ? " (hw falhou)" : "");
                toast(ui, msg);
                break;
            }
            case ACT_VSYNC:
                ui->vsync = !ui->vsync;
                snprintf(msg, sizeof(msg), "vsync: %s", ui->vsync ? "on" : "off");
                toast(ui, msg);
                break;
            case ACT_PREFETCH:
                ui->prefetch = !ui->prefetch;
                stream_set_prefetch(ui->prefetch);
                snprintf(msg, sizeof(msg), "prefetch: %s", ui->prefetch ? "on" : "off");
                toast(ui, msg);
                break;
            case ACT_TRANSPORT:
                ui->switch_transport = 1;
                break;
            }
        }
    }
}

static void draw_overlay(const ui_t *ui, const stats_t *s)
{
    if (ui->overlay) {
        display_text(0, 0, 0xFF00FF00, "%4.1f fps %5.1f KB %4.0f KB/s", s->fps, s->kb, s->kbps);
        display_text(0, 1, 0xFF00FF00, "dec %4.1f ms (%s) rede %4.1f ms %s drop %u", s->dec_ms, decoder_name(),
                     s->net_ms, ui->udp ? "udp" : "tcp", stream_dropped());
        if (ui->udp)
            display_text(0, 2, 0xFF00FF00, "perdidos %u  nack %u", stream_lost(), stream_nacks());
    }
    if (ui->toast_until && (int)(ui->toast_until - now_us()) > 0)
        display_text(0, 33, 0xFF00FFFF, "%s", ui->toast);
}

/* bench=1: decodifica o mesmo frame N vezes com cada decoder e mostra o tempo
 * médio. Compara hw x sw no PSP real sem a rede no meio. */
static void decode_bench(const ps_frame_t *f)
{
    const int runs = 30;
    int original = decoder_kind();
    int kinds[2] = {DEC_SW, DEC_HW};
    char line[2][64];
    for (int k = 0; k < 2; k++) {
        if (decoder_select(kinds[k]) != kinds[k]) {
            snprintf(line[k], sizeof(line[k]), "%s: indisponivel (%s)", kinds[k] == DEC_HW ? "hw" : "sw",
                     decoder_error());
            continue;
        }
        int w, h, fails = 0;
        decoder_decode(f->data, f->size, display_back(), &w, &h); /* aquece caches/módulo */
        unsigned t0 = now_us();
        for (int i = 0; i < runs; i++)
            fails += decoder_decode(f->data, f->size, display_back(), &w, &h) < 0;
        unsigned dt = now_us() - t0;
        snprintf(line[k], sizeof(line[k]), "%-6s %5.2f ms/frame (%dx%d, %.1f KB)%s", decoder_name(),
                 dt / 1000.0f / runs, w, h, f->size / 1024.0f, fails ? " COM ERROS" : "");
    }
    decoder_select(original);
    display_console("Benchmark de decode (%d execucoes):", runs);
    display_console("  %s", line[0]);
    display_console("  %s", line[1]);
    display_console("O stream continua em 5 s...");
    sceKernelDelayThread(5 * 1000 * 1000);
}

/* Um stream completo, até a conexão cair, o usuário sair ou trocar de
 * transporte (devolve 1). */
static int run_stream(int sock, const struct sockaddr_in *dest, const ps_config_t *cfg, ui_t *ui)
{
    input_udp = ui->udp;
    ui->switch_transport = 0;
    if (stream_start(sock, ui->udp, dest, ui->prefetch, &g_running) < 0) {
        status("Erro ao iniciar a thread de rede");
        return -1;
    }
    stats_t st;
    memset(&st, 0, sizeof(st));
    st.t0 = now_us();
    unsigned seen[ACT_COUNT];
    for (int i = 0; i < ACT_COUNT; i++)
        seen[i] = act_count[i];
    input_run = 1;
    SceUID input_thid = sceKernelCreateThread("ps_input", input_thread, INPUT_PRIO, 16 * 1024, PSP_THREAD_ATTR_USER, NULL);
    if (input_thid >= 0)
        sceKernelStartThread(input_thid, 0, NULL);
    int shown = 0, last_w = SCR_W, last_h = SCR_H;
    int bench_pending = cfg->bench;
    unsigned t_start = now_us();
    int waiting_msg = 0;
    ui->clear = 3;

    while (g_running) {
        apply_menu(ui, seen);
        if (ui->switch_transport)
            break;

        ps_frame_t *f = stream_take(100 * 1000);
        if (!f) {
            if (stream_error())
                break;
            /* No UDP não há "conexão": avisa se o PC não responde. */
            if (ui->udp && !stream_completed() && now_us() - t_start > 3 * 1000 * 1000 && !waiting_msg) {
                status("Sem resposta do PC via UDP. Servidor rodando?");
                status("Firewall liberado para UDP? (firewall-cmd --add-port=5123/udp)");
                waiting_msg = 1;
            }
            continue;
        }

        if (bench_pending) {
            bench_pending = 0;
            decode_bench(f);
            ui->clear = 3;
        }

        unsigned t0 = now_us();
        uint32_t *dst = display_back();
        if (ui->clear > 0) {
            display_clear_back();
            ui->clear--;
        }
        int w = 0, h = 0;
        if (decoder_decode(f->data, f->size, dst, &w, &h) < 0) {
            printf("frame %u: %s\n", (unsigned)f->frame_no, decoder_error());
            stream_release(f, NULL);
            continue;
        }
        unsigned t1 = now_us();
        if (w != last_w || h != last_h) {
            last_w = w;
            last_h = h;
            ui->clear = 3; /* sobra da imagem anterior nas bordas */
        }
        display_writeback();
        draw_overlay(ui, &st);
        display_flip(ui->vsync);
        unsigned t2 = now_us();

        ps_ack_t ack = {f->frame_no, f->send_ts, t2, tenth_ms(f->t_recv - f->t_req), tenth_ms(t2 - f->t_recv),
                        tenth_ms(t1 - t0)};
        stats_add(&st, f, t1 - t0, t2 - f->t_recv);
        stream_release(f, &ack);

        if (cfg->exit_after && ++shown >= cfg->exit_after) {
            display_wait_vblank();
            display_wait_vblank();
            emu_screenshot();
            g_running = 0;
        }
    }
    int err = ui->switch_transport ? 1 : stream_error();
    input_run = 0;
    if (input_thid >= 0) {
        SceUInt timeout = 500 * 1000;
        sceKernelWaitThreadEnd(input_thid, &timeout);
        sceKernelDeleteThread(input_thid);
    }
    stream_stop();
    return err;
}

int main(int argc, char *argv[])
{
    setup_callbacks();
    sceKernelChangeThreadPriority(0, DECODE_PRIO);
    scePowerSetClockFrequency(333, 333, 166);
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    display_init();
    status("PSPStream v0.3");

    char dir[192], err[128];
    app_dir(argc > 0 ? argv[0] : NULL, dir, sizeof(dir));
    ps_config_t cfg;
    if (config_load(&cfg, dir, err, sizeof(err)) < 0) {
        status(err);
        status("Crie o server.txt com o IP do PC (veja o README).");
        wait_exit();
    }
    display_console("Servidor: %s:%d", cfg.host, cfg.port);

    int r;
    if ((r = net_init()) < 0) {
        display_console("Erro ao iniciar a rede: 0x%08X", r);
        wait_exit();
    }
    display_console("Conectando ao Wi-Fi (perfil %d)...", cfg.wifi_profile);
    char ip[32];
    if ((r = net_connect_ap(cfg.wifi_profile, ip, sizeof(ip), status, &g_running)) < 0) {
        display_console("Falha no Wi-Fi: 0x%08X", r);
        status("O perfil existe? A chave WLAN esta ligada?");
        wait_exit();
    }
    display_console("IP do PSP: %s", ip);
    net_ap_info_t ap;
    net_ap_info(&ap);
    display_console("Sinal %d%%, canal %d", ap.strength, ap.channel);
    if (ap.power_save == 1) {
        status("AVISO: 'Economia de energia WLAN' esta LIGADA.");
        status("  Ela desliga o radio entre beacons e aumenta muito a latencia.");
        status("  Desligue em Ajustes > Ajustes de economia de energia.");
    }

    if (decoder_init(cfg.decoder) < 0) {
        display_console("Decoder falhou: %s", decoder_error());
        wait_exit();
    }
    if (cfg.decoder != DEC_SW && decoder_kind() != DEC_HW)
        display_console("sceJpeg indisponivel (%s), usando software", decoder_error());
    display_console("Decoder: %s", decoder_name());

    ui_t ui;
    memset(&ui, 0, sizeof(ui));
    ui.overlay = cfg.overlay;
    ui.vsync = cfg.vsync;
    ui.prefetch = cfg.prefetch;
    ui.udp = cfg.udp;
    input_enabled = cfg.input;

    while (g_running) {
        if (!net_ap_connected()) {
            display_console("Wi-Fi caiu. Reconectando (perfil %d)...", cfg.wifi_profile);
            sceNetApctlDisconnect();
            if (net_connect_ap(cfg.wifi_profile, ip, sizeof(ip), status, &g_running) < 0) {
                for (int i = 0; i < 30 && g_running; i++) /* tenta de novo em 3 s */
                    sceKernelDelayThread(100 * 1000);
                continue;
            }
            display_console("IP do PSP: %s", ip);
        }
        struct sockaddr_in dest;
        int sock;
        if (ui.udp) {
            display_console("Conectando ao PC %s:%d via UDP...", cfg.host, cfg.port);
            sock = net_open_udp(cfg.host, cfg.port, cfg.rcvbuf_kb, &dest);
        } else {
            display_console("Conectando ao PC %s:%d via TCP...", cfg.host, cfg.port);
            sock = net_connect_server(cfg.host, cfg.port, cfg.rcvbuf_kb);
        }
        if (sock < 0) {
            status("Sem conexao. Servidor rodando? Firewall liberado?");
            for (int i = 0; i < 20 && g_running; i++) /* tenta de novo em 2 s */
                sceKernelDelayThread(100 * 1000);
            continue;
        }
        g_sock = sock;
        int e = run_stream(sock, &dest, &cfg, &ui);
        g_sock = -1;
        close(sock);
        if (e == 1) {
            ui.udp = !ui.udp;
            display_console("Trocando para %s...", ui.udp ? "UDP" : "TCP");
        } else if (g_running) {
            display_console("Conexao perdida (%d). Reconectando...", e);
        }
    }

    decoder_term();
    net_term();
    sceKernelExitGame();
    return 0;
}
