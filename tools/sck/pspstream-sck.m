/*
 * pspstream-sck: captura nativa no macOS (ScreenCaptureKit, macOS 13+).
 *
 * Entrega vídeo I420 (480x272) a 60 fps com redução na GPU do macOS e/ou áudio
 * do sistema em blocos IMA ADPCM por TCP local para server/gst_pipe.py, sem
 * precisar do GStreamer nem de driver virtual (BlackHole) para capturar o som
 * das caixas.
 *
 * Uso:
 *   pspstream-sck video <host> <port> <monitor> <width> <height> <fps> <cursor:0|1> <keep_aspect:0|1>
 *   pspstream-sck audio <host> <port> <rate> <channels> <samples>
 */
#import <CoreMedia/CoreMedia.h>
#import <CoreVideo/CoreVideo.h>
#import <Foundation/Foundation.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>

#include <arpa/inet.h>
#include <errno.h>
#include <math.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig)
{
    (void)sig;
    g_stop = 1;
}

static int connect_tcp(const char *host, int port)
{
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) {
        perror("socket");
        return -1;
    }
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        fprintf(stderr, "pspstream-sck: invalid host %s\n", host);
        close(fd);
        return -1;
    }
    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        perror("connect");
        close(fd);
        return -1;
    }
    return fd;
}

static int send_all(int fd, const void *buf, size_t len)
{
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0 && !g_stop) {
        ssize_t n = send(fd, p, len, 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            return -1;
        p += n;
        len -= (size_t)n;
    }
    return g_stop ? -1 : 0;
}

/* ---- IMA ADPCM (mesmas tabelas de psp/src/ima.c e server/audio.py) ---- */

static const int16_t kStepTable[89] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,    21,    23,    25,    28,
    31,    34,    37,    41,    45,    50,    55,    60,    66,    73,    80,    88,    97,    107,   118,
    130,   143,   157,   173,   190,   209,   230,   253,   279,   307,   337,   371,   408,   449,   494,
    544,   598,   658,   724,   796,   876,   963,   1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,
    2272,  2499,  2749,  3024,  3327,  3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,
    9493,  10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
};

static const int8_t kIndexTable[16] = {-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8};

static uint8_t ima_encode_sample(int16_t sample, int *pred, int *index)
{
    int step = kStepTable[*index];
    int diff = sample - *pred;
    uint8_t nib = 0;
    if (diff < 0) {
        nib = 8;
        diff = -diff;
    }
    int delta = step >> 3;
    if (diff >= step) {
        nib |= 4;
        diff -= step;
        delta += step;
    }
    if (diff >= (step >> 1)) {
        nib |= 2;
        diff -= (step >> 1);
        delta += (step >> 1);
    }
    if (diff >= (step >> 2)) {
        nib |= 1;
        delta += (step >> 2);
    }
    int p = (nib & 8) ? (*pred - delta) : (*pred + delta);
    if (p > 32767)
        p = 32767;
    else if (p < -32768)
        p = -32768;
    *pred = p;
    int idx = *index + kIndexTable[nib];
    if (idx < 0)
        idx = 0;
    else if (idx > 88)
        idx = 88;
    *index = idx;
    return nib;
}

static size_t ima_encode_block(const int16_t *pcm, int samples, int channels, int state_idx[2], uint8_t *out)
{
    int pred[2] = {0, 0};
    int index[2] = {state_idx[0], state_idx[1]};
    for (int c = 0; c < channels; c++) {
        pred[c] = pcm[c];
        out[4 * c + 0] = (uint8_t)(pred[c] & 0xFF);
        out[4 * c + 1] = (uint8_t)((pred[c] >> 8) & 0xFF);
        out[4 * c + 2] = (uint8_t)index[c];
        out[4 * c + 3] = 0;
    }
    uint8_t *dst = out + 4 * channels;
    int groups = (samples - 1) / 8;
    for (int g = 0; g < groups; g++) {
        for (int c = 0; c < channels; c++) {
            uint8_t *p = dst + (g * channels + c) * 4;
            for (int k = 0; k < 8; k++) {
                int sidx = 1 + g * 8 + k;
                uint8_t nib = ima_encode_sample(pcm[sidx * channels + c], &pred[c], &index[c]);
                if ((k & 1) == 0)
                    p[k >> 1] = nib;
                else
                    p[k >> 1] |= (uint8_t)(nib << 4);
            }
        }
    }
    for (int c = 0; c < channels; c++)
        state_idx[c] = index[c];
    return (size_t)(4 * channels + groups * 4 * channels);
}

