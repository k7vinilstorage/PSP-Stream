/*
 * PSPStream SDL2 proxy library for CrossOver on macOS.
 *
 * CrossOver's winebus.so loads "libSDL2-2.0.0.dylib" with @loader_path/ first
 * in its LC_RPATH. Placing this ad-hoc signed universal dylib alongside
 * winebus.so lets PSPStream expose a virtual Xbox 360 controller (045e:028e)
 * inside every CrossOver bottle over Wi-Fi or USB without DriverKit or
 * Apple notarization.
 */
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define PSP_PAD_SHM_MAGIC   0x50535050u /* "PSPP" */
#define PSP_PAD_DEFAULT_SHM "/tmp/pspstream_pad.shm"

#define SDL_VIRTUAL_JOYSTICK_DESC_VERSION 1
#define SDL_JOYSTICK_TYPE_GAMECONTROLLER  1

#define SDL_VIRTUAL_NAXES    6
#define SDL_VIRTUAL_NBUTTONS 15
#define SDL_VIRTUAL_NHATS    0
#define SDL_VIRTUAL_AXIS_MASK   0x003Fu
#define SDL_VIRTUAL_BUTTON_MASK 0x7FFFu

#define XBOX_VENDOR_ID  0x045Eu
#define XBOX_PRODUCT_ID 0x028Eu

struct PspPadShm {
    uint32_t magic;
    uint32_t active;
    uint32_t pid;
    uint32_t seq;
    uint16_t buttons_hat;
    uint8_t lx;
    uint8_t ly;
    uint8_t rx;
    uint8_t ry;
    uint8_t lt;
    uint8_t rt;
} __attribute__((packed));

_Static_assert(sizeof(struct PspPadShm) == 24, "PspPadShm must be 24 bytes");

typedef struct {
    uint8_t data[16];
} SDL_JoystickGUID;

typedef struct SDL_VirtualJoystickDesc {
    uint16_t version;
    uint16_t type;
    uint16_t naxes;
    uint16_t nbuttons;
    uint16_t nhats;
    uint16_t vendor_id;
    uint16_t product_id;
    uint16_t padding;
    uint32_t button_mask;
    uint32_t axis_mask;
    const char *name;
    void *userdata;
    void (*Update)(void *userdata);
    void (*SetPlayerIndex)(void *userdata, int player_index);
    int (*Rumble)(void *userdata, uint16_t low_frequency_rumble, uint16_t high_frequency_rumble);
    int (*RumbleTriggers)(void *userdata, uint16_t left_rumble, uint16_t right_rumble);
    int (*SetLED)(void *userdata, uint8_t red, uint8_t green, uint8_t blue);
    int (*SendEffect)(void *userdata, const void *data, int size);
} SDL_VirtualJoystickDesc;

_Static_assert(sizeof(SDL_VirtualJoystickDesc) == 88, "SDL_VirtualJoystickDesc must be 88 bytes on 64-bit");

/* Forwarded function pointers loaded from the real libSDL2-2.0.0.dylib. */
static pthread_mutex_t g_init_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t g_sync_lock = PTHREAD_MUTEX_INITIALIZER;
static void *g_real_sdl = NULL;

