/*
 * Tela de configuração no próprio PSP: IP e porta do PC (editados dígito a
 * dígito), perfil de Wi-Fi do XMB, busca do PC na rede e as opções do
 * server.txt. Grava o server.txt (START) ou só conecta (O).
 *
 * Texto com a fonte 8x8 do pspDebugScreen (60 x 34 caracteres, só ASCII),
 * desenhado no buffer de trás e trocado no vblank.
 */
#include "menu.h"
#include "decode.h"
#include "display.h"
#include "protocol.h"
#include "version.h"

#include <pspctrl.h>
#include <pspkernel.h>
#include <psputility_netparam.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define C_TITLE 0xFFFFFF00u /* ciano (ABGR) */
#define C_TEXT 0xFFFFFFFFu
#define C_DIM 0xFF909090u
#define C_SEL 0xFF00FFFFu   /* amarelo */
#define C_EDIT 0xFF4080FFu  /* laranja */
#define C_OK 0xFF00FF00u
#define C_BAD 0xFF4040FFu

#define VALUE_X 30
#define REPEAT_DELAY_US (350 * 1000)
#define REPEAT_US (70 * 1000)
#define MAX_PROFILES 10

enum {
    IT_HOST, IT_PORT, IT_WIFI, IT_FIND, IT_TRANSPORT, IT_H264, IT_H264P, IT_DECODER, IT_VSYNC, IT_OVERLAY,
    IT_INPUT, IT_AUDIO, IT_PREFETCH, IT_EARLY, IT_RXWAIT, IT_SAVE, IT_CONNECT, IT_QUIT, IT_COUNT
};

static const char *const labels[IT_COUNT] = {
    "IP do PC", "Porta", "Perfil de Wi-Fi", "[ Procurar o PC na rede ]", "Transporte", "H.264",
    "H.264 com frames P", "Decoder do JPEG", "Vsync", "Overlay (FPS, tempos)", "Controles para o PC",
    "Som do PC (UDP)", "Prefetch", "Pedido antecipado (UDP)", "Espera de pacotes (UDP)", "[ Salvar e conectar ]",
    "[ Conectar sem salvar ]", "[ Sair ]",
};

static const char *const help[IT_COUNT] = {
    "IP do PC que roda o servidor. X: editar",
    "Porta do servidor (padrao 5123). X: editar",
    "Perfil salvo em Ajustes > Ajustes de rede",
    "Liga o Wi-Fi e procura o servidor (ele precisa estar rodando)",
    "UDP: recomendado. TCP: so se o UDP nao passar no roteador",
    "Aceita H.264 (metade dos bytes do JPEG; o servidor decide)",
    "Aceita frames P: ~10x menos bytes (servidor com --codec h264p)",
    "auto = hardware (sceJpeg) com reserva em software",
    "Troca de imagem no vblank: sem rasgo, +0 a 16 ms",
    "Mostra FPS, KB e tempos no canto da tela",
    "Botoes do PSP viram controle/teclado no PC",
    "Toca o som do PC (~46 KB/s). No stream: SELECT+START+cima",
    "auto: frames P pedidos quando o decode comeca. 0: depois de exibir",
    "Pede o proximo frame antes do fim do atual (auto = medido)",
    "auto mede select e consulta no inicio e usa o mais rapido",
    "Grava o server.txt e conecta (START faz o mesmo)",
    "Conecta com estas opcoes sem gravar (O faz o mesmo)",
    "Volta para o XMB",
};

static const int early_values[] = {-1, 0, 2, 3, 4, 6, 8};
#define N_EARLY ((int)(sizeof(early_values) / sizeof(early_values[0])))

/* ---- IP e porta como dígitos ---- */

static int parse_ip(const char *s, int oct[4])
{
    unsigned a, b, c, d;
    char tail;
    if (sscanf(s, "%u.%u.%u.%u%c", &a, &b, &c, &d, &tail) != 4 || a > 255 || b > 255 || c > 255 || d > 255)
        return -1;
    oct[0] = a;
    oct[1] = b;
    oct[2] = c;
    oct[3] = d;
    return 0;
}

typedef struct {
    int active;    /* editando IT_HOST ou IT_PORT */
    int item;
    char d[12];    /* dígitos '0'-'9' */
    int n, cursor;
} editor_t;