/* ---- Delegado do SCStream (vídeo e áudio) ---- */

@interface SckRunner : NSObject <SCStreamOutput, SCStreamDelegate> {
@public
    int sock;
    int mode_audio;
    /* Vídeo */
    int width, height;
    int dst_w, dst_h;
    int off_x, off_y;
    uint8_t *i420;
    size_t i420_bytes;
    /* Áudio */
    int rate, channels, block_samples;
    size_t block_bytes;
    float *in_buf;
    size_t in_frames, in_cap;
    double src_pos;
    int16_t *pcm_buf;
    size_t pcm_frames, pcm_cap;
    uint8_t *adpcm_buf;
    int ima_idx[2];
    dispatch_semaphore_t done_sem;
}
@end

@implementation SckRunner

- (void)dealloc
{
    free(i420);
    free(in_buf);
    free(pcm_buf);
    free(adpcm_buf);
}

- (void)signalDone
{
    g_stop = 1;
    if (done_sem)
        dispatch_semaphore_signal(done_sem);
}

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error
{
    (void)stream;
    if (error && !g_stop)
        fprintf(stderr, "pspstream-sck: stream stopped: %s\n", error.localizedDescription.UTF8String);
    [self signalDone];
}

- (void)handleVideo:(CMSampleBufferRef)sampleBuffer
{
    CVImageBufferRef img = CMSampleBufferGetImageBuffer(sampleBuffer);
    if (!img)
        return;
    if (CVPixelBufferLockBaseAddress(img, kCVPixelBufferLock_ReadOnly) != kCVReturnSuccess)
        return;

    int pw = (int)CVPixelBufferGetWidth(img);
    int ph = (int)CVPixelBufferGetHeight(img);
    int copy_w = (pw < dst_w ? pw : dst_w) & ~1;
    int copy_h = (ph < dst_h ? ph : dst_h) & ~1;

    const uint8_t *src_y = (const uint8_t *)CVPixelBufferGetBaseAddressOfPlane(img, 0);
    size_t stride_y = CVPixelBufferGetBytesPerRowOfPlane(img, 0);
    const uint8_t *src_uv = (const uint8_t *)CVPixelBufferGetBaseAddressOfPlane(img, 1);
    size_t stride_uv = CVPixelBufferGetBytesPerRowOfPlane(img, 1);

    uint8_t *y_dst = i420;
    uint8_t *u_dst = y_dst + (size_t)width * height;
    uint8_t *v_dst = u_dst + (size_t)(width / 2) * (height / 2);

    if (src_y && src_uv && copy_w > 0 && copy_h > 0) {
        for (int y = 0; y < copy_h; y++)
            memcpy(y_dst + (size_t)(off_y + y) * width + off_x, src_y + (size_t)y * stride_y, (size_t)copy_w);

        int cw = copy_w / 2, ch = copy_h / 2;
        int uw = width / 2, ox = off_x / 2, oy = off_y / 2;
        for (int y = 0; y < ch; y++) {
            const uint8_t *row = src_uv + (size_t)y * stride_uv;
            uint8_t *du = u_dst + (size_t)(oy + y) * uw + ox;
            uint8_t *dv = v_dst + (size_t)(oy + y) * uw + ox;
            for (int x = 0; x < cw; x++) {
                du[x] = row[2 * x];
                dv[x] = row[2 * x + 1];
            }
        }
    }
    CVPixelBufferUnlockBaseAddress(img, kCVPixelBufferLock_ReadOnly);

    if (send_all(sock, i420, i420_bytes) < 0)
        [self signalDone];
}

static int16_t float_to_s16(float v)
{
    if (v >= 1.0f)
        return 32767;
    if (v <= -1.0f)
        return -32768;
    long s = lroundf(v * 32767.0f);
    if (s > 32767)
        s = 32767;
    else if (s < -32768)
        s = -32768;
    return (int16_t)s;
}

- (void)ensureInCapacity:(size_t)need_frames
{
    if (need_frames <= in_cap)
        return;
    size_t cap = need_frames < 4096 ? 4096 : need_frames * 2;
    in_buf = (float *)realloc(in_buf, cap * (size_t)channels * sizeof(float));
    in_cap = cap;
}