static const char *(*p_SDL_GetError)(void);
static int (*p_SDL_Init)(uint32_t);
static void (*p_SDL_Quit)(void);
static void (*p_SDL_PumpEvents)(void);
static int (*p_SDL_WaitEventTimeout)(void *, int);
static uint32_t (*p_SDL_RegisterEvents)(int);
static int (*p_SDL_PushEvent)(void *);
static uint32_t (*p_SDL_GetTicks)(void);
static int (*p_SDL_SetHint)(const char *, const char *);
static int (*p_SDL_NumJoysticks)(void);
static void *(*p_SDL_JoystickOpen)(int);
static void (*p_SDL_JoystickClose)(void *);
static int (*p_SDL_JoystickEventState)(int);
static SDL_JoystickGUID (*p_SDL_JoystickGetGUID)(void *);
static void (*p_SDL_JoystickGetGUIDString)(SDL_JoystickGUID, char *, int);
static int32_t (*p_SDL_JoystickInstanceID)(void *);
static int32_t (*p_SDL_JoystickGetDeviceInstanceID)(int);
static const char *(*p_SDL_JoystickName)(void *);
static int (*p_SDL_JoystickNumAxes)(void *);
static int (*p_SDL_JoystickNumButtons)(void *);
static int (*p_SDL_JoystickNumBalls)(void *);
static int (*p_SDL_JoystickNumHats)(void *);
static int16_t (*p_SDL_JoystickGetAxis)(void *, int);
static uint8_t (*p_SDL_JoystickGetButton)(void *, int);
static uint8_t (*p_SDL_JoystickGetHat)(void *, int);
static int (*p_SDL_JoystickIsHaptic)(void *);
static int (*p_SDL_JoystickRumble)(void *, uint16_t, uint16_t, uint32_t);
static int (*p_SDL_JoystickRumbleTriggers)(void *, uint16_t, uint16_t, uint32_t);
static uint16_t (*p_SDL_JoystickGetProduct)(void *);
static uint16_t (*p_SDL_JoystickGetProductVersion)(void *);
static uint16_t (*p_SDL_JoystickGetVendor)(void *);
static int (*p_SDL_JoystickGetType)(void *);
static const char *(*p_SDL_JoystickGetSerial)(void *);
static int (*p_SDL_JoystickAttachVirtual)(int, int, int, int);
static int (*p_SDL_JoystickAttachVirtualEx)(const SDL_VirtualJoystickDesc *);
static int (*p_SDL_JoystickDetachVirtual)(int);
static int (*p_SDL_JoystickIsVirtual)(int);
static int (*p_SDL_JoystickSetVirtualAxis)(void *, int, int16_t);
static int (*p_SDL_JoystickSetVirtualButton)(void *, int, uint8_t);
static int (*p_SDL_JoystickSetVirtualHat)(void *, int, uint8_t);
static int (*p_SDL_IsGameController)(int);
static void *(*p_SDL_GameControllerOpen)(int);
static void (*p_SDL_GameControllerClose)(void *);
static int16_t (*p_SDL_GameControllerGetAxis)(void *, int);
static uint8_t (*p_SDL_GameControllerGetButton)(void *, int);
static const char *(*p_SDL_GameControllerName)(void *);
static int (*p_SDL_GameControllerEventState)(int);
static int (*p_SDL_GameControllerAddMapping)(const char *);
static void *(*p_SDL_GameControllerGetJoystick)(void *);
static void (*p_SDL_HapticClose)(void *);
static void (*p_SDL_HapticDestroyEffect)(void *, int);
static int (*p_SDL_HapticGetEffectStatus)(void *, int);
static int (*p_SDL_HapticNewEffect)(void *, void *);
static int (*p_SDL_HapticNumAxes)(void *);
static void *(*p_SDL_HapticOpenFromJoystick)(void *);
static int (*p_SDL_HapticPause)(void *);
static unsigned int (*p_SDL_HapticQuery)(void *);
static int (*p_SDL_HapticRumbleInit)(void *);
static int (*p_SDL_HapticRumblePlay)(void *, float, uint32_t);
static int (*p_SDL_HapticRumbleStop)(void *);
static int (*p_SDL_HapticRumbleSupported)(void *);
static int (*p_SDL_HapticRunEffect)(void *, int, uint32_t);
static int (*p_SDL_HapticSetGain)(void *, int);
static int (*p_SDL_HapticSetAutocenter)(void *, int);
static int (*p_SDL_HapticStopAll)(void *);
static int (*p_SDL_HapticStopEffect)(void *, int);
static int (*p_SDL_HapticUnpause)(void *);
static int (*p_SDL_HapticUpdateEffect)(void *, int, void *);

/* Shared memory and virtual controller state (guarded by g_sync_lock). */
static char g_shm_path[PATH_MAX];
static int g_shm_fd = -1;
static dev_t g_shm_dev = 0;
static ino_t g_shm_ino = 0;
static const volatile struct PspPadShm *g_shm = NULL;

static int g_dev_index = -1;
static int32_t g_instance_id = -1;
static void *g_joy = NULL;
static uint32_t g_last_seq = 0;
static uint64_t g_last_check_ms = 0;
static int g_cached_pid_alive = 0;
static uint32_t g_cached_pid = 0;

static uint64_t monotonic_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000u + (uint64_t)ts.tv_nsec / 1000000u;
}

static void *try_open_sdl(const char *path, int have_self, const struct stat *self_st) {
    struct stat st;
    if (!path || !path[0]) {
        return NULL;
    }
    if (stat(path, &st) != 0) {
        return NULL;
    }
    if (have_self && st.st_dev == self_st->st_dev && st.st_ino == self_st->st_ino) {
        return NULL;
    }
    return dlopen(path, RTLD_LAZY | RTLD_LOCAL);
}