static void edit_begin(editor_t *e, int item, const ps_config_t *cfg)
{
    memset(e, 0, sizeof(*e));
    e->active = 1;
    e->item = item;
    if (item == IT_HOST) {
        int oct[4] = {192, 168, 1, 100};
        parse_ip(cfg->host, oct);
        char buf[16];
        snprintf(buf, sizeof(buf), "%03d%03d%03d%03d", oct[0], oct[1], oct[2], oct[3]);
        memcpy(e->d, buf, 12);
        e->n = 12;
    } else {
        char buf[8];
        snprintf(buf, sizeof(buf), "%05d", cfg->port);
        memcpy(e->d, buf, 5);
        e->n = 5;
    }
}

static int digits_value(const char *d, int n)
{
    int v = 0;
    for (int i = 0; i < n; i++)
        v = v * 10 + (d[i] - '0');
    return v;
}

static void edit_end(editor_t *e, ps_config_t *cfg)
{
    if (e->item == IT_HOST) {
        int oct[4];
        for (int k = 0; k < 4; k++) {
            oct[k] = digits_value(e->d + 3 * k, 3);
            if (oct[k] > 255)
                oct[k] = 255;
        }
        snprintf(cfg->host, sizeof(cfg->host), "%d.%d.%d.%d", oct[0], oct[1], oct[2], oct[3]);
    } else {
        int p = digits_value(e->d, 5);
        cfg->port = p >= 1 && p <= 65535 ? p : PS_DEFAULT_PORT;
    }
    e->active = 0;
}

/* ---- perfis de Wi-Fi do XMB ---- */

static int profile_exists(int id)
{
    return sceUtilityCheckNetParam(id) == 0;
}

static void profile_name(int id, char *out, int len)
{
    netData data;
    memset(&data, 0, sizeof(data));
    if (!profile_exists(id) || sceUtilityGetNetParam(id, PSP_NETPARAM_NAME, &data) < 0) {
        snprintf(out, len, "%d: (nao existe)", id);
        return;
    }
    data.asString[sizeof(data.asString) - 1] = 0;
    snprintf(out, len, "%d: %.24s", id, data.asString);
}

static int next_profile(int id, int dir)
{
    for (int k = 1; k <= MAX_PROFILES; k++) {
        int c = (id - 1 + dir * k + 10 * MAX_PROFILES) % MAX_PROFILES + 1;
        if (profile_exists(c))
            return c;
    }
    return id;
}

/* ---- valores ---- */

static void change(ps_config_t *cfg, int item, int dir)
{
    switch (item) {
    case IT_WIFI:
        cfg->wifi_profile = next_profile(cfg->wifi_profile, dir);
        break;
    case IT_TRANSPORT:
        cfg->udp = !cfg->udp;
        break;
    case IT_H264:
        cfg->h264 = !cfg->h264;
        break;
    case IT_H264P:
        cfg->h264p = !cfg->h264p;
        break;
    case IT_DECODER: /* auto -> hw -> sw */
        cfg->decoder = cfg->decoder == DEC_AUTO ? (dir > 0 ? DEC_HW : DEC_SW)
                     : cfg->decoder == DEC_HW   ? (dir > 0 ? DEC_SW : DEC_AUTO)
                                                : (dir > 0 ? DEC_AUTO : DEC_HW);
        break;
    case IT_VSYNC:
        cfg->vsync = !cfg->vsync;
        break;
    case IT_OVERLAY:
        cfg->overlay = !cfg->overlay;
        break;
    case IT_INPUT:
        cfg->input = !cfg->input;
        break;
    case IT_AUDIO:
        cfg->audio = !cfg->audio;
        break;
    case IT_PREFETCH: /* auto -> sim -> nao */
        cfg->prefetch = cfg->prefetch == PREFETCH_AUTO ? (dir > 0 ? 1 : 0)
                      : cfg->prefetch               ? (dir > 0 ? 0 : PREFETCH_AUTO)
                                                    : (dir > 0 ? PREFETCH_AUTO : 1);
        break;
    case IT_EARLY: {
        int k = 0;
        while (k < N_EARLY && early_values[k] != cfg->early_kb)
            k++;
        k = k == N_EARLY ? 0 : (k + dir + N_EARLY) % N_EARLY;
        cfg->early_kb = early_values[k];
        break;
    }
    case IT_RXWAIT:
        cfg->rxwait = (cfg->rxwait + dir + 3) % 3;
        break;
    }
}

static const char *yes(int v)
{
    return v ? "sim" : "nao";
}