- (void)ensurePcmCapacity:(size_t)need_frames
{
    if (need_frames <= pcm_cap)
        return;
    size_t cap = need_frames < 4096 ? 4096 : need_frames * 2;
    pcm_buf = (int16_t *)realloc(pcm_buf, cap * (size_t)channels * sizeof(int16_t));
    pcm_cap = cap;
}

- (void)handleAudio:(CMSampleBufferRef)sampleBuffer
{
    CMFormatDescriptionRef fmt = CMSampleBufferGetFormatDescription(sampleBuffer);
    const AudioStreamBasicDescription *asbd = fmt ? CMAudioFormatDescriptionGetStreamBasicDescription(fmt) : NULL;
    if (!asbd || asbd->mFormatID != kAudioFormatLinearPCM || asbd->mChannelsPerFrame == 0)
        return;

    CMItemCount nframes = CMSampleBufferGetNumSamples(sampleBuffer);
    if (nframes <= 0)
        return;

    size_t abl_size = 0;
    CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sampleBuffer, &abl_size, NULL, 0, NULL, NULL, 0, NULL);
    if (abl_size == 0)
        return;
    AudioBufferList *abl = (AudioBufferList *)malloc(abl_size);
    if (!abl)
        return;
    CMBlockBufferRef block_buf = NULL;
    OSStatus st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
        sampleBuffer, NULL, abl, abl_size, NULL, NULL,
        kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment, &block_buf);
    if (st != noErr) {
        free(abl);
        if (block_buf)
            CFRelease(block_buf);
        return;
    }

    int src_ch = (int)asbd->mChannelsPerFrame;
    int is_float = (asbd->mFormatFlags & kAudioFormatFlagIsFloat) != 0 && asbd->mBitsPerChannel == 32;
    int is_s16 = (asbd->mFormatFlags & kAudioFormatFlagIsSignedInteger) != 0 && asbd->mBitsPerChannel == 16;
    int non_interleaved = (asbd->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;

    if (!is_float && !is_s16) {
        free(abl);
        if (block_buf)
            CFRelease(block_buf);
        return;
    }

    [self ensureInCapacity:in_frames + (size_t)nframes];
    float *dst = in_buf + in_frames * (size_t)channels;
    for (CMItemCount i = 0; i < nframes; i++) {
        for (int c = 0; c < channels; c++) {
            int sc = c < src_ch ? c : 0;
            float v = 0.0f;
            if (non_interleaved) {
                if ((UInt32)sc < abl->mNumberBuffers && abl->mBuffers[sc].mData) {
                    if (is_float)
                        v = ((const float *)abl->mBuffers[sc].mData)[i];
                    else
                        v = ((const int16_t *)abl->mBuffers[sc].mData)[i] / 32768.0f;
                }
            } else if (abl->mNumberBuffers > 0 && abl->mBuffers[0].mData) {
                size_t idx = (size_t)i * (size_t)src_ch + (size_t)sc;
                if (is_float)
                    v = ((const float *)abl->mBuffers[0].mData)[idx];
                else
                    v = ((const int16_t *)abl->mBuffers[0].mData)[idx] / 32768.0f;
            }
            dst[i * channels + c] = v;
        }
    }
    in_frames += (size_t)nframes;

    if (block_buf)
        CFRelease(block_buf);
    free(abl);

    double src_rate = asbd->mSampleRate > 0 ? asbd->mSampleRate : (double)rate;
    if (fabs(src_rate - (double)rate) < 1.0) {
        [self ensurePcmCapacity:pcm_frames + in_frames];
        for (size_t i = 0; i < in_frames * (size_t)channels; i++)
            pcm_buf[pcm_frames * (size_t)channels + i] = float_to_s16(in_buf[i]);
        pcm_frames += in_frames;
        in_frames = 0;
        src_pos = 0.0;
    } else {
        double step = src_rate / (double)rate;
        size_t est = (size_t)(in_frames / step) + 8;
        [self ensurePcmCapacity:pcm_frames + est];
        while (src_pos + 1.0 < (double)in_frames) {
            size_t idx = (size_t)src_pos;
            double frac = src_pos - (double)idx;
            for (int c = 0; c < channels; c++) {
                float a = in_buf[idx * (size_t)channels + (size_t)c];
                float b = in_buf[(idx + 1) * (size_t)channels + (size_t)c];
                float v = (float)(a * (1.0 - frac) + b * frac);
                pcm_buf[pcm_frames * (size_t)channels + (size_t)c] = float_to_s16(v);
            }
            pcm_frames++;
            src_pos += step;
        }
        size_t consumed = (size_t)src_pos;
        if (consumed > 0 && consumed <= in_frames) {
            size_t left = in_frames - consumed;
            if (left > 0)
                memmove(in_buf, in_buf + consumed * (size_t)channels, left * (size_t)channels * sizeof(float));
            in_frames = left;
            src_pos -= (double)consumed;
        }
    }

    size_t offset = 0;
    while (pcm_frames - offset >= (size_t)block_samples) {
        size_t nbytes = ima_encode_block(pcm_buf + offset * (size_t)channels, block_samples, channels, ima_idx, adpcm_buf);
        if (send_all(sock, adpcm_buf, nbytes) < 0) {
            [self signalDone];
            return;
        }
        offset += (size_t)block_samples;
    }
    if (offset > 0) {
        size_t left = pcm_frames - offset;
        if (left > 0)
            memmove(pcm_buf, pcm_buf + offset * (size_t)channels, left * (size_t)channels * sizeof(int16_t));
        pcm_frames = left;
    }
}