static int ensure_real_sdl(void) {
    if (__atomic_load_n(&g_real_sdl, __ATOMIC_ACQUIRE) != NULL) {
        return 1;
    }
    pthread_mutex_lock(&g_init_lock);
    if (g_real_sdl == NULL) {
        Dl_info self_info;
        struct stat self_st;
        int have_self = 0;
        char self_dir[PATH_MAX] = {0};

        if (dladdr((void *)&ensure_real_sdl, &self_info) && self_info.dli_fname) {
            if (stat(self_info.dli_fname, &self_st) == 0) {
                have_self = 1;
            }
            const char *slash = strrchr(self_info.dli_fname, '/');
            if (slash && (size_t)(slash - self_info.dli_fname) < sizeof(self_dir)) {
                size_t len = (size_t)(slash - self_info.dli_fname);
                memcpy(self_dir, self_info.dli_fname, len);
                self_dir[len] = '\0';
            }
        }

        char cand_rel3[PATH_MAX] = {0};
        char cand_rel1[PATH_MAX] = {0};
        if (self_dir[0]) {
            snprintf(cand_rel3, sizeof(cand_rel3), "%s/../../../lib64/libSDL2-2.0.0.dylib", self_dir);
            snprintf(cand_rel1, sizeof(cand_rel1), "%s/../lib64/libSDL2-2.0.0.dylib", self_dir);
        }

        const char *candidates[] = {
            getenv("PSPSTREAM_REAL_SDL2"),
            cand_rel3[0] ? cand_rel3 : NULL,
            cand_rel1[0] ? cand_rel1 : NULL,
            "/Applications/CrossOver.app/Contents/SharedSupport/CrossOver/lib64/libSDL2-2.0.0.dylib",
            "/opt/homebrew/lib/libSDL2-2.0.0.dylib",
            "/usr/local/lib/libSDL2-2.0.0.dylib",
        };

        void *handle = NULL;
        for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
            handle = try_open_sdl(candidates[i], have_self, &self_st);
            if (handle != NULL) {
                break;
            }
        }

        if (handle != NULL) {
#define LOAD_SYM(name) *(void **)(&p_##name) = dlsym(handle, #name)
            LOAD_SYM(SDL_GetError);
            LOAD_SYM(SDL_Init);
            LOAD_SYM(SDL_Quit);
            LOAD_SYM(SDL_PumpEvents);
            LOAD_SYM(SDL_WaitEventTimeout);
            LOAD_SYM(SDL_RegisterEvents);
            LOAD_SYM(SDL_PushEvent);
            LOAD_SYM(SDL_GetTicks);
            LOAD_SYM(SDL_SetHint);
            LOAD_SYM(SDL_NumJoysticks);
            LOAD_SYM(SDL_JoystickOpen);
            LOAD_SYM(SDL_JoystickClose);
            LOAD_SYM(SDL_JoystickEventState);
            LOAD_SYM(SDL_JoystickGetGUID);
            LOAD_SYM(SDL_JoystickGetGUIDString);
            LOAD_SYM(SDL_JoystickInstanceID);
            LOAD_SYM(SDL_JoystickGetDeviceInstanceID);
            LOAD_SYM(SDL_JoystickName);
            LOAD_SYM(SDL_JoystickNumAxes);
            LOAD_SYM(SDL_JoystickNumButtons);
            LOAD_SYM(SDL_JoystickNumBalls);
            LOAD_SYM(SDL_JoystickNumHats);
            LOAD_SYM(SDL_JoystickGetAxis);
            LOAD_SYM(SDL_JoystickGetButton);
            LOAD_SYM(SDL_JoystickGetHat);
            LOAD_SYM(SDL_JoystickIsHaptic);
            LOAD_SYM(SDL_JoystickRumble);
            LOAD_SYM(SDL_JoystickRumbleTriggers);
            LOAD_SYM(SDL_JoystickGetProduct);
            LOAD_SYM(SDL_JoystickGetProductVersion);
            LOAD_SYM(SDL_JoystickGetVendor);
            LOAD_SYM(SDL_JoystickGetType);
            LOAD_SYM(SDL_JoystickGetSerial);
            LOAD_SYM(SDL_JoystickAttachVirtual);
            LOAD_SYM(SDL_JoystickAttachVirtualEx);
            LOAD_SYM(SDL_JoystickDetachVirtual);
            LOAD_SYM(SDL_JoystickIsVirtual);
            LOAD_SYM(SDL_JoystickSetVirtualAxis);
            LOAD_SYM(SDL_JoystickSetVirtualButton);
            LOAD_SYM(SDL_JoystickSetVirtualHat);
            LOAD_SYM(SDL_IsGameController);
            LOAD_SYM(SDL_GameControllerOpen);
            LOAD_SYM(SDL_GameControllerClose);
            LOAD_SYM(SDL_GameControllerGetAxis);
            LOAD_SYM(SDL_GameControllerGetButton);
            LOAD_SYM(SDL_GameControllerName);
            LOAD_SYM(SDL_GameControllerEventState);
            LOAD_SYM(SDL_GameControllerAddMapping);
            LOAD_SYM(SDL_GameControllerGetJoystick);
            LOAD_SYM(SDL_HapticClose);
            LOAD_SYM(SDL_HapticDestroyEffect);
            LOAD_SYM(SDL_HapticGetEffectStatus);
            LOAD_SYM(SDL_HapticNewEffect);
            LOAD_SYM(SDL_HapticNumAxes);
            LOAD_SYM(SDL_HapticOpenFromJoystick);
            LOAD_SYM(SDL_HapticPause);
            LOAD_SYM(SDL_HapticQuery);
            LOAD_SYM(SDL_HapticRumbleInit);
            LOAD_SYM(SDL_HapticRumblePlay);
            LOAD_SYM(SDL_HapticRumbleStop);
            LOAD_SYM(SDL_HapticRumbleSupported);
            LOAD_SYM(SDL_HapticRunEffect);
            LOAD_SYM(SDL_HapticSetGain);
            LOAD_SYM(SDL_HapticSetAutocenter);
            LOAD_SYM(SDL_HapticStopAll);
            LOAD_SYM(SDL_HapticStopEffect);
            LOAD_SYM(SDL_HapticUnpause);
            LOAD_SYM(SDL_HapticUpdateEffect);
#undef LOAD_SYM
            __atomic_store_n(&g_real_sdl, handle, __ATOMIC_RELEASE);
        }
    }
    pthread_mutex_unlock(&g_init_lock);
    return g_real_sdl != NULL;
}