static void value_text(const ps_config_t *cfg, int item, char *out, int len)
{
    out[0] = 0;
    switch (item) {
    case IT_HOST:
        snprintf(out, len, "%s", cfg->host[0] ? cfg->host : "(nenhum)");
        break;
    case IT_PORT:
        snprintf(out, len, "%d", cfg->port);
        break;
    case IT_WIFI:
        profile_name(cfg->wifi_profile, out, len);
        break;
    case IT_TRANSPORT:
        snprintf(out, len, "%s", cfg->udp ? "UDP" : "TCP");
        break;
    case IT_H264:
        snprintf(out, len, "%s", yes(cfg->h264));
        break;
    case IT_H264P:
        snprintf(out, len, "%s", yes(cfg->h264p));
        break;
    case IT_DECODER:
        snprintf(out, len, "%s", cfg->decoder == DEC_HW ? "hardware" : cfg->decoder == DEC_SW ? "software" : "auto");
        break;
    case IT_VSYNC:
        snprintf(out, len, "%s", yes(cfg->vsync));
        break;
    case IT_OVERLAY:
        snprintf(out, len, "%s", yes(cfg->overlay));
        break;
    case IT_INPUT:
        snprintf(out, len, "%s", yes(cfg->input));
        break;
    case IT_AUDIO:
        snprintf(out, len, "%s", yes(cfg->audio));
        break;
    case IT_PREFETCH:
        snprintf(out, len, "%s", cfg->prefetch == PREFETCH_AUTO ? "auto (recomendado)" : yes(cfg->prefetch));
        break;
    case IT_EARLY:
        if (cfg->early_kb < 0)
            snprintf(out, len, "auto");
        else if (cfg->early_kb == 0)
            snprintf(out, len, "no fim do frame");
        else
            snprintf(out, len, "faltando %d KB", cfg->early_kb);
        break;
    case IT_RXWAIT:
        snprintf(out, len, "%s", cfg->rxwait == RXWAIT_SELECT ? "select" : cfg->rxwait == RXWAIT_POLL ? "consulta" : "auto");
        break;
    }
}

static int is_action(int item)
{
    return item == IT_FIND || item == IT_SAVE || item == IT_CONNECT || item == IT_QUIT;
}

/* linha de cada item: ações separadas das opções por uma linha em branco */
static int item_row(int item)
{
    int row = 3 + item;
    if (item > IT_FIND)
        row++;
    if (item >= IT_SAVE)
        row++;
    return row;
}

static void draw(const ps_config_t *cfg, const char *dir, int sel, const editor_t *ed, const char *status,
                 uint32_t status_color, int countdown)
{
    display_clear_back();
    display_text(1, 0, C_TITLE, "PSPStream v%s - configuracao", PSPSTREAM_VERSION);
    display_text(1, 1, C_DIM, "%.56sserver.txt", dir);
    for (int i = 0; i < IT_COUNT; i++) {
        int row = item_row(i);
        uint32_t color = i == sel ? C_SEL : C_TEXT;
        display_text(0, row, color, "%c %s", i == sel ? '>' : ' ', labels[i]);
        if (is_action(i))
            continue;
        if (ed->active && ed->item == i) {
            int x = VALUE_X;
            for (int k = 0; k < ed->n; k++) {
                if (ed->item == IT_HOST && k && k % 3 == 0)
                    display_text(x++, row, C_EDIT, ".");
                display_text(x++, row, k == ed->cursor ? C_SEL : C_EDIT, "%c", ed->d[k]);
            }
            display_text(x + 1, row, C_DIM, "<- ->  cima/baixo");
        } else {
            char v[72];
            value_text(cfg, i, v, sizeof(v));
            display_text(VALUE_X, row, color, "%s", v);
        }
    }
    display_text(1, 27, C_DIM, "%s", ed->active ? "Esq/Dir: digito  Cima/Baixo: muda  X: pronto" : help[sel]);
    if (status && status[0])
        display_text(1, 29, status_color, "%.58s", status);
    if (countdown > 0)
        display_text(1, 30, C_OK, "Conectando em %d s... aperte um botao para configurar", countdown);
    display_text(1, 32, C_DIM, "Cima/Baixo: item  Esq/Dir: muda  X: editar/escolher");
    display_text(1, 33, C_DIM, "START: salvar e conectar   O: conectar sem salvar");
    display_flip(1);
}