- (void)stream:(SCStream *)stream didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer ofType:(SCStreamOutputType)type
{
    (void)stream;
    if (g_stop || !CMSampleBufferIsValid(sampleBuffer))
        return;
    if (!mode_audio && type == SCStreamOutputTypeScreen)
        [self handleVideo:sampleBuffer];
    else if (mode_audio && type == SCStreamOutputTypeAudio)
        [self handleAudio:sampleBuffer];
}

@end

static SCShareableContent *fetch_content(void)
{
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block SCShareableContent *content = nil;
    __block NSError *fetch_err = nil;
    [SCShareableContent getShareableContentExcludingDesktopWindows:NO
                                              onScreenWindowsOnly:YES
                                                completionHandler:^(SCShareableContent *c, NSError *err) {
                                                    content = c;
                                                    fetch_err = err;
                                                    dispatch_semaphore_signal(sem);
                                                }];
    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC));
    if (!content) {
        fprintf(stderr, "pspstream-sck: %s\n",
                fetch_err ? fetch_err.localizedDescription.UTF8String : "could not list displays");
    }
    return content;
}

static int run_stream(SCStream *stream, SckRunner *runner)
{
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    __block NSError *start_err = nil;
    [stream startCaptureWithCompletionHandler:^(NSError *err) {
        start_err = err;
        dispatch_semaphore_signal(sem);
    }];
    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, 8 * NSEC_PER_SEC));
    if (start_err) {
        fprintf(stderr, "pspstream-sck: start failed: %s\n", start_err.localizedDescription.UTF8String);
        return 1;
    }

    while (!g_stop)
        dispatch_semaphore_wait(runner->done_sem, dispatch_time(DISPATCH_TIME_NOW, 200 * NSEC_PER_MSEC));

    dispatch_semaphore_t stop_sem = dispatch_semaphore_create(0);
    [stream stopCaptureWithCompletionHandler:^(NSError *err) {
        (void)err;
        dispatch_semaphore_signal(stop_sem);
    }];
    dispatch_semaphore_wait(stop_sem, dispatch_time(DISPATCH_TIME_NOW, 2 * NSEC_PER_SEC));
    return 0;
}