static void unmap_shm_locked(void) {
    if (g_shm != NULL) {
        munmap((void *)g_shm, sizeof(struct PspPadShm));
        g_shm = NULL;
    }
    if (g_shm_fd >= 0) {
        close(g_shm_fd);
        g_shm_fd = -1;
    }
    g_shm_dev = 0;
    g_shm_ino = 0;
}

static void detach_virtual_locked(void) {
    if (g_joy != NULL) {
        if (p_SDL_JoystickClose) {
            p_SDL_JoystickClose(g_joy);
        }
        g_joy = NULL;
    }
    if (g_dev_index >= 0) {
        int detach_idx = g_dev_index;
        if (g_instance_id >= 0 && p_SDL_NumJoysticks && p_SDL_JoystickGetDeviceInstanceID) {
            int count = p_SDL_NumJoysticks();
            for (int i = 0; i < count; i++) {
                if (p_SDL_JoystickGetDeviceInstanceID(i) == g_instance_id) {
                    detach_idx = i;
                    break;
                }
            }
        }
        if (p_SDL_JoystickDetachVirtual) {
            p_SDL_JoystickDetachVirtual(detach_idx);
        }
        g_dev_index = -1;
        g_instance_id = -1;
    }
    g_last_seq = 0;
}

static int16_t decode_stick(uint8_t raw) {
    if (raw == 128) {
        return 0;
    }
    if (raw < 128) {
        return (int16_t)(((int)raw - 128) * 32768 / 128);
    }
    return (int16_t)(((int)raw - 128) * 32767 / 127);
}

static int16_t decode_trigger(uint8_t raw) {
    return (int16_t)(-32768 + ((int)raw * 65535) / 255);
}

static void apply_pad_state_locked(const struct PspPadShm *snap) {
    if (!g_joy || !p_SDL_JoystickSetVirtualAxis || !p_SDL_JoystickSetVirtualButton) {
        return;
    }
    p_SDL_JoystickSetVirtualAxis(g_joy, 0, decode_stick(snap->lx));
    p_SDL_JoystickSetVirtualAxis(g_joy, 1, decode_stick(snap->ly));
    p_SDL_JoystickSetVirtualAxis(g_joy, 2, decode_stick(snap->rx));
    p_SDL_JoystickSetVirtualAxis(g_joy, 3, decode_stick(snap->ry));
    p_SDL_JoystickSetVirtualAxis(g_joy, 4, decode_trigger(snap->lt));
    p_SDL_JoystickSetVirtualAxis(g_joy, 5, decode_trigger(snap->rt));

    /*
     * Map mac_gamepad.py bit positions (bits 0..10: A, B, X, Y, LB, RB,
     * BACK, START, GUIDE, L3, R3) to SDL_GameControllerButton indices (0..10).
     */
    static const uint8_t bit_to_sdl[11] = {
        0,  /* BTN_A -> SDL_CONTROLLER_BUTTON_A */
        1,  /* BTN_B -> SDL_CONTROLLER_BUTTON_B */
        2,  /* BTN_X -> SDL_CONTROLLER_BUTTON_X */
        3,  /* BTN_Y -> SDL_CONTROLLER_BUTTON_Y */
        9,  /* BTN_TL -> SDL_CONTROLLER_BUTTON_LEFTSHOULDER */
        10, /* BTN_TR -> SDL_CONTROLLER_BUTTON_RIGHTSHOULDER */
        4,  /* BTN_SELECT -> SDL_CONTROLLER_BUTTON_BACK */
        6,  /* BTN_START -> SDL_CONTROLLER_BUTTON_START */
        5,  /* BTN_MODE -> SDL_CONTROLLER_BUTTON_GUIDE */
        7,  /* BTN_THUMBL -> SDL_CONTROLLER_BUTTON_LEFTSTICK */
        8,  /* BTN_THUMBR -> SDL_CONTROLLER_BUTTON_RIGHTSTICK */
    };
    uint16_t btns = snap->buttons_hat & 0x0FFFu;
    for (int bit = 0; bit < 11; bit++) {
        p_SDL_JoystickSetVirtualButton(g_joy, bit_to_sdl[bit], (uint8_t)((btns >> bit) & 1u));
    }

    uint8_t hat = (uint8_t)((snap->buttons_hat >> 12) & 0x0Fu);
    uint8_t up = (hat == 7 || hat == 0 || hat == 1) ? 1 : 0;
    uint8_t down = (hat == 3 || hat == 4 || hat == 5) ? 1 : 0;
    uint8_t left = (hat == 5 || hat == 6 || hat == 7) ? 1 : 0;
    uint8_t right = (hat == 1 || hat == 2 || hat == 3) ? 1 : 0;
    p_SDL_JoystickSetVirtualButton(g_joy, 11, up);
    p_SDL_JoystickSetVirtualButton(g_joy, 12, down);
    p_SDL_JoystickSetVirtualButton(g_joy, 13, left);
    p_SDL_JoystickSetVirtualButton(g_joy, 14, right);
}

