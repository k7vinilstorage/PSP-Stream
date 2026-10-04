/*
 * server.txt: a primeira linha útil é o IP do PC, opcionalmente com :porta.
 * As linhas seguintes podem ter chave=valor. '#' inicia comentário.
 *
 *   192.168.1.100
 *   wifi_profile=1
 */
#include "config.h"
#include "protocol.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
    else if (!strcmp(key, "exit_after"))
        cfg->exit_after = v;
}

int config_load(ps_config_t *cfg, const char *dir, char *err, int errlen)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->port = PS_DEFAULT_PORT;
    cfg->wifi_profile = 1;

    char path[256];
    snprintf(path, sizeof(path), "%sserver.txt", dir);
    FILE *f = fopen(path, "r");
    if (!f) {
        snprintf(err, errlen, "nao achei %s", path);
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

    if (!cfg->host[0]) {
        snprintf(err, errlen, "%s nao tem o IP do PC", path);
        return -1;
    }
    if (cfg->port <= 0 || cfg->port > 65535)
        cfg->port = PS_DEFAULT_PORT;
    return 0;
}
