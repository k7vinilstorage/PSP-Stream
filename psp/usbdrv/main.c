/*
 * PSPStream USB Kernel Driver
 *
 * Exposes a custom USB device (VID 0x054C, PID 0x02D2) with 2 bulk endpoints:
 *   EP 1 (0x81) Bulk IN:  PSP -> PC (requests, controls, 52 bytes)
 *   EP 2 (0x02) Bulk OUT: PC -> PSP (video frames, audio packets)
 *
 * Implements a thread-context bulk receive pipeline feeding a 128 KB kernel ring buffer.
 * Callbacks in interrupt context only signal event flags to ensure zero kernel panics.
 */
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <string.h>

#include "pspstream_usb.h"

PSP_MODULE_INFO("PSPStreamUSB", PSP_MODULE_KERNEL, 1, 1);

#define USB_RX_BUF_SIZE (32 * 1024)
#define USB_TX_BUF_SIZE (16 * 1024)
#define RX_RING_SIZE    (256 * 1024)

enum UsbEvents {
    USB_EVENT_ATTACH   = 0x01,
    USB_EVENT_DETACH   = 0x02,
    USB_EVENT_ATTACHED = 0x04,
};

enum UsbTransEvents {
    USB_TRANSEVENT_BULKIN_DONE  = 0x01,
    USB_TRANSEVENT_BULKOUT_DONE = 0x02,
    USB_TRANSEVENT_RX_DATA      = 0x04,
    USB_TRANSEVENT_SPACE_AVAIL  = 0x08,
    USB_TRANSEVENT_ABORT        = 0x10,
    USB_TRANSEVENT_DETACH       = 0x20,
};

static SceUID g_main_thid = -1;
static SceUID g_rx_thid = -1;
static SceUID g_mainevent = -1;
static SceUID g_transevent = -1;
static SceUID g_sendsema = -1;
static SceUID g_recvsema = -1;

static volatile int g_connected = 0;
static volatile int g_aborted = 0;
static volatile int g_driver_started = 0;

/* Static bulk requests */
static struct UsbdDeviceReq g_bulkin_req;
static struct UsbdDeviceReq g_bulkout_req;

/* Aligned buffers for USB DMA */
static uint8_t usb_tx_buf[USB_TX_BUF_SIZE] __attribute__((aligned(64)));
static uint8_t usb_rx_buf[USB_RX_BUF_SIZE] __attribute__((aligned(64)));

/* Receive Ring Buffer */
static uint8_t rx_ring[RX_RING_SIZE];
static volatile unsigned int rx_wr = 0;
static volatile unsigned int rx_rd = 0;

/* Hi-Speed device descriptor */
static struct DeviceDescriptor devdesc_hi = {
    .bLength = 18,
    .bDescriptorType = 0x01,
    .bcdUSB = 0x200,
    .bDeviceClass = 0,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize = 64,
    .idVendor = PSPSTREAM_USB_VID,
    .idProduct = PSPSTREAM_USB_PID,
    .bcdDevice = 0x100,
    .iManufacturer = 0,
    .iProduct = 1,
    .iSerialNumber = 0,
    .bNumConfigurations = 1
};

/* Hi-Speed configuration descriptor */
static struct ConfigDescriptor confdesc_hi = {
    .bLength = 9,
    .bDescriptorType = 2,
    .wTotalLength = (9 + 9 + (2 * 7)),
    .bNumInterfaces = 1,
    .bConfigurationValue = 1,
    .iConfiguration = 0,
    .bmAttributes = 0xC0,
    .bMaxPower = 0
};

/* Hi-Speed interface descriptor */
static struct InterfaceDescriptor interdesc_hi = {
    .bLength = 9,
    .bDescriptorType = 4,
    .bInterfaceNumber = 0,
    .bAlternateSetting = 0,
    .bNumEndpoints = 2,
    .bInterfaceClass = 0xFF,
    .bInterfaceSubClass = 0x01,
    .bInterfaceProtocol = 0xFF,
    .iInterface = 1
};

/* Hi-Speed endpoint descriptors (EP1 IN 512B, EP2 OUT 512B) */
static struct EndpointDescriptor endpdesc_hi[2] = {
    {
        .bLength = 7,
        .bDescriptorType = 5,
        .bEndpointAddress = 0x81, /* EP1 IN */
        .bmAttributes = 2,        /* Bulk */
        .wMaxPacketSize = 512,
        .bInterval = 0
    },
    {
        .bLength = 7,
        .bDescriptorType = 5,
        .bEndpointAddress = 0x02, /* EP2 OUT */
        .bmAttributes = 2,        /* Bulk */
        .wMaxPacketSize = 512,
        .bInterval = 0
    }
};