static int pid_is_alive_locked(uint32_t pid, uint64_t now_ms) {
    if (pid == 0) {
        return 0;
    }
    if (pid == g_cached_pid && (now_ms - g_last_check_ms) < 250u) {
        return g_cached_pid_alive;
    }
    int alive = (kill((pid_t)pid, 0) == 0 || errno == EPERM) ? 1 : 0;
    g_cached_pid = pid;
    g_cached_pid_alive = alive;
    return alive;
}

static void refresh_shm_mapping_locked(const char *path, uint64_t now_ms) {
    if (strcmp(path, g_shm_path) != 0) {
        unmap_shm_locked();
        snprintf(g_shm_path, sizeof(g_shm_path), "%s", path);
    }

    if (g_shm != NULL && (now_ms - g_last_check_ms) < 250u) {
        return;
    }

    struct stat st;
    if (stat(path, &st) != 0 || (size_t)st.st_size < sizeof(struct PspPadShm)) {
        unmap_shm_locked();
        return;
    }
    if (g_shm != NULL && st.st_dev == g_shm_dev && st.st_ino == g_shm_ino) {
        return;
    }

    unmap_shm_locked();
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        return;
    }
    void *mem = mmap(NULL, sizeof(struct PspPadShm), PROT_READ, MAP_SHARED, fd, 0);
    if (mem == MAP_FAILED) {
        close(fd);
        return;
    }
    g_shm_fd = fd;
    g_shm_dev = st.st_dev;
    g_shm_ino = st.st_ino;
    g_shm = (const volatile struct PspPadShm *)mem;
}

static int attach_virtual_locked(void) {
    int idx = -1;
    if (p_SDL_JoystickAttachVirtualEx) {
        SDL_VirtualJoystickDesc desc;
        memset(&desc, 0, sizeof(desc));
        desc.version = SDL_VIRTUAL_JOYSTICK_DESC_VERSION;
        desc.type = SDL_JOYSTICK_TYPE_GAMECONTROLLER;
        desc.naxes = SDL_VIRTUAL_NAXES;
        desc.nbuttons = SDL_VIRTUAL_NBUTTONS;
        desc.nhats = SDL_VIRTUAL_NHATS;
        desc.vendor_id = XBOX_VENDOR_ID;
        desc.product_id = XBOX_PRODUCT_ID;
        desc.button_mask = SDL_VIRTUAL_BUTTON_MASK;
        desc.axis_mask = SDL_VIRTUAL_AXIS_MASK;
        desc.name = "PSPStream X-Box 360 pad";
        idx = p_SDL_JoystickAttachVirtualEx(&desc);
    } else if (p_SDL_JoystickAttachVirtual) {
        idx = p_SDL_JoystickAttachVirtual(
            SDL_JOYSTICK_TYPE_GAMECONTROLLER, SDL_VIRTUAL_NAXES, SDL_VIRTUAL_NBUTTONS, SDL_VIRTUAL_NHATS);
    }
    if (idx < 0 || !p_SDL_JoystickOpen) {
        return 0;
    }
    void *joy = p_SDL_JoystickOpen(idx);
    if (!joy) {
        if (p_SDL_JoystickDetachVirtual) {
            p_SDL_JoystickDetachVirtual(idx);
        }
        return 0;
    }
    g_dev_index = idx;
    g_joy = joy;
    g_instance_id = p_SDL_JoystickInstanceID ? p_SDL_JoystickInstanceID(joy) : -1;
    return 1;
}

static void pspstream_sync(void) {
    if (!ensure_real_sdl()) {
        return;
    }
    const char *env_path = getenv("PSPSTREAM_PAD_SHM");
    const char *path = (env_path && env_path[0]) ? env_path : PSP_PAD_DEFAULT_SHM;

    pthread_mutex_lock(&g_sync_lock);
    uint64_t now_ms = monotonic_ms();
    refresh_shm_mapping_locked(path, now_ms);

    int should_attach = 0;
    struct PspPadShm snap;
    if (g_shm != NULL) {
        snap.magic = g_shm->magic;
        snap.active = g_shm->active;
        snap.pid = g_shm->pid;
        snap.seq = g_shm->seq;
        snap.buttons_hat = g_shm->buttons_hat;
        snap.lx = g_shm->lx;
        snap.ly = g_shm->ly;
        snap.rx = g_shm->rx;
        snap.ry = g_shm->ry;
        snap.lt = g_shm->lt;
        snap.rt = g_shm->rt;
        if (snap.magic == PSP_PAD_SHM_MAGIC && snap.active == 1u && pid_is_alive_locked(snap.pid, now_ms)) {
            should_attach = 1;
        }
    }
    if ((now_ms - g_last_check_ms) >= 250u) {
        g_last_check_ms = now_ms;
    }

    if (!should_attach) {
        if (g_dev_index >= 0) {
            detach_virtual_locked();
        }
        pthread_mutex_unlock(&g_sync_lock);
        return;
    }

    if (g_dev_index < 0) {
        if (!attach_virtual_locked()) {
            pthread_mutex_unlock(&g_sync_lock);
            return;
        }
        apply_pad_state_locked(&snap);
        g_last_seq = snap.seq;
    } else if (snap.seq != g_last_seq) {
        apply_pad_state_locked(&snap);
        g_last_seq = snap.seq;
    }
    pthread_mutex_unlock(&g_sync_lock);
}

