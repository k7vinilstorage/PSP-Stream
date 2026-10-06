/*
 * server.txt: a primeira linha útil é o IP do PC, opcionalmente com :porta.
 * As linhas seguintes podem ter chave=valor. '#' inicia comentário.
 *
 *   192.168.1.100
 *   wifi_profile=1
 *   decoder=auto     (auto | hw | sw)
 *   vsync=1
 *   prefetch=auto    (auto = sim; frames P: até 2 à frente, pedidos quando o decode começa | 1 | 0)
 *   overlay=1
 *   rcvbuf=64        (KB)
 *   bench=0          (1 = mede decode hw x sw no primeiro frame)
 *   input=1          (1 = controles do PSP viram teclado/mouse no PC)
 *   transport=udp    (udp | tcp)
 *   early_kb=auto    (UDP: pede o próximo frame quando faltar isso do atual; auto = ida e volta x vazão, 0 = só no fim)
 *   rxwait=auto      (UDP: auto | select | poll; auto mede os dois no início e fica com o mais rápido)
 *   h264=1           (1 = aceita H.264 do servidor rodando com --codec h264)
 *   h264p=1          (1 = aceita também frames P, do servidor com --codec h264p)
 *   menu_wait=3      (s com a tela de configuração aberta antes de conectar sozinho; 0 = direto)
 *   audio=1          (1 = toca o som do PC; só pelo UDP)
 *   lang=en          (en | pt: idioma das telas do PSP)
 */
#include "config.h"
#include "decode.h"
#include "lang.h"
#include "protocol.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int ps_lang_pt;

static char *trim(char *s)
{
    while (*s && isspace((unsigned char)*s))
        s++;
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1]))
        *--end = '\0';
    return s;
}

static void set_key(ps_config_t *cfg, const char *key, const char *value)
{
    int v = atoi(value);
    if (!strcmp(key, "host"))
        snprintf(cfg->host, sizeof(cfg->host), "%s", value);
    else if (!strcmp(key, "port"))
        cfg->port = v;
    else if (!strcmp(key, "wifi_profile"))
        cfg->wifi_profile = v;
    else if (!strcmp(key, "decoder"))
        cfg->decoder = !strcmp(value, "hw") ? DEC_HW : !strcmp(value, "sw") ? DEC_SW : DEC_AUTO;
    else if (!strcmp(key, "vsync"))
        cfg->vsync = v;
    else if (!strcmp(key, "prefetch"))
        cfg->prefetch = !strcmp(value, "auto") ? PREFETCH_AUTO : v != 0;
    else if (!strcmp(key, "overlay"))
        cfg->overlay = v;
    else if (!strcmp(key, "rcvbuf"))
        cfg->rcvbuf_kb = v;
    else if (!strcmp(key, "bench"))
        cfg->bench = v;
    else if (!strcmp(key, "input"))
        cfg->input = v;
    else if (!strcmp(key, "transport"))
        cfg->udp = strcmp(value, "tcp") != 0;
    else if (!strcmp(key, "early_kb"))
        cfg->early_kb = !strcmp(value, "auto") ? -1 : v;
    else if (!strcmp(key, "rxwait"))
        cfg->rxwait = !strcmp(value, "select") ? RXWAIT_SELECT : !strcmp(value, "poll") ? RXWAIT_POLL : RXWAIT_AUTO;
    else if (!strcmp(key, "h264"))
        cfg->h264 = v;
    else if (!strcmp(key, "h264p"))
        cfg->h264p = v;
    else if (!strcmp(key, "exit_after"))
        cfg->exit_after = v;
    else if (!strcmp(key, "menu_wait"))
        cfg->menu_wait = v;
    else if (!strcmp(key, "menu_shot"))
        cfg->menu_shot = v;
    else if (!strcmp(key, "audio"))
        cfg->audio = v;
    else if (!strcmp(key, "lang"))
        cfg->lang_pt = !strncmp(value, "pt", 2);
}