/* Full-Speed device descriptor */
static struct DeviceDescriptor devdesc_full = {
    .bLength = 18,
    .bDescriptorType = 0x01,
    .bcdUSB = 0x200,
    .bDeviceClass = 0,
    .bDeviceSubClass = 0,
    .bDeviceProtocol = 0,
    .bMaxPacketSize = 64,
    .idVendor = PSPSTREAM_USB_VID,
    .idProduct = PSPSTREAM_USB_PID,
    .bcdDevice = 0x100,
    .iManufacturer = 0,
    .iProduct = 1,
    .iSerialNumber = 0,
    .bNumConfigurations = 1
};

/* Full-Speed configuration descriptor */
static struct ConfigDescriptor confdesc_full = {
    .bLength = 9,
    .bDescriptorType = 2,
    .wTotalLength = (9 + 9 + (2 * 7)),
    .bNumInterfaces = 1,
    .bConfigurationValue = 1,
    .iConfiguration = 0,
    .bmAttributes = 0xC0,
    .bMaxPower = 0
};

/* Full-Speed interface descriptor */
static struct InterfaceDescriptor interdesc_full = {
    .bLength = 9,
    .bDescriptorType = 4,
    .bInterfaceNumber = 0,
    .bAlternateSetting = 0,
    .bNumEndpoints = 2,
    .bInterfaceClass = 0xFF,
    .bInterfaceSubClass = 0x01,
    .bInterfaceProtocol = 0xFF,
    .iInterface = 1
};

/* Full-Speed endpoint descriptors (64 bytes each) */
static struct EndpointDescriptor endpdesc_full[2] = {
    {
        .bLength = 7,
        .bDescriptorType = 5,
        .bEndpointAddress = 0x81,
        .bmAttributes = 2,
        .wMaxPacketSize = 64,
        .bInterval = 0
    },
    {
        .bLength = 7,
        .bDescriptorType = 5,
        .bEndpointAddress = 0x02,
        .bmAttributes = 2,
        .wMaxPacketSize = 64,
        .bInterval = 0
    }
};

/* UTF-16LE String descriptor for "PSPStream" */
static unsigned char strp[] = {
    20, 0x03,
    'P', 0, 'S', 0, 'P', 0, 'S', 0, 't', 0, 'r', 0, 'e', 0, 'a', 0, 'm', 0
};

/* Endpoint blocks (0: control, 1: EP1 IN, 2: EP2 OUT) */
static struct UsbEndpoint endp[3] = {
    { 0, 0, 0 },
    { 1, 0, 0 },
    { 2, 0, 0 },
};

/* Interface block */
static struct UsbInterface intp = {
    0xFFFFFFFF, 0, 1
};

/* Data structures for sceUsbbd */
static struct UsbData usbdata[2];

/* Forward declaration */
extern struct UsbDriver g_driver;

static int usb_request(int arg1, int arg2, struct DeviceRequest *req)
{
    return 0;
}

static int func28(int arg1, int arg2, int arg3)
{
    return 0;
}

/* Callbacks in interrupt context: ONLY set event flags, zero blocking or syscalls */
static int usb_attach(int speed, void *arg2, void *arg3)
{
    if (g_mainevent >= 0)
        sceKernelSetEventFlag(g_mainevent, USB_EVENT_ATTACH);
    return 0;
}

static int usb_detach(int arg1, int arg2, int arg3)
{
    if (g_mainevent >= 0)
        sceKernelSetEventFlag(g_mainevent, USB_EVENT_DETACH);
    return 0;
}

static int bulkin_req_done(struct UsbdDeviceReq *req, int arg2, int arg3)
{
    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_BULKIN_DONE);
    return 0;
}

static int bulkout_req_done(struct UsbdDeviceReq *req, int arg2, int arg3)
{
    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_BULKOUT_DONE);
    return 0;
}