/* ---- Exported SDL2 API ---- */

const char *SDL_GetError(void) {
    if (!ensure_real_sdl() || !p_SDL_GetError) {
        return "real libSDL2-2.0.0.dylib not found";
    }
    return p_SDL_GetError();
}

int SDL_Init(uint32_t flags) {
    if (!ensure_real_sdl() || !p_SDL_Init) {
        return -1;
    }
    return p_SDL_Init(flags);
}

void SDL_Quit(void) {
    pthread_mutex_lock(&g_sync_lock);
    detach_virtual_locked();
    unmap_shm_locked();
    pthread_mutex_unlock(&g_sync_lock);
    if (ensure_real_sdl() && p_SDL_Quit) {
        p_SDL_Quit();
    }
}

void SDL_PumpEvents(void) {
    pspstream_sync();
    if (ensure_real_sdl() && p_SDL_PumpEvents) {
        p_SDL_PumpEvents();
    }
}

int SDL_WaitEventTimeout(void *event, int timeout) {
    pspstream_sync();
    if (!ensure_real_sdl() || !p_SDL_WaitEventTimeout) {
        return 0;
    }
    return p_SDL_WaitEventTimeout(event, timeout);
}

uint32_t SDL_RegisterEvents(int numevents) {
    if (!ensure_real_sdl() || !p_SDL_RegisterEvents) {
        return (uint32_t)-1;
    }
    return p_SDL_RegisterEvents(numevents);
}

int SDL_PushEvent(void *event) {
    if (!ensure_real_sdl() || !p_SDL_PushEvent) {
        return -1;
    }
    return p_SDL_PushEvent(event);
}

uint32_t SDL_GetTicks(void) {
    if (!ensure_real_sdl() || !p_SDL_GetTicks) {
        return 0;
    }
    return p_SDL_GetTicks();
}

int SDL_SetHint(const char *name, const char *value) {
    if (!ensure_real_sdl() || !p_SDL_SetHint) {
        return 0;
    }
    return p_SDL_SetHint(name, value);
}

int SDL_NumJoysticks(void) {
    if (!ensure_real_sdl() || !p_SDL_NumJoysticks) {
        return 0;
    }
    return p_SDL_NumJoysticks();
}

void *SDL_JoystickOpen(int device_index) {
    if (!ensure_real_sdl() || !p_SDL_JoystickOpen) {
        return NULL;
    }
    return p_SDL_JoystickOpen(device_index);
}

void SDL_JoystickClose(void *joystick) {
    if (ensure_real_sdl() && p_SDL_JoystickClose) {
        p_SDL_JoystickClose(joystick);
    }
}

int SDL_JoystickEventState(int state) {
    if (!ensure_real_sdl() || !p_SDL_JoystickEventState) {
        return -1;
    }
    return p_SDL_JoystickEventState(state);
}

SDL_JoystickGUID SDL_JoystickGetGUID(void *joystick) {
    SDL_JoystickGUID zero = {{0}};
    if (!ensure_real_sdl() || !p_SDL_JoystickGetGUID) {
        return zero;
    }
    return p_SDL_JoystickGetGUID(joystick);
}

void SDL_JoystickGetGUIDString(SDL_JoystickGUID guid, char *pszGUID, int cbGUID) {
    if (ensure_real_sdl() && p_SDL_JoystickGetGUIDString) {
        p_SDL_JoystickGetGUIDString(guid, pszGUID, cbGUID);
    } else if (pszGUID && cbGUID > 0) {
        pszGUID[0] = '\0';
    }
}

int32_t SDL_JoystickInstanceID(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickInstanceID) {
        return -1;
    }
    return p_SDL_JoystickInstanceID(joystick);
}

int32_t SDL_JoystickGetDeviceInstanceID(int device_index) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetDeviceInstanceID) {
        return -1;
    }
    return p_SDL_JoystickGetDeviceInstanceID(device_index);
}

const char *SDL_JoystickName(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickName) {
        return NULL;
    }
    return p_SDL_JoystickName(joystick);
}

int SDL_JoystickNumAxes(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickNumAxes) {
        return -1;
    }
    return p_SDL_JoystickNumAxes(joystick);
}

int SDL_JoystickNumButtons(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickNumButtons) {
        return -1;
    }
    return p_SDL_JoystickNumButtons(joystick);
}