int menu_run(ps_config_t *cfg, const char *dir, int countdown_s, const char *status_in, const menu_hooks_t *hooks,
             volatile int *running)
{
    editor_t ed;
    memset(&ed, 0, sizeof(ed));
    char status[96] = "";
    uint32_t status_color = C_BAD;
    if (status_in)
        snprintf(status, sizeof(status), "%s", status_in);
    int sel = cfg->host[0] ? IT_SAVE : IT_FIND;
    unsigned t_end = countdown_s > 0 && cfg->host[0] ? sceKernelGetSystemTimeLow() + countdown_s * 1000000u : 0;
    int shown_count = -1, dirty = 1;

    SceCtrlData pad;
    sceCtrlPeekBufferPositive(&pad, 1);
    unsigned prev = pad.Buttons; /* o que já estava apertado ao abrir não conta */
    unsigned held_since = 0, last_repeat = 0;

    while (*running) {
        int count = 0;
        if (t_end) {
            int left = (int)(t_end - sceKernelGetSystemTimeLow());
            if (left <= 0)
                return MENU_CONNECT;
            count = (left + 999999) / 1000000;
        }
        if (dirty || count != shown_count) {
            draw(cfg, dir, sel, &ed, status, status_color, count);
            dirty = 0;
            shown_count = count;
            if (cfg->menu_shot && hooks && hooks->shot) {
                display_wait_vblank();
                display_wait_vblank();
                hooks->shot();
                return MENU_QUIT;
            }
        }

        sceCtrlReadBufferPositive(&pad, 1); /* espera a próxima amostra (vblank) */
        unsigned b = pad.Buttons;
        unsigned pressed = b & ~prev;
        unsigned now = sceKernelGetSystemTimeLow();
        const unsigned dirs = PSP_CTRL_UP | PSP_CTRL_DOWN | PSP_CTRL_LEFT | PSP_CTRL_RIGHT;
        if (pressed & dirs) {
            held_since = now;
            last_repeat = now;
        } else if ((b & dirs) && now - held_since > REPEAT_DELAY_US && now - last_repeat > REPEAT_US) {
            pressed |= b & dirs; /* direcional segurado: repete */
            last_repeat = now;
        }
        prev = b;
        if (!pressed)
            continue;
        if (t_end) { /* qualquer botão cancela a conexão automática */
            t_end = 0;
            dirty = 1;
            continue;
        }
        dirty = 1;

        if (ed.active) {
            if (pressed & PSP_CTRL_LEFT)
                ed.cursor = (ed.cursor + ed.n - 1) % ed.n;
            if (pressed & PSP_CTRL_RIGHT)
                ed.cursor = (ed.cursor + 1) % ed.n;
            if (pressed & PSP_CTRL_UP)
                ed.d[ed.cursor] = ed.d[ed.cursor] == '9' ? '0' : ed.d[ed.cursor] + 1;
            if (pressed & PSP_CTRL_DOWN)
                ed.d[ed.cursor] = ed.d[ed.cursor] == '0' ? '9' : ed.d[ed.cursor] - 1;
            if (pressed & (PSP_CTRL_CROSS | PSP_CTRL_CIRCLE | PSP_CTRL_START)) {
                edit_end(&ed, cfg);
                status[0] = 0;
            }
            continue;
        }

        if (pressed & PSP_CTRL_UP)
            sel = (sel + IT_COUNT - 1) % IT_COUNT;
        if (pressed & PSP_CTRL_DOWN)
            sel = (sel + 1) % IT_COUNT;
        if (pressed & (PSP_CTRL_LEFT | PSP_CTRL_RIGHT))
            change(cfg, sel, pressed & PSP_CTRL_RIGHT ? 1 : -1);

        int act = -1;
        if (pressed & PSP_CTRL_START)
            act = IT_SAVE;
        else if (pressed & PSP_CTRL_CIRCLE)
            act = IT_CONNECT;
        else if (pressed & PSP_CTRL_CROSS) {
            if (sel == IT_HOST || sel == IT_PORT)
                edit_begin(&ed, sel, cfg);
            else if (is_action(sel))
                act = sel;
            else
                change(cfg, sel, 1);
        }
        if (act < 0)
            continue;
        if (act == IT_QUIT)
            return MENU_QUIT;
        if (act == IT_FIND) {
            draw(cfg, dir, sel, &ed, "Procurando o PC na rede...", C_OK, 0);
            char msg[96];
            int r = hooks && hooks->discover ? hooks->discover(cfg, msg, sizeof(msg)) : -1;
            snprintf(status, sizeof(status), "%s", msg);
            status_color = r == 0 ? C_OK : C_BAD;
            if (r == 0)
                sel = IT_SAVE;
            continue;
        }
        int oct[4];
        if (!cfg->host[0] || (parse_ip(cfg->host, oct) == 0 && !oct[0])) {
            snprintf(status, sizeof(status), "Falta o IP do PC: edite ou use Procurar o PC na rede");
            status_color = C_BAD;
            sel = IT_HOST;
            continue;
        }
        if (act == IT_SAVE) {
            char err[96];
            if (config_save(cfg, dir, err, sizeof(err)) < 0) {
                snprintf(status, sizeof(status), "%s", err);
                status_color = C_BAD;
                continue;
            }
        }
        return MENU_CONNECT;
    }
    return MENU_QUIT;
}