/* USB attach / detach monitor thread */
static int usb_main_thread(SceSize size, void *argp)
{
    u32 result = 0;
    while (g_driver_started) {
        int ret = sceKernelWaitEventFlag(g_mainevent,
                                         USB_EVENT_ATTACH | USB_EVENT_DETACH,
                                         PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                         &result, NULL);
        if (ret < 0)
            break;

        if (result & USB_EVENT_DETACH) {
            g_connected = 0;
            sceUsbbdReqCancelAll(&endp[1]);
            sceUsbbdReqCancelAll(&endp[2]);
            sceKernelClearEventFlag(g_mainevent, ~USB_EVENT_ATTACHED);
            if (g_transevent >= 0)
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_DETACH);
        }

        if (result & USB_EVENT_ATTACH) {
            rx_wr = rx_rd = 0;
            g_aborted = 0;
            g_connected = 1;
            sceKernelSetEventFlag(g_mainevent, USB_EVENT_ATTACHED);
            if (g_transevent >= 0)
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_SPACE_AVAIL);
        }
    }
    return 0;
}

/* Background thread receiving packets from EP2 OUT into ring buffer */
static int usb_rx_thread(SceSize size, void *argp)
{
    while (g_driver_started) {
        if (!g_connected || g_aborted) {
            sceKernelDelayThread(10 * 1000); /* 10 ms */
            continue;
        }

        /* Check if ring buffer has room for another incoming chunk */
        unsigned int used = rx_wr - rx_rd;
        if (RX_RING_SIZE - used < USB_RX_BUF_SIZE) {
            u32 res = 0;
            SceUInt timeout = 10 * 1000; /* 10 ms */
            sceKernelWaitEventFlag(g_transevent,
                                   USB_TRANSEVENT_SPACE_AVAIL | USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH,
                                   PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                   &res, &timeout);
            continue;
        }

        /* Invalidate DMA buffer cache before receiving */
        sceKernelDcacheInvalidateRange(usb_rx_buf, USB_RX_BUF_SIZE);
        memset(&g_bulkout_req, 0, sizeof(g_bulkout_req));
        g_bulkout_req.endp = &endp[2]; /* EP2 OUT */
        g_bulkout_req.data = usb_rx_buf;
        g_bulkout_req.size = USB_RX_BUF_SIZE;
        g_bulkout_req.func = bulkout_req_done;

        sceKernelClearEventFlag(g_transevent, ~USB_TRANSEVENT_BULKOUT_DONE);
        int r = sceUsbbdReqRecv(&g_bulkout_req);
        if (r < 0) {
            sceKernelDelayThread(10 * 1000);
            continue;
        }

        /* Wait for completion, abort, or detach */
        u32 result = 0;
        SceUInt wait_time = 1000 * 1000; /* 1 second timeout */
        r = sceKernelWaitEventFlag(g_transevent,
                                   USB_TRANSEVENT_BULKOUT_DONE | USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH,
                                   PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                   &result, &wait_time);

        if (r < 0 || (result & (USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH))) {
            sceUsbbdReqCancelAll(&endp[2]);
            continue;
        }

        if (result & USB_TRANSEVENT_BULKOUT_DONE) {
            if (g_bulkout_req.retcode == 0 && g_bulkout_req.recvsize > 0) {
                int n = g_bulkout_req.recvsize;
                unsigned int wr = rx_wr;
                unsigned int off = wr & (RX_RING_SIZE - 1);
                unsigned int first = RX_RING_SIZE - off;
                if (first > (unsigned int)n)
                    first = (unsigned int)n;
                memcpy(&rx_ring[off], usb_rx_buf, first);
                if ((unsigned int)n > first) {
                    memcpy(&rx_ring[0], usb_rx_buf + first, (unsigned int)n - first);
                }
                rx_wr = wr + n;

                /* Notify user-space reader */
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_RX_DATA);
            }
        }
    }
    return 0;
}