int SDL_JoystickNumBalls(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickNumBalls) {
        return -1;
    }
    return p_SDL_JoystickNumBalls(joystick);
}

int SDL_JoystickNumHats(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickNumHats) {
        return -1;
    }
    return p_SDL_JoystickNumHats(joystick);
}

int16_t SDL_JoystickGetAxis(void *joystick, int axis) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetAxis) {
        return 0;
    }
    return p_SDL_JoystickGetAxis(joystick, axis);
}

uint8_t SDL_JoystickGetButton(void *joystick, int button) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetButton) {
        return 0;
    }
    return p_SDL_JoystickGetButton(joystick, button);
}

uint8_t SDL_JoystickGetHat(void *joystick, int hat) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetHat) {
        return 0;
    }
    return p_SDL_JoystickGetHat(joystick, hat);
}

int SDL_JoystickIsHaptic(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickIsHaptic) {
        return -1;
    }
    return p_SDL_JoystickIsHaptic(joystick);
}

int SDL_JoystickRumble(void *joystick, uint16_t low_frequency_rumble, uint16_t high_frequency_rumble,
                       uint32_t duration_ms) {
    if (!ensure_real_sdl() || !p_SDL_JoystickRumble) {
        return -1;
    }
    return p_SDL_JoystickRumble(joystick, low_frequency_rumble, high_frequency_rumble, duration_ms);
}

int SDL_JoystickRumbleTriggers(void *joystick, uint16_t left_rumble, uint16_t right_rumble, uint32_t duration_ms) {
    if (!ensure_real_sdl() || !p_SDL_JoystickRumbleTriggers) {
        return -1;
    }
    return p_SDL_JoystickRumbleTriggers(joystick, left_rumble, right_rumble, duration_ms);
}

uint16_t SDL_JoystickGetProduct(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetProduct) {
        return 0;
    }
    return p_SDL_JoystickGetProduct(joystick);
}

uint16_t SDL_JoystickGetProductVersion(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetProductVersion) {
        return 0;
    }
    return p_SDL_JoystickGetProductVersion(joystick);
}

uint16_t SDL_JoystickGetVendor(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetVendor) {
        return 0;
    }
    return p_SDL_JoystickGetVendor(joystick);
}

int SDL_JoystickGetType(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetType) {
        return 0;
    }
    return p_SDL_JoystickGetType(joystick);
}

const char *SDL_JoystickGetSerial(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_JoystickGetSerial) {
        return NULL;
    }
    return p_SDL_JoystickGetSerial(joystick);
}

int SDL_JoystickAttachVirtual(int type, int naxes, int nbuttons, int nhats) {
    if (!ensure_real_sdl() || !p_SDL_JoystickAttachVirtual) {
        return -1;
    }
    return p_SDL_JoystickAttachVirtual(type, naxes, nbuttons, nhats);
}

int SDL_JoystickAttachVirtualEx(const SDL_VirtualJoystickDesc *desc) {
    if (!ensure_real_sdl() || !p_SDL_JoystickAttachVirtualEx) {
        return -1;
    }
    return p_SDL_JoystickAttachVirtualEx(desc);
}

int SDL_JoystickDetachVirtual(int device_index) {
    if (!ensure_real_sdl() || !p_SDL_JoystickDetachVirtual) {
        return -1;
    }
    return p_SDL_JoystickDetachVirtual(device_index);
}

int SDL_JoystickIsVirtual(int device_index) {
    if (!ensure_real_sdl() || !p_SDL_JoystickIsVirtual) {
        return 0;
    }
    return p_SDL_JoystickIsVirtual(device_index);
}

int SDL_JoystickSetVirtualAxis(void *joystick, int axis, int16_t value) {
    if (!ensure_real_sdl() || !p_SDL_JoystickSetVirtualAxis) {
        return -1;
    }
    return p_SDL_JoystickSetVirtualAxis(joystick, axis, value);
}

int SDL_JoystickSetVirtualButton(void *joystick, int button, uint8_t value) {
    if (!ensure_real_sdl() || !p_SDL_JoystickSetVirtualButton) {
        return -1;
    }
    return p_SDL_JoystickSetVirtualButton(joystick, button, value);
}

int SDL_JoystickSetVirtualHat(void *joystick, int hat, uint8_t value) {
    if (!ensure_real_sdl() || !p_SDL_JoystickSetVirtualHat) {
        return -1;
    }
    return p_SDL_JoystickSetVirtualHat(joystick, hat, value);
}

int SDL_IsGameController(int joystick_index) {
    if (!ensure_real_sdl() || !p_SDL_IsGameController) {
        return 0;
    }
    return p_SDL_IsGameController(joystick_index);
}

void *SDL_GameControllerOpen(int joystick_index) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerOpen) {
        return NULL;
    }
    return p_SDL_GameControllerOpen(joystick_index);
}

void SDL_GameControllerClose(void *gamecontroller) {
    if (ensure_real_sdl() && p_SDL_GameControllerClose) {
        p_SDL_GameControllerClose(gamecontroller);
    }
}