int config_load(ps_config_t *cfg, const char *dir, char *err, int errlen)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->port = PS_DEFAULT_PORT;
    cfg->wifi_profile = 1;
    cfg->decoder = DEC_AUTO;
    cfg->vsync = 1;
    cfg->prefetch = PREFETCH_AUTO;
    cfg->overlay = 1;
    cfg->rcvbuf_kb = 64;
    cfg->input = 1;
    cfg->udp = 1; /* medido no PSP-3000: UDP sem travadas, TCP com várias */
    cfg->early_kb = -1; /* auto; valores fixos de 6-14 KB (JPEG, PSP-3000) pediam cedo demais */
    cfg->h264 = 1;
    cfg->h264p = 1;
    cfg->menu_wait = 3;
    cfg->audio = 1;

    char path[256];
    snprintf(path, sizeof(path), "%sserver.txt", dir);
    FILE *f = fopen(path, "r");
    ps_lang_pt = 0;
    if (!f) {
        snprintf(err, errlen, T("could not find %s", "nao achei %s"), path);
        return -1;
    }

    char line[128];
    while (fgets(line, sizeof(line), f)) {
        char *hash = strchr(line, '#');
        if (hash)
            *hash = '\0';
        char *s = trim(line);
        if (!*s)
            continue;
        char *eq = strchr(s, '=');
        if (eq) {
            *eq = '\0';
            set_key(cfg, trim(s), trim(eq + 1));
        } else if (!cfg->host[0]) {
            char *colon = strchr(s, ':');
            if (colon) {
                *colon = '\0';
                cfg->port = atoi(colon + 1);
            }
            snprintf(cfg->host, sizeof(cfg->host), "%s", s);
        }
    }
    fclose(f);
    ps_lang_pt = cfg->lang_pt;

    if (!cfg->host[0]) {
        snprintf(err, errlen, T("%s has no PC IP", "%s nao tem o IP do PC"), path);
        return -1;
    }
    if (cfg->port <= 0 || cfg->port > 65535)
        cfg->port = PS_DEFAULT_PORT;
    if (cfg->rcvbuf_kb < 8 || cfg->rcvbuf_kb > 256)
        cfg->rcvbuf_kb = 64;
    if (cfg->early_kb < -1 || cfg->early_kb > 64)
        cfg->early_kb = -1;
    if (cfg->menu_wait < 0 || cfg->menu_wait > 30)
        cfg->menu_wait = 3;
    return 0;
}

static const char *onoff(int v)
{
    return v ? "1" : "0";
}

int config_save(const ps_config_t *cfg, const char *dir, char *err, int errlen)
{
    char path[256], tmp[260];
    snprintf(path, sizeof(path), "%sserver.txt", dir);
    snprintf(tmp, sizeof(tmp), "%s.new", path);
    FILE *f = fopen(tmp, "w");
    if (!f) {
        snprintf(err, errlen, T("could not write %s", "nao consegui gravar %s"), tmp);
        return -1;
    }
    char early[16];
    if (cfg->early_kb < 0)
        snprintf(early, sizeof(early), "auto");
    else
        snprintf(early, sizeof(early), "%d", cfg->early_kb);
    fprintf(f, "%s\n", T("# PSPStream: written by the PSP settings screen (options: project wiki, Using-the-PSP page)",
                         "# PSPStream: gravado pela tela de configuracao do PSP (opcoes: wiki do projeto, pagina Using-the-PSP)"));
    if (cfg->port == PS_DEFAULT_PORT)
        fprintf(f, "%s\n", cfg->host);
    else
        fprintf(f, "%s:%d\n", cfg->host, cfg->port);
    fprintf(f, "wifi_profile=%d\n", cfg->wifi_profile);
    fprintf(f, "transport=%s\n", cfg->udp ? "udp" : "tcp");
    fprintf(f, "h264=%s\n", onoff(cfg->h264));
    fprintf(f, "h264p=%s\n", onoff(cfg->h264p));
    fprintf(f, "decoder=%s\n", cfg->decoder == DEC_HW ? "hw" : cfg->decoder == DEC_SW ? "sw" : "auto");
    fprintf(f, "vsync=%s\n", onoff(cfg->vsync));
    fprintf(f, "overlay=%s\n", onoff(cfg->overlay));
    fprintf(f, "input=%s\n", onoff(cfg->input));
    fprintf(f, "audio=%s\n", onoff(cfg->audio));
    fprintf(f, "prefetch=%s\n", cfg->prefetch == PREFETCH_AUTO ? "auto" : onoff(cfg->prefetch));
    fprintf(f, "early_kb=%s\n", early);
    fprintf(f, "rxwait=%s\n", cfg->rxwait == RXWAIT_SELECT ? "select" : cfg->rxwait == RXWAIT_POLL ? "poll" : "auto");
    fprintf(f, "rcvbuf=%d\n", cfg->rcvbuf_kb);
    fprintf(f, "bench=%s\n", onoff(cfg->bench));
    fprintf(f, "menu_wait=%d\n", cfg->menu_wait);
    fprintf(f, "lang=%s\n", cfg->lang_pt ? "pt" : "en");
    int ok = !ferror(f);
    if (fclose(f) != 0 || !ok) {
        snprintf(err, errlen, T("error writing %s", "erro ao gravar %s"), tmp);
        remove(tmp);
        return -1;
    }
    /* troca só depois de gravar inteiro: se o PSP desligar no meio, o antigo fica */
    remove(path);
    if (rename(tmp, path) != 0) {
        snprintf(err, errlen, T("could not rename %s", "nao consegui renomear %s"), tmp);
        return -1;
    }
    return 0;
}