static int start_func(int size, void *p)
{
    memset(usbdata, 0, sizeof(usbdata));

    /* Hi-Speed setup */
    memcpy(usbdata[0].devdesc, &devdesc_hi, sizeof(devdesc_hi));
    usbdata[0].config.pconfdesc = &usbdata[0].confdesc;
    usbdata[0].config.pinterfaces = &usbdata[0].interfaces;
    usbdata[0].config.pinterdesc = &usbdata[0].interdesc;
    usbdata[0].config.pendp = usbdata[0].endp;
    memcpy(usbdata[0].confdesc.desc, &confdesc_hi, sizeof(confdesc_hi));
    usbdata[0].confdesc.pinterfaces = &usbdata[0].interfaces;
    usbdata[0].interfaces.pinterdesc[0] = &usbdata[0].interdesc;
    usbdata[0].interfaces.intcount = 1;
    memcpy(usbdata[0].interdesc.desc, &interdesc_hi, sizeof(interdesc_hi));
    usbdata[0].interdesc.pendp = usbdata[0].endp;
    memcpy(usbdata[0].endp[0].desc, &endpdesc_hi[0], sizeof(endpdesc_hi[0]));
    memcpy(usbdata[0].endp[1].desc, &endpdesc_hi[1], sizeof(endpdesc_hi[1]));

    /* Full-Speed setup */
    memcpy(usbdata[1].devdesc, &devdesc_full, sizeof(devdesc_full));
    usbdata[1].config.pconfdesc = &usbdata[1].confdesc;
    usbdata[1].config.pinterfaces = &usbdata[1].interfaces;
    usbdata[1].config.pinterdesc = &usbdata[1].interdesc;
    usbdata[1].config.pendp = usbdata[1].endp;
    memcpy(usbdata[1].confdesc.desc, &confdesc_full, sizeof(confdesc_full));
    usbdata[1].confdesc.pinterfaces = &usbdata[1].interfaces;
    usbdata[1].interfaces.pinterdesc[0] = &usbdata[1].interdesc;
    usbdata[1].interfaces.intcount = 1;
    memcpy(usbdata[1].interdesc.desc, &interdesc_full, sizeof(interdesc_full));
    usbdata[1].interdesc.pendp = usbdata[1].endp;
    memcpy(usbdata[1].endp[0].desc, &endpdesc_full[0], sizeof(endpdesc_full[0]));
    memcpy(usbdata[1].endp[1].desc, &endpdesc_full[1], sizeof(endpdesc_full[1]));

    g_driver.devp_hi = usbdata[0].devdesc;
    g_driver.confp_hi = &usbdata[0].config;
    g_driver.devp = usbdata[1].devdesc;
    g_driver.confp = &usbdata[1].config;

    g_mainevent = sceKernelCreateEventFlag("PSUSB_MainEvent", 0x200, 0, NULL);
    if (g_mainevent < 0)
        return g_mainevent;

    g_transevent = sceKernelCreateEventFlag("PSUSB_TransEvent", 0x200, 0, NULL);
    if (g_transevent < 0)
        return g_transevent;

    g_sendsema = sceKernelCreateSema("PSUSB_SendSema", 0, 1, 1, NULL);
    if (g_sendsema < 0)
        return g_sendsema;

    g_recvsema = sceKernelCreateSema("PSUSB_RecvSema", 0, 1, 1, NULL);
    if (g_recvsema < 0)
        return g_recvsema;

    g_driver_started = 1;
    g_main_thid = sceKernelCreateThread("PSUSB_MainThread", usb_main_thread, 0x18, 0x4000, 0, NULL);
    if (g_main_thid >= 0)
        sceKernelStartThread(g_main_thid, 0, NULL);

    g_rx_thid = sceKernelCreateThread("PSUSB_RxThread", usb_rx_thread, 0x14, 0x8000, 0, NULL);
    if (g_rx_thid >= 0)
        sceKernelStartThread(g_rx_thid, 0, NULL);

    return 0;
}

static int stop_func(int size, void *p)
{
    g_driver_started = 0;
    g_connected = 0;
    g_aborted = 1;

    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_ABORT);
    if (g_mainevent >= 0)
        sceKernelSetEventFlag(g_mainevent, USB_EVENT_DETACH);

    if (g_rx_thid >= 0) {
        sceKernelTerminateDeleteThread(g_rx_thid);
        g_rx_thid = -1;
    }
    if (g_main_thid >= 0) {
        sceKernelTerminateDeleteThread(g_main_thid);
        g_main_thid = -1;
    }

    if (g_mainevent >= 0) {
        sceKernelDeleteEventFlag(g_mainevent);
        g_mainevent = -1;
    }
    if (g_transevent >= 0) {
        sceKernelDeleteEventFlag(g_transevent);
        g_transevent = -1;
    }
    if (g_sendsema >= 0) {
        sceKernelDeleteSema(g_sendsema);
        g_sendsema = -1;
    }
    if (g_recvsema >= 0) {
        sceKernelDeleteSema(g_recvsema);
        g_recvsema = -1;
    }
    return 0;
}