int main(int argc, char *argv[])
{
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_signal);
    signal(SIGINT, on_signal);

    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: pspstream-sck video|audio ...\n");
            return 2;
        }
        if (@available(macOS 13.0, *)) {
            ;
        } else {
            fprintf(stderr, "pspstream-sck: ScreenCaptureKit audio/video requires macOS 13+\n");
            return 1;
        }

        int is_audio = strcmp(argv[1], "audio") == 0;
        int is_video = strcmp(argv[1], "video") == 0;
        if (!is_audio && !is_video) {
            fprintf(stderr, "usage: pspstream-sck video|audio ...\n");
            return 2;
        }
        if ((is_video && argc < 10) || (is_audio && argc < 7)) {
            fprintf(stderr, "pspstream-sck: missing arguments\n");
            return 2;
        }

        const char *host = argv[2];
        int port = atoi(argv[3]);

        SCShareableContent *content = fetch_content();
        if (!content || content.displays.count == 0)
            return 1;

        SckRunner *runner = [[SckRunner alloc] init];
        runner->done_sem = dispatch_semaphore_create(0);
        runner->mode_audio = is_audio;

        SCStreamConfiguration *cfg = [[SCStreamConfiguration alloc] init];
        int monitor = 0;

        if (is_video) {
            monitor = atoi(argv[4]);
            int width = atoi(argv[5]) & ~1;
            int height = atoi(argv[6]) & ~1;
            int fps = atoi(argv[7]);
            int cursor = atoi(argv[8]);
            int keep_aspect = atoi(argv[9]);
            if (monitor < 0 || (NSUInteger)monitor >= content.displays.count) {
                fprintf(stderr, "pspstream-sck: monitor %d out of range (%lu connected)\n",
                        monitor, (unsigned long)content.displays.count);
                return 1;
            }
            if (width < 16 || height < 16 || fps < 1) {
                fprintf(stderr, "pspstream-sck: invalid video parameters\n");
                return 2;
            }
            SCDisplay *disp = content.displays[(NSUInteger)monitor];
            int dst_w = width, dst_h = height;
            if (keep_aspect && disp.width > 0 && disp.height > 0) {
                if ((double)disp.width * height >= (double)width * disp.height) {
                    dst_w = width;
                    dst_h = (int)lround((double)width * (double)disp.height / (double)disp.width) & ~1;
                } else {
                    dst_h = height;
                    dst_w = (int)lround((double)height * (double)disp.width / (double)disp.height) & ~1;
                }
                if (dst_w < 2) dst_w = 2;
                if (dst_h < 2) dst_h = 2;
                if (dst_w > width) dst_w = width;
                if (dst_h > height) dst_h = height;
            }
            runner->width = width;
            runner->height = height;
            runner->dst_w = dst_w;
            runner->dst_h = dst_h;
            runner->off_x = ((width - dst_w) / 2) & ~1;
            runner->off_y = ((height - dst_h) / 2) & ~1;
            size_t y_bytes = (size_t)width * height;
            size_t uv_bytes = (size_t)(width / 2) * (height / 2);
            runner->i420_bytes = y_bytes + 2 * uv_bytes;
            runner->i420 = (uint8_t *)malloc(runner->i420_bytes);
            if (!runner->i420)
                return 1;
            memset(runner->i420, 16, y_bytes);
            memset(runner->i420 + y_bytes, 128, 2 * uv_bytes);

            cfg.width = (size_t)dst_w;
            cfg.height = (size_t)dst_h;
            cfg.minimumFrameInterval = CMTimeMake(1, fps);
            cfg.pixelFormat = kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange;
            cfg.showsCursor = cursor != 0;
            cfg.queueDepth = 3;
        } else {
            int rate = atoi(argv[4]);
            int channels = atoi(argv[5]);
            int samples = atoi(argv[6]);
            if (rate < 8000 || channels < 1 || channels > 2 || samples < 9 || (samples - 1) % 8 != 0) {
                fprintf(stderr, "pspstream-sck: invalid audio parameters\n");
                return 2;
            }
            runner->rate = rate;
            runner->channels = channels;
            runner->block_samples = samples;
            runner->block_bytes = (size_t)(4 * channels + (samples - 1) / 2 * channels);
            runner->adpcm_buf = (uint8_t *)malloc(runner->block_bytes);
            if (!runner->adpcm_buf)
                return 1;

            cfg.width = 64;
            cfg.height = 64;
            cfg.minimumFrameInterval = CMTimeMake(1, 1);
            cfg.showsCursor = NO;
            cfg.capturesAudio = YES;
            cfg.excludesCurrentProcessAudio = YES;
            cfg.sampleRate = 48000;
            cfg.channelCount = channels;
        }

        runner->sock = connect_tcp(host, port);
        if (runner->sock < 0)
            return 1;

        SCDisplay *disp = content.displays[(NSUInteger)monitor];
        SCContentFilter *filter = [[SCContentFilter alloc] initWithDisplay:disp excludingWindows:@[]];
        SCStream *stream = [[SCStream alloc] initWithFilter:filter configuration:cfg delegate:runner];
        dispatch_queue_t q = dispatch_queue_create("pspstream.sck", DISPATCH_QUEUE_SERIAL);
        NSError *err = nil;
        SCStreamOutputType out_type = is_audio ? SCStreamOutputTypeAudio : SCStreamOutputTypeScreen;
        if (![stream addStreamOutput:runner type:out_type sampleHandlerQueue:q error:&err]) {
            fprintf(stderr, "pspstream-sck: addStreamOutput: %s\n",
                    err ? err.localizedDescription.UTF8String : "error");
            close(runner->sock);
            return 1;
        }
        int rc = run_stream(stream, runner);
        close(runner->sock);
        return rc;
    }
}
