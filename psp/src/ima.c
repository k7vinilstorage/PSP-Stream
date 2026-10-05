/*
 * IMA ADPCM (bloco do WAV, 4 bits por amostra). Por canal, o bloco começa
 * com 4 bytes: a 1ª amostra (int16) e o índice do passo; depois, grupos de 4
 * bytes (8 amostras) alternando os canais, nibble baixo primeiro. É o
 * contrário exato do adpcmenc do GStreamer (soma de deslocamentos, não a
 * multiplicação que o adpcmdec usa): conferido nibble a nibble no PC.
 */
#include "ima.h"

static const int16_t step_table[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
};
static const int8_t index_table[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

int ima_decode_block(const uint8_t *block, int len, int channels, int samples, int16_t *out)
{
    int pred[2], index[2];
    if (channels < 1 || channels > 2 || samples < 1 || len < 4 * channels)
        return 0;
    for (int c = 0; c < channels; c++) {
        pred[c] = (int16_t)(block[4 * c] | block[4 * c + 1] << 8);
        index[c] = block[4 * c + 2] > 88 ? 88 : block[4 * c + 2];
    }
    out[0] = (int16_t)pred[0];
    out[1] = (int16_t)pred[channels - 1];
    const uint8_t *data = block + 4 * channels;
    int groups = (samples - 1) / 8, avail = (len - 4 * channels) / (4 * channels);
    if (groups > avail)
        groups = avail;
    for (int g = 0; g < groups; g++) {
        for (int c = 0; c < channels; c++) {
            const uint8_t *p = data + (g * channels + c) * 4;
            for (int k = 0; k < 8; k++) {
                int nib = (k & 1) ? p[k >> 1] >> 4 : p[k >> 1] & 0x0F;
                int step = step_table[index[c]];
                int diff = step >> 3;
                if (nib & 4)
                    diff += step;
                if (nib & 2)
                    diff += step >> 1;
                if (nib & 1)
                    diff += step >> 2;
                int v = (nib & 8) ? pred[c] - diff : pred[c] + diff;
                pred[c] = v < -32768 ? -32768 : v > 32767 ? 32767 : v;
                index[c] += index_table[nib];
                index[c] = index[c] < 0 ? 0 : index[c] > 88 ? 88 : index[c];
                int16_t *o = out + 2 * (1 + g * 8 + k);
                if (channels == 2) {
                    o[c] = (int16_t)pred[c];
                } else {
                    o[0] = o[1] = (int16_t)pred[0];
                }
            }
        }
    }
    return 1 + groups * 8;
}