struct UsbDriver g_driver = {
    .name = PSPSTREAM_USB_DRIVERNAME,
    .endpoints = 3,
    .endp = endp,
    .intp = &intp,
    .devp_hi = NULL,
    .confp_hi = NULL,
    .devp = NULL,
    .confp = NULL,
    .str = (struct StringDescriptor *)strp,
    .recvctl = usb_request,
    .func28 = func28,
    .attach = usb_attach,
    .detach = usb_detach,
    .unk34 = 0,
    .start_func = start_func,
    .stop_func = stop_func,
    .link = NULL
};

/* Module entry point */
int module_start(SceSize args, void *argp)
{
    int k1 = pspSdkSetK1(0);
    int ret = sceUsbbdRegister(&g_driver);
    pspSdkSetK1(k1);
    return ret < 0 ? ret : 0;
}

/* Module stop entry */
int module_stop(SceSize args, void *argp)
{
    int k1 = pspSdkSetK1(0);
    int ret = sceUsbbdUnregister(&g_driver);
    pspSdkSetK1(k1);
    return ret < 0 ? ret : 0;
}

/*
 * Public API (Exported to user-mode)
 */

int pspusb_start(void)
{
    int k1 = pspSdkSetK1(0);
    g_aborted = 0;

    int ret = sceUsbStart(PSP_USBBUS_DRIVERNAME, 0, 0);
    if (ret != 0 && ret != (int)0x80243003) { /* already started is ok */
        pspSdkSetK1(k1);
        return ret;
    }

    ret = sceUsbStart(PSPSTREAM_USB_DRIVERNAME, 0, 0);
    if (ret != 0 && ret != (int)0x80243003) {
        pspSdkSetK1(k1);
        return ret;
    }

    ret = sceUsbActivate(PSPSTREAM_USB_PID);
    pspSdkSetK1(k1);
    return ret;
}

int pspusb_stop(void)
{
    int k1 = pspSdkSetK1(0);
    g_aborted = 1;
    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_ABORT);

    sceUsbDeactivate(PSPSTREAM_USB_PID);
    sceUsbStop(PSPSTREAM_USB_DRIVERNAME, 0, 0);
    sceUsbStop(PSP_USBBUS_DRIVERNAME, 0, 0);
    g_connected = 0;
    pspSdkSetK1(k1);
    return 0;
}

int pspusb_is_attached(void)
{
    return g_connected;
}

int pspusb_wait_attached(unsigned int timeout_us)
{
    if (g_connected)
        return 1;
    if (g_mainevent < 0)
        return -1;

    u32 result;
    SceUInt wait_time = timeout_us;
    int r = sceKernelWaitEventFlag(g_mainevent, USB_EVENT_ATTACHED,
                                   PSP_EVENT_WAITOR, &result,
                                   timeout_us > 0 ? &wait_time : NULL);
    return (r == 0) ? 1 : 0;
}

int pspusb_abort(void)
{
    int k1 = pspSdkSetK1(0);
    g_aborted = 1;
    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_ABORT);
    sceUsbbdReqCancelAll(&endp[1]);
    sceUsbbdReqCancelAll(&endp[2]);
    pspSdkSetK1(k1);
    return 0;
}

void pspusb_clear_buffers(void)
{
    int k1 = pspSdkSetK1(0);
    rx_wr = rx_rd = 0;
    g_aborted = 0;
    sceUsbbdClearFIFO(&endp[1]);
    sceUsbbdClearFIFO(&endp[2]);
    if (g_transevent >= 0) {
        sceKernelClearEventFlag(g_transevent, ~0);
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_SPACE_AVAIL);
    }
    pspSdkSetK1(k1);
}

