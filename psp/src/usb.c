#include "usb.h"
#include "../usbdrv/pspstream_usb.h"

#include <kubridge.h>
#include <pspkernel.h>
#include <stdio.h>
#include <string.h>

static SceUID g_modid = -1;
static int g_inited = 0;

int usb_init(const char *dir)
{
    if (g_inited)
        return 0;

    char prx_path[256];
    snprintf(prx_path, sizeof(prx_path), "%spspstream_usb.prx", dir ? dir : "");

    /* kuKernelLoadModule is provided by CFW (ARK-4 / PRO-C / ME) */
    g_modid = kuKernelLoadModule(prx_path, 0, NULL);
    if (g_modid < 0) {
        snprintf(prx_path, sizeof(prx_path), "ms0:/PSP/GAME/PSPStream/pspstream_usb.prx");
        g_modid = kuKernelLoadModule(prx_path, 0, NULL);
    }
    if (g_modid < 0) {
        return g_modid; /* CFW missing or file not found */
    }

    int status = 0;
    int ret = sceKernelStartModule(g_modid, 0, NULL, &status, NULL);
    if (ret < 0) {
        sceKernelUnloadModule(g_modid);
        g_modid = -1;
        return ret;
    }

    ret = pspusb_start();
    if (ret < 0) {
        sceKernelStopModule(g_modid, 0, NULL, &status, NULL);
        sceKernelUnloadModule(g_modid);
        g_modid = -1;
        return ret;
    }

    g_inited = 1;
    return 0;
}

int usb_is_attached(void)
{
    if (!g_inited)
        return 0;
    return pspusb_is_attached();
}

int usb_wait_attached(unsigned int timeout_us)
{
    if (!g_inited)
        return -1;
    return pspusb_wait_attached(timeout_us);
}

int usb_send_all(const void *buf, int len)
{
    if (!g_inited)
        return -1;
    const uint8_t *p = (const uint8_t *)buf;
    while (len > 0) {
        int n = pspusb_write(p, len, 3000000);
        if (n <= 0)
            return -1;
        p += n;
        len -= n;
    }
    return 0;
}

int usb_recv_all(void *buf, int len)
{
    if (!g_inited)
        return -1;
    uint8_t *p = (uint8_t *)buf;
    while (len > 0) {
        int n = pspusb_read(p, len, 5000000);
        if (n <= 0)
            return -1;
        p += n;
        len -= n;
    }
    return 0;
}

void usb_abort(void)
{
    if (g_inited)
        pspusb_abort();
}

void usb_clear(void)
{
    if (g_inited)
        pspusb_clear_buffers();
}

void usb_term(void)
{
    if (!g_inited)
        return;
    pspusb_stop();
    if (g_modid >= 0) {
        int status = 0;
        sceKernelStopModule(g_modid, 0, NULL, &status, NULL);
        sceKernelUnloadModule(g_modid);
        g_modid = -1;
    }
    g_inited = 0;
}