int16_t SDL_GameControllerGetAxis(void *gamecontroller, int axis) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerGetAxis) {
        return 0;
    }
    return p_SDL_GameControllerGetAxis(gamecontroller, axis);
}

uint8_t SDL_GameControllerGetButton(void *gamecontroller, int button) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerGetButton) {
        return 0;
    }
    return p_SDL_GameControllerGetButton(gamecontroller, button);
}

const char *SDL_GameControllerName(void *gamecontroller) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerName) {
        return NULL;
    }
    return p_SDL_GameControllerName(gamecontroller);
}

int SDL_GameControllerEventState(int state) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerEventState) {
        return -1;
    }
    return p_SDL_GameControllerEventState(state);
}

int SDL_GameControllerAddMapping(const char *mappingString) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerAddMapping) {
        return -1;
    }
    return p_SDL_GameControllerAddMapping(mappingString);
}

void *SDL_GameControllerGetJoystick(void *gamecontroller) {
    if (!ensure_real_sdl() || !p_SDL_GameControllerGetJoystick) {
        return NULL;
    }
    return p_SDL_GameControllerGetJoystick(gamecontroller);
}

void SDL_HapticClose(void *haptic) {
    if (ensure_real_sdl() && p_SDL_HapticClose) {
        p_SDL_HapticClose(haptic);
    }
}

void SDL_HapticDestroyEffect(void *haptic, int effect) {
    if (ensure_real_sdl() && p_SDL_HapticDestroyEffect) {
        p_SDL_HapticDestroyEffect(haptic, effect);
    }
}

int SDL_HapticGetEffectStatus(void *haptic, int effect) {
    if (!ensure_real_sdl() || !p_SDL_HapticGetEffectStatus) {
        return -1;
    }
    return p_SDL_HapticGetEffectStatus(haptic, effect);
}

int SDL_HapticNewEffect(void *haptic, void *effect) {
    if (!ensure_real_sdl() || !p_SDL_HapticNewEffect) {
        return -1;
    }
    return p_SDL_HapticNewEffect(haptic, effect);
}

int SDL_HapticNumAxes(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticNumAxes) {
        return -1;
    }
    return p_SDL_HapticNumAxes(haptic);
}

void *SDL_HapticOpenFromJoystick(void *joystick) {
    if (!ensure_real_sdl() || !p_SDL_HapticOpenFromJoystick) {
        return NULL;
    }
    return p_SDL_HapticOpenFromJoystick(joystick);
}

int SDL_HapticPause(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticPause) {
        return -1;
    }
    return p_SDL_HapticPause(haptic);
}

unsigned int SDL_HapticQuery(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticQuery) {
        return 0;
    }
    return p_SDL_HapticQuery(haptic);
}

int SDL_HapticRumbleInit(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticRumbleInit) {
        return -1;
    }
    return p_SDL_HapticRumbleInit(haptic);
}

int SDL_HapticRumblePlay(void *haptic, float strength, uint32_t length) {
    if (!ensure_real_sdl() || !p_SDL_HapticRumblePlay) {
        return -1;
    }
    return p_SDL_HapticRumblePlay(haptic, strength, length);
}

int SDL_HapticRumbleStop(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticRumbleStop) {
        return -1;
    }
    return p_SDL_HapticRumbleStop(haptic);
}

int SDL_HapticRumbleSupported(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticRumbleSupported) {
        return 0;
    }
    return p_SDL_HapticRumbleSupported(haptic);
}

int SDL_HapticRunEffect(void *haptic, int effect, uint32_t iterations) {
    if (!ensure_real_sdl() || !p_SDL_HapticRunEffect) {
        return -1;
    }
    return p_SDL_HapticRunEffect(haptic, effect, iterations);
}

int SDL_HapticSetGain(void *haptic, int gain) {
    if (!ensure_real_sdl() || !p_SDL_HapticSetGain) {
        return -1;
    }
    return p_SDL_HapticSetGain(haptic, gain);
}

int SDL_HapticSetAutocenter(void *haptic, int autocenter) {
    if (!ensure_real_sdl() || !p_SDL_HapticSetAutocenter) {
        return -1;
    }
    return p_SDL_HapticSetAutocenter(haptic, autocenter);
}

int SDL_HapticStopAll(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticStopAll) {
        return -1;
    }
    return p_SDL_HapticStopAll(haptic);
}

int SDL_HapticStopEffect(void *haptic, int effect) {
    if (!ensure_real_sdl() || !p_SDL_HapticStopEffect) {
        return -1;
    }
    return p_SDL_HapticStopEffect(haptic, effect);
}

int SDL_HapticUnpause(void *haptic) {
    if (!ensure_real_sdl() || !p_SDL_HapticUnpause) {
        return -1;
    }
    return p_SDL_HapticUnpause(haptic);
}

int SDL_HapticUpdateEffect(void *haptic, int effect, void *data) {
    if (!ensure_real_sdl() || !p_SDL_HapticUpdateEffect) {
        return -1;
    }
    return p_SDL_HapticUpdateEffect(haptic, effect, data);
}
