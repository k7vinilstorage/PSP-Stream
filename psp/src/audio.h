#ifndef PSPSTREAM_AUDIO_H
#define PSPSTREAM_AUDIO_H

#include <stdint.h>

/* Som do PC (UDP). audio_start ao começar um stream, audio_stop no fim. */
int audio_start(int enabled);
void audio_stop(void);

/* Liga/desliga durante o stream (SELECT + START + cima). Desligado, a thread
 * para de tocar e o PSP para de pedir som (PS_CAP_AUDIO), então o PC para de
 * mandar. */
void audio_set_enabled(int on);
int audio_enabled(void);

/* Thread de rede: um pacote PS_MAGIC_AUDIO inteiro. */
void audio_packet(const uint8_t *pkt, int len);

typedef struct {
    int rate, channels;     /* formato do último pacote (0 = nenhum ainda) */
    int buffered_ms;        /* som no anel, esperando para tocar */
    int target_ms;          /* alvo do anel (adaptativo: sobe a cada falta) */
    unsigned packets, lost; /* pacotes recebidos e perdidos (buraco no seq) */
    unsigned underruns;     /* o anel esvaziou tocando */
    unsigned skips;         /* som descartado por excesso no anel (atraso demais) */
    int error;              /* < 0: sceAudioSRCChReserve falhou */
} audio_stats_t;

void audio_get_stats(audio_stats_t *st);

#endif
