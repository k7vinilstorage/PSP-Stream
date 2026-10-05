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
 *   círculo   = vsync      X        = prefetch auto -> sim -> nao
 *   cima      = som do PC liga/desliga (UDP)
 *   L         = transporte TCP/UDP (reconecta)
 *   R         = tela de configuração (menu.c)
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

#include "audio.h"
#include "config.h"
#include "decode.h"
#include "display.h"
#include "menu.h"
#include "net.h"
#include "protocol.h"
#include "stream.h"
#include "version.h"

PSP_MODULE_INFO("PSPStream", PSP_MODULE_USER, 1, 1);
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

enum { ACT_OVERLAY, ACT_DECODER, ACT_VSYNC, ACT_PREFETCH, ACT_TRANSPORT, ACT_CONFIG, ACT_AUDIO, ACT_COUNT };
static const uint32_t act_button[ACT_COUNT] = {PSP_CTRL_TRIANGLE, PSP_CTRL_SQUARE, PSP_CTRL_CIRCLE, PSP_CTRL_CROSS,
                                               PSP_CTRL_LTRIGGER, PSP_CTRL_RTRIGGER, PSP_CTRL_UP};
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
    /* A thread nasce de novo a cada reconexão: botões que já estavam
     * segurados (ex.: SELECT+START+L que pediu a troca de transporte) não
     * contam como apertados agora, senão a troca se repete enquanto o
     * atalho estiver segurado. */
    SceCtrlData start;
    if (sceCtrlPeekBufferPositive(&start, 1) > 0)
        prev_raw = start.Buttons & FORWARD_MASK;
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
    int overlay, vsync, prefetch, udp, audio;
    int early_auto;       /* early_kb=auto: o overlay mostra o valor calculado */
    int switch_transport; /* atalho L: reconectar com o outro transporte */
    int open_config;      /* atalho R: parar o stream e abrir a configuração */
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
            case ACT_PREFETCH: /* auto -> sim -> nao -> auto: compara os três ao vivo */
                ui->prefetch = ui->prefetch == PREFETCH_AUTO ? 1 : ui->prefetch ? 0 : PREFETCH_AUTO;
                stream_set_prefetch(ui->prefetch);
                snprintf(msg, sizeof(msg), "prefetch: %s",
                         ui->prefetch == PREFETCH_AUTO ? "auto" : ui->prefetch ? "sim (antecipado)" : "nao (depois de exibir)");
                toast(ui, msg);
                break;
            case ACT_AUDIO:
                ui->audio = !ui->audio;
                audio_set_enabled(ui->audio);
                stream_set_audio(ui->audio); /* sem o pedido de som, o PC para de mandar */
                snprintf(msg, sizeof(msg), "som: %s%s", ui->audio ? "ligado" : "desligado",
                         ui->audio && !ui->udp ? " (so no UDP)" : "");
                toast(ui, msg);
                break;
            case ACT_TRANSPORT:
                ui->switch_transport = 1;
                break;
            case ACT_CONFIG:
                ui->open_config = 1;
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
        if (ui->udp) {
            unsigned sel, poll, live, live_min;
            int polling;
            stream_ping(&sel, &poll, &polling, &live, &live_min);
            display_text(0, 2, 0xFF00FF00, "perdidos %u nack %u repet %u idr %u ping %.1f ms (min %.1f, ini %.1f %s)",
                         stream_lost(), stream_nacks(), stream_retries(), stream_idr_requests(), live / 1000.0f,
                         live_min / 1000.0f, (polling ? poll : sel) / 1000.0f, polling ? "poll" : "sel");
            unsigned early = stream_early();
            if (!stream_prefetch_on())
                display_text(0, 3, 0xFF00FF00, "pede o proximo depois de exibir (sem prefetch)");
            else if (stream_p_mode() && ui->prefetch == PREFETCH_AUTO)
                display_text(0, 3, 0xFF00FF00, "pede ate 2 frames a frente quando o decode comeca (auto)");
            else if (early)
                display_text(0, 3, 0xFF00FF00, "pede o proximo faltando %.1f KB%s", early / 1024.0f,
                             ui->early_auto ? " (auto)" : "");
            else
                display_text(0, 3, 0xFF00FF00, "pede o proximo no fim do frame");
            audio_stats_t a;
            audio_get_stats(&a);
            if (!ui->audio)
                display_text(0, 4, 0xFF00FF00, "som desligado (SELECT+START+cima)");
            else if (a.error < 0)
                display_text(0, 4, 0xFF00FF00, "som: erro no canal de audio 0x%08X", a.error);
            else if (!a.rate)
                display_text(0, 4, 0xFF00FF00, "som: esperando o PC (servidor sem --no-audio?)");
            else
                display_text(0, 4, 0xFF00FF00, "som %.1f kHz buf %d ms (alvo %d) perdidos %u vazio %u pulos %u",
                             a.rate / 1000.0f, a.buffered_ms, a.target_ms, a.lost, a.underruns, a.skips);
        }
    }
    if (ui->toast_until && (int)(ui->toast_until - now_us()) > 0)
        display_text(0, 33, 0xFF00FFFF, "%s", ui->toast);
}