int pspusb_write(const void *data, int len, unsigned int timeout_us)
{
    if (!g_connected || g_aborted)
        return -1;
    if (len <= 0)
        return 0;

    int k1 = pspSdkSetK1(0);
    if (g_sendsema >= 0 && sceKernelWaitSema(g_sendsema, 1, NULL) < 0) {
        pspSdkSetK1(k1);
        return -1;
    }

    const uint8_t *src = (const uint8_t *)data;
    int remaining = len;

    while (remaining > 0) {
        if (!g_connected || g_aborted) {
            if (g_sendsema >= 0) sceKernelSignalSema(g_sendsema, 1);
            pspSdkSetK1(k1);
            return -1;
        }

        int chunk = (remaining > (int)sizeof(usb_tx_buf)) ? (int)sizeof(usb_tx_buf) : remaining;
        memcpy(usb_tx_buf, src, chunk);
        sceKernelDcacheWritebackRange(usb_tx_buf, chunk);

        memset(&g_bulkin_req, 0, sizeof(g_bulkin_req));
        g_bulkin_req.endp = &endp[1]; /* EP1 IN */
        g_bulkin_req.data = usb_tx_buf;
        g_bulkin_req.size = chunk;
        g_bulkin_req.func = bulkin_req_done;

        sceKernelClearEventFlag(g_transevent, ~USB_TRANSEVENT_BULKIN_DONE);
        int r = sceUsbbdReqSend(&g_bulkin_req);
        if (r < 0) {
            if (g_sendsema >= 0) sceKernelSignalSema(g_sendsema, 1);
            pspSdkSetK1(k1);
            return r;
        }

        u32 result = 0;
        SceUInt wait_time = timeout_us > 0 ? timeout_us : (3 * 1000 * 1000);
        r = sceKernelWaitEventFlag(g_transevent,
                                   USB_TRANSEVENT_BULKIN_DONE | USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH,
                                   PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                   &result, &wait_time);

        if (r < 0 || (result & (USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH))) {
            sceUsbbdReqCancelAll(&endp[1]);
            if (g_sendsema >= 0) sceKernelSignalSema(g_sendsema, 1);
            pspSdkSetK1(k1);
            return -1;
        }

        if (g_bulkin_req.retcode != 0 || g_bulkin_req.recvsize <= 0) {
            if (g_sendsema >= 0) sceKernelSignalSema(g_sendsema, 1);
            pspSdkSetK1(k1);
            return -1;
        }

        src += g_bulkin_req.recvsize;
        remaining -= g_bulkin_req.recvsize;
    }

    if (g_sendsema >= 0) sceKernelSignalSema(g_sendsema, 1);
    pspSdkSetK1(k1);
    return len;
}

int pspusb_read(void *data, int len, unsigned int timeout_us)
{
    uint8_t *dst = (uint8_t *)data;
    int remaining = len;
    unsigned int t0 = sceKernelGetSystemTimeLow();
    int k1 = pspSdkSetK1(0);

    if (g_recvsema >= 0 && sceKernelWaitSema(g_recvsema, 1, NULL) < 0) {
        pspSdkSetK1(k1);
        return -1;
    }

    while (remaining > 0) {
        if (!g_connected || g_aborted) {
            if (g_recvsema >= 0) sceKernelSignalSema(g_recvsema, 1);
            pspSdkSetK1(k1);
            return -1;
        }

        unsigned int avail = rx_wr - rx_rd;
        if (avail > 0) {
            unsigned int chunk = (avail > (unsigned int)remaining) ? (unsigned int)remaining : avail;
            unsigned int rd = rx_rd;
            unsigned int off = rd & (RX_RING_SIZE - 1);
            unsigned int first = RX_RING_SIZE - off;
            if (first > chunk)
                first = chunk;
            memcpy(dst, &rx_ring[off], first);
            if (chunk > first) {
                memcpy(dst + first, &rx_ring[0], chunk - first);
            }
            rx_rd = rd + chunk;
            dst += chunk;
            remaining -= chunk;

            /* Signal space available to RX thread only when transitioning from space-constrained */
            unsigned int used_before = rx_wr - rd;
            unsigned int used_after = rx_wr - (rd + chunk);
            if (used_before > (RX_RING_SIZE - USB_RX_BUF_SIZE) && used_after <= (RX_RING_SIZE - USB_RX_BUF_SIZE)) {
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_SPACE_AVAIL);
            }

            if (remaining == 0)
                break;
        }

        /* Wait for RX data */
        u32 result = 0;
        SceUInt wait_slice = 20 * 1000; /* 20 ms */
        int r = sceKernelWaitEventFlag(g_transevent,
                                       USB_TRANSEVENT_RX_DATA | USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH,
                                       PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                       &result, &wait_slice);

        if (result & (USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH)) {
            if (g_recvsema >= 0) sceKernelSignalSema(g_recvsema, 1);
            pspSdkSetK1(k1);
            return -1;
        }

        if (timeout_us > 0 && (sceKernelGetSystemTimeLow() - t0) > timeout_us) {
            if (g_recvsema >= 0) sceKernelSignalSema(g_recvsema, 1);
            pspSdkSetK1(k1);
            return (len - remaining > 0) ? (len - remaining) : -2;
        }
        (void)r;
    }

    if (g_recvsema >= 0) sceKernelSignalSema(g_recvsema, 1);
    pspSdkSetK1(k1);
    return len;
}