/* bench=1: decodifica o mesmo frame N vezes com cada decoder e mostra o tempo
 * médio. Compara hw x sw no PSP real sem a rede no meio. */
static void decode_bench(const ps_frame_t *f)
{
    if (decoder_is_h264(f->data, f->size)) { /* hw x sw só existe para JPEG */
        display_console("Benchmark de decode: so para JPEG (o servidor esta em H.264).");
        sceKernelDelayThread(2 * 1000 * 1000);
        return;
    }
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

/* Um stream completo, até a conexão cair, o usuário sair, trocar de
 * transporte (devolve 1) ou pedir a configuração (devolve 2). */
static int run_stream(int sock, const struct sockaddr_in *dest, const ps_config_t *cfg, ui_t *ui)
{
    input_udp = ui->udp;
    ui->switch_transport = 0;
    ui->open_config = 0;
    stream_set_h264(cfg->h264);
    stream_set_h264p(cfg->h264p);
    stream_set_audio(ui->audio);
    if (ui->udp) /* o som só vem pelo UDP */
        audio_start(ui->audio);
    int early = cfg->early_kb < 0 ? STREAM_EARLY_AUTO : cfg->early_kb * 1024;
    if (stream_start(sock, ui->udp, dest, ui->prefetch, early, cfg->rxwait, &g_running) < 0) {
        status("Erro ao iniciar a thread de rede");
        audio_stop();
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
    static int bench_done; /* uma vez por execução, não a cada reconexão */
    int bench_pending = cfg->bench && !bench_done;
    unsigned t_start = now_us();
    unsigned t_wifi = 0;
    int waiting_msg = 0;
    ui->clear = 3;

    while (g_running) {
        apply_menu(ui, seen);
        if (ui->switch_transport || ui->open_config)
            break;
        if (now_us() - t_wifi > 2 * 1000 * 1000) { /* sinal e economia de energia vão no log do servidor */
            net_ap_info_t ap;
            net_ap_info(&ap);
            stream_set_wifi(ap.strength, ap.power_save == 1 ? PS_WIFI_POWER_SAVE : 0);
            t_wifi = now_us();
            /* como um player de vídeo: sem isso, o modo de espera automático
             * (Ajustes de economia de energia) suspende o PSP no meio do stream
             * se nenhum botão for apertado, e a tela também escurece */
            scePowerTick(PSP_POWER_TICK_ALL);
        }

        ps_frame_t *f = stream_take(100 * 1000);
        if (!f) {
            if (stream_error())
                break;
            /* No UDP não há "conexão": avisa se o PC não responde. */
            if (ui->udp && !stream_completed() && now_us() - t_start > 3 * 1000 * 1000 && !waiting_msg) {
                status("Sem resposta do PC via UDP. Servidor rodando?");
                status("Firewall do PC liberado? Rode no PC: pspstream.py --check");
                status("SELECT + START + R: tela de configuracao (IP, procurar o PC)");
                waiting_msg = 1;
            }
            continue;
        }

        if (bench_pending) {
            bench_pending = 0;
            bench_done = 1;
            decode_bench(f);
            ui->clear = 3;
            /* Esse frame ficou ~6 s parado no benchmark: sem ack, para não
             * contaminar as estatísticas do servidor. */
            stream_release(f, NULL);
            continue;
        }

        /* Frames P: um frame se perdeu e a corrente quebrou. Os P seguintes
         * ficariam com a imagem errada até o próximo IDR (já pedido). */
        int pk = decoder_h264_packet(f->data, f->size);
        if (pk == H264_P && stream_frame_needs_idr(f->frame_no)) {
            stream_release(f, NULL);
            continue;
        }

        unsigned t0 = now_us();
        uint32_t *dst = display_back();
        if (ui->clear > 0) {
            display_clear_back();
            ui->clear--;
        }
        int w = 0, h = 0;
        if (decoder_decode(f->data, f->size, dst, &w, &h) < 0) {
            if (pk)
                stream_request_idr(f->frame_no + 1);
            printf("frame %u: %s\n", (unsigned)f->frame_no, decoder_error());
            if (!(ui->toast_until && (int)(ui->toast_until - now_us()) > 0))
                toast(ui, decoder_error()); /* na tela: senão só aparece no PSPLink */
            draw_overlay(ui, &st);
            display_writeback();
            stream_release(f, NULL);
            continue;
        }
        unsigned t1 = now_us();
        if (pk == H264_P_IDR)
            stream_idr_done(f->frame_no);
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
                        tenth_ms(t1 - t0),
                        (int)(f->t_first - f->t_req) > 0 ? tenth_ms(f->t_first - f->t_req) : 0,
                        tenth_ms(f->t_recv - f->t_first), f->idle_t};
        stats_add(&st, f, t1 - t0, t2 - f->t_recv);
        stream_release(f, &ack);

        if (cfg->exit_after && ++shown >= cfg->exit_after) {
            display_wait_vblank();
            display_wait_vblank();
            emu_screenshot();
            g_running = 0;
        }
    }
    int err = ui->switch_transport ? 1 : ui->open_config ? 2 : stream_error();
    input_run = 0;
    if (input_thid >= 0) {
        SceUInt timeout = 500 * 1000;
        sceKernelWaitThreadEnd(input_thid, &timeout);
        sceKernelDeleteThread(input_thid);
    }
    stream_stop();
    audio_stop();
    return err;
}

/* ---- Wi-Fi e tela de configuração ---- */
static int wifi_profile_up; /* perfil conectado agora (0 = nenhum) */

static int wifi_ensure(int profile)
{
    if (wifi_profile_up == profile && net_ap_connected())
        return 0;
    if (wifi_profile_up) { /* caiu, ou o perfil mudou na configuração */
        display_console("Desconectando do Wi-Fi (perfil %d)...", wifi_profile_up);
        sceNetApctlDisconnect();
        wifi_profile_up = 0;
    }
    display_console("Conectando ao Wi-Fi (perfil %d)...", profile);
    char ip[32];
    int r = net_connect_ap(profile, ip, sizeof(ip), status, &g_running);
    if (r < 0)
        return r;
    wifi_profile_up = profile;
    display_console("IP do PSP: %s", ip);
    net_ap_info_t ap;
    net_ap_info(&ap);
    display_console("Sinal %d%%, canal %d", ap.strength, ap.channel);
    if (ap.power_save == 1) {
        status("AVISO: 'Economia de energia WLAN' esta LIGADA.");
        status("  Ela desliga o radio entre beacons e aumenta muito a latencia.");
        status("  Desligue em Ajustes > Ajustes de economia de energia.");
    }
    return 0;
}

static int menu_discover(ps_config_t *cfg, char *msg, int len)
{
    display_console_clear();
    int r = wifi_ensure(cfg->wifi_profile);
    if (r < 0) {
        snprintf(msg, len, "Falha no Wi-Fi (perfil %d): 0x%08X", cfg->wifi_profile, r);
        return -1;
    }
    display_console("Procurando o servidor na porta %d...", cfg->port);
    char found[32];
    if (net_discover(cfg->port, found, sizeof(found), 2 * 1000 * 1000) < 0) {
        snprintf(msg, len, "Ninguem respondeu na porta %d. Servidor rodando? Firewall?", cfg->port);
        return -1;
    }
    snprintf(cfg->host, sizeof(cfg->host), "%s", found);
    snprintf(msg, len, "PC achado: %s (START salva e conecta)", found);
    return 0;
}

/* Espera até us; 1 se START foi apertado (abrir a configuração). */
static int wait_or_menu(unsigned us)
{
    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    unsigned prev = pad.Buttons, t0 = now_us();
    while (g_running && now_us() - t0 < us) {
        sceKernelDelayThread(50 * 1000);
        sceCtrlPeekBufferPositive(&pad, 1);
        if (pad.Buttons & ~prev & PSP_CTRL_START)
            return 1;
        prev = pad.Buttons;
    }
    return 0;
}

static void apply_config(const ps_config_t *cfg, ui_t *ui, int decoder_ready)
{
    ui->overlay = cfg->overlay;
    ui->early_auto = cfg->early_kb < 0;
    ui->vsync = cfg->vsync;
    ui->prefetch = cfg->prefetch;
    ui->udp = cfg->udp;
    ui->audio = cfg->audio;
    input_enabled = cfg->input;
    if (decoder_ready)
        decoder_select(cfg->decoder == DEC_SW ? DEC_SW : DEC_HW);
}

int main(int argc, char *argv[])
{
    setup_callbacks();
    sceKernelChangeThreadPriority(0, DECODE_PRIO);
    scePowerSetClockFrequency(333, 333, 166);
    sceCtrlSetSamplingCycle(0);
    sceCtrlSetSamplingMode(PSP_CTRL_MODE_ANALOG);
    display_init();
    status("PSPStream v" PSPSTREAM_VERSION);

    char dir[192], err[128];
    app_dir(argc > 0 ? argv[0] : NULL, dir, sizeof(dir));
    ps_config_t cfg;
    int loaded = config_load(&cfg, dir, err, sizeof(err)) == 0;

    int r;
    if ((r = net_init()) < 0) {
        display_console("Erro ao iniciar a rede: 0x%08X", r);
        wait_exit();
    }

    /* Tela de configuração: na abertura (conecta sozinha em menu_wait s se o
     * IP já está configurado; sem server.txt, fica esperando), com START
     * quando a conexão falha e com SELECT + START + R no stream. Os testes
     * no emulador (exit_after) pulam a tela se o IP já está no server.txt. */
    static const menu_hooks_t hooks = {menu_discover, emu_screenshot};
    int need_menu = !loaded || (!cfg.exit_after && (cfg.menu_wait > 0 || cfg.menu_shot));
    int countdown = loaded ? cfg.menu_wait : 0;
    const char *menu_msg = loaded ? NULL : err;
    int decoder_ready = 0;
    ui_t ui;
    memset(&ui, 0, sizeof(ui));
    apply_config(&cfg, &ui, 0);

    while (g_running) {
        if (need_menu) {
            if (menu_run(&cfg, dir, countdown, menu_msg, &hooks, &g_running) == MENU_QUIT)
                break;
            need_menu = 0;
            countdown = 0;
            menu_msg = NULL;
            apply_config(&cfg, &ui, decoder_ready);
            display_console_clear();
            status("PSPStream v" PSPSTREAM_VERSION);
        }
        if (!decoder_ready) {
            if (decoder_init(cfg.decoder) < 0) {
                display_console("Decoder falhou: %s", decoder_error());
                wait_exit();
            }
            if (cfg.decoder != DEC_SW && decoder_kind() != DEC_HW)
                display_console("sceJpeg indisponivel (%s), usando software", decoder_error());
            decoder_ready = 1;
        }
        if ((r = wifi_ensure(cfg.wifi_profile)) < 0) {
            display_console("Falha no Wi-Fi: 0x%08X", r);
            status("O perfil existe? A chave WLAN esta ligada?");
            status("START: tela de configuracao (ou espere: tenta de novo em 3 s)");
            if (wait_or_menu(3 * 1000 * 1000))
                need_menu = 1;
            continue;
        }
        struct sockaddr_in dest;
        int sock;
        display_console("Servidor: %s:%d, decoder %s", cfg.host, cfg.port, decoder_name());
        if (ui.udp) {
            display_console("Conectando ao PC %s:%d via UDP...", cfg.host, cfg.port);
            sock = net_open_udp(cfg.host, cfg.port, cfg.rcvbuf_kb, &dest);
        } else {
            display_console("Conectando ao PC %s:%d via TCP...", cfg.host, cfg.port);
            sock = net_connect_server(cfg.host, cfg.port, cfg.rcvbuf_kb);
        }
        if (sock < 0) {
            status("Sem conexao. Servidor rodando? Firewall liberado?");
            status("START: tela de configuracao (ou espere: tenta de novo em 2 s)");
            if (wait_or_menu(2 * 1000 * 1000))
                need_menu = 1;
            continue;
        }
        g_sock = sock;
        int e = run_stream(sock, &dest, &cfg, &ui);
        g_sock = -1;
        close(sock);
        if (e == 1) {
            ui.udp = !ui.udp;
            display_console("Trocando para %s...", ui.udp ? "UDP" : "TCP");
        } else if (e == 2) {
            /* a tela mostra o estado atual, inclusive o que os atalhos mudaram */
            cfg.overlay = ui.overlay;
            cfg.vsync = ui.vsync;
            cfg.prefetch = ui.prefetch;
            cfg.udp = ui.udp;
            cfg.audio = ui.audio;
            need_menu = 1;
        } else if (g_running) {
            display_console("Conexao perdida (%d). Reconectando...", e);
        }
    }

    if (decoder_ready)
        decoder_term();
    net_term();
    sceKernelExitGame();
    return 0;
}
