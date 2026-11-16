/*
 * PSPStream USB Kernel Driver
 *
 * Exposes a 2-interface USB Composite Device (VID 0x054C, PID 0x02D2):
 *   Interface 0 (0xFF Vendor): Video / audio / control stream
 *     EP 1 (0x81) Bulk IN:       PSP -> PC (requests, controls, 52 bytes)
 *     EP 2 (0x02) Bulk OUT:      PC -> PSP (video frames, audio packets)
 *   Interface 1 (0x03 HID):    Native USB Gamepad (works on macOS without drivers)
 *     EP 3 (0x83) Interrupt IN:  PSP -> PC (8-byte PspHidReport)
 *
 * Implements a thread-context bulk receive pipeline feeding a 256 KB kernel ring buffer
 * and a dedicated thread for non-blocking HID interrupt IN reports.
 * Callbacks in interrupt context only copy short state / signal event flags.
 */
#include <pspkerror.h>
#include <pspkernel.h>
#include <pspsdk.h>
#include <pspusb.h>
#include <pspusbbus.h>
#include <string.h>

#include "pspstream_usb.h"

PSP_MODULE_INFO("PSPStreamUSB", PSP_MODULE_KERNEL, 1, 2);

#define USB_RX_BUF_SIZE (32 * 1024)
#define USB_TX_BUF_SIZE (16 * 1024)
#define RX_RING_SIZE    (256 * 1024)

/* USB Request constants */
#define USB_DT_HID              0x21
#define USB_DT_REPORT           0x22
#define USB_REQ_GET_DESCRIPTOR  0x06
#define HID_REQ_GET_REPORT      0x01
#define HID_REQ_SET_IDLE        0x0A
#define HID_REQ_SET_PROTOCOL    0x0B

/* Vendor EP0 commands sent by server/usb_link.py for full mapped Xbox/gamepad state */
#define PSP_HID_SET_LOW         0x10 /* wValue = buttons_hat, wIndex = lx | (ly << 8) */
#define PSP_HID_SET_HIGH_COMMIT 0x11 /* wValue = rx | (ry << 8), wIndex = lt | (rt << 8) */

/* PSP controller bit masks (from pspctrl.h / server/inject.py) */
#define PSP_BTN_SELECT   0x0001
#define PSP_BTN_START    0x0008
#define PSP_BTN_UP       0x0010
#define PSP_BTN_RIGHT    0x0020
#define PSP_BTN_DOWN     0x0040
#define PSP_BTN_LEFT     0x0080
#define PSP_BTN_L        0x0100
#define PSP_BTN_R        0x0200
#define PSP_BTN_TRIANGLE 0x1000
#define PSP_BTN_CIRCLE   0x2000
#define PSP_BTN_CROSS    0x4000
#define PSP_BTN_SQUARE   0x8000

struct PspHidReport {
    uint16_t buttons_hat; /* bits 0..10: A,B,X,Y,LB,RB,BACK,START,GUIDE,L3,R3; bit 11: pad; bits 12..15: hat */
    uint8_t lx;
    uint8_t ly;
    uint8_t rx;
    uint8_t ry;
    uint8_t lt;
    uint8_t rt;
} __attribute__((packed));

_Static_assert(sizeof(struct PspHidReport) == 8, "PspHidReport must be 8 bytes");

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

enum UsbHidEvents {
    USB_HIDEVENT_SEND   = 0x01,
    USB_HIDEVENT_DONE   = 0x02,
    USB_HIDEVENT_DETACH = 0x04,
    USB_HIDEVENT_EXIT   = 0x08,
};

/* Kernel sceUsbbd configuration / interface / endpoint descriptor layout */
struct UsbEpEntry {
    struct EndpointDescriptor desc;
    uint8_t pad;
    const void *extra;
    int extraLength;
} __attribute__((packed, aligned(4)));

struct UsbIfEntry {
    struct InterfaceDescriptor desc;
    uint8_t pad[3];
    struct UsbEpEntry *endpoints;
    const void *extra;
    int extraLength;
} __attribute__((packed, aligned(4)));

struct UsbIfSettings {
    struct UsbIfEntry *descriptors;
    unsigned int alternateSetting;
    unsigned int numDescriptors;
};

struct UsbCfgEntry {
    struct ConfigDescriptor desc;
    uint8_t pad[3];
    struct UsbIfSettings *settings;
    const void *extra;
    int extraLength;
} __attribute__((packed, aligned(4)));

struct UsbConfBundle {
    struct UsbCfgEntry *pconfdesc;
    struct UsbIfSettings *pinterfaces;
    struct UsbIfEntry *pinterdesc;
    struct UsbEpEntry *pendp;
};

struct UsbDevEntry {
    struct DeviceDescriptor desc;
    uint8_t pad[2];
} __attribute__((packed, aligned(4)));

_Static_assert(sizeof(struct UsbEpEntry) == 16, "UsbEpEntry size");
_Static_assert(sizeof(struct UsbIfEntry) == 24, "UsbIfEntry size");
_Static_assert(sizeof(struct UsbIfSettings) == 12, "UsbIfSettings size");
_Static_assert(sizeof(struct UsbCfgEntry) == 24, "UsbCfgEntry size");
_Static_assert(sizeof(struct UsbConfBundle) == 16, "UsbConfBundle size");
_Static_assert(sizeof(struct UsbDevEntry) == 20, "UsbDevEntry size");

static SceUID g_main_thid = -1;
static SceUID g_rx_thid = -1;
static SceUID g_hid_thid = -1;
static SceUID g_mainevent = -1;
static SceUID g_transevent = -1;
static SceUID g_hidevent = -1;
static SceUID g_sendsema = -1;
static SceUID g_recvsema = -1;

static volatile int g_connected = 0;
static volatile int g_aborted = 0;
static volatile int g_driver_started = 0;

/* HID state & EP0 buffers (64-byte aligned for DMA & cache lines) */
static struct PspHidReport g_hid_cur = { 0xF000, 128, 128, 128, 128, 0, 0 };
static volatile uint16_t g_host_buttons_hat = 0xF000;
static volatile uint8_t g_host_lx = 128;
static volatile uint8_t g_host_ly = 128;
static volatile uint32_t g_hid_host_ts = 0;
static volatile int g_hid_has_host = 0;
static volatile int g_hid_dirty = 0;
static volatile int g_hid_busy = 0;

static uint8_t usb_hid_tx_buf[64] __attribute__((aligned(64)));
static uint8_t usb_hid_ep0_buf[64] __attribute__((aligned(64)));
static struct UsbdDeviceReq g_ep0_req;
static struct UsbdDeviceReq g_hid_req;

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

/* Standard USB HID Gamepad Report Descriptor (matches 8-byte PspHidReport) */
static const uint8_t hid_report_descriptor_src[] = {
    0x05, 0x01,       /* Usage Page (Generic Desktop Ctrls) */
    0x09, 0x05,       /* Usage (Game Pad) */
    0xA1, 0x01,       /* Collection (Application) */
    0x05, 0x09,       /*   Usage Page (Button) */
    0x19, 0x01,       /*   Usage Minimum (0x01) */
    0x29, 0x0B,       /*   Usage Maximum (0x0B) - 11 buttons */
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x25, 0x01,       /*   Logical Maximum (1) */
    0x75, 0x01,       /*   Report Size (1) */
    0x95, 0x0B,       /*   Report Count (11) */
    0x81, 0x02,       /*   Input (Data,Var,Abs) */
    0x75, 0x01,       /*   Report Size (1) */
    0x95, 0x01,       /*   Report Count (1) - 1 bit padding */
    0x81, 0x03,       /*   Input (Const,Var,Abs) */
    0x05, 0x01,       /*   Usage Page (Generic Desktop Ctrls) */
    0x09, 0x39,       /*   Usage (Hat switch) */
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x25, 0x07,       /*   Logical Maximum (7) */
    0x35, 0x00,       /*   Physical Minimum (0) */
    0x46, 0x3B, 0x01, /*   Physical Maximum (315) */
    0x65, 0x14,       /*   Unit (Degrees) */
    0x75, 0x04,       /*   Report Size (4) */
    0x95, 0x01,       /*   Report Count (1) */
    0x81, 0x42,       /*   Input (Data,Var,Abs,Null State) */
    0x35, 0x00,       /*   Physical Minimum (0) */
    0x45, 0x00,       /*   Physical Maximum (0) */
    0x65, 0x00,       /*   Unit (None) */
    0x09, 0x30,       /*   Usage (X)  - Left Stick X */
    0x09, 0x31,       /*   Usage (Y)  - Left Stick Y */
    0x09, 0x33,       /*   Usage (Rx) - Right Stick X */
    0x09, 0x34,       /*   Usage (Ry) - Right Stick Y */
    0x09, 0x32,       /*   Usage (Z)  - Left Trigger */
    0x09, 0x35,       /*   Usage (Rz) - Right Trigger */
    0x15, 0x00,       /*   Logical Minimum (0) */
    0x26, 0xFF, 0x00, /*   Logical Maximum (255) */
    0x75, 0x08,       /*   Report Size (8) */
    0x95, 0x06,       /*   Report Count (6) */
    0x81, 0x02,       /*   Input (Data,Var,Abs) */
    0xC0              /* End Collection */
};

/* Padded to multiple of 64 bytes for DMA cache writeback */
static uint8_t usb_hid_report_desc_buf[128] __attribute__((aligned(64)));

/* 9-byte USB HID class descriptor */
static uint8_t usb_hid_class_desc[64] __attribute__((aligned(64))) = {
    0x09,                         /* bLength */
    USB_DT_HID,                   /* bDescriptorType = 0x21 */
    0x11, 0x01,                   /* bcdHID = 1.11 */
    0x00,                         /* bCountryCode */
    0x01,                         /* bNumDescriptors */
    USB_DT_REPORT,                /* bDescriptorType = 0x22 */
    sizeof(hid_report_descriptor_src) & 0xFF,
    (sizeof(hid_report_descriptor_src) >> 8) & 0xFF,
};

#define HID_DESC_LEN 9
#define TOTAL_CONFIG_LEN (9 + 9 + 7 + 7 + 9 + HID_DESC_LEN + 7) /* 57 bytes */

/* Hi-Speed descriptors */
static struct UsbDevEntry devdesc_hi = {
    .desc = {
        .bLength = 18,
        .bDescriptorType = 0x01,
        .bcdUSB = 0x200,
        .bDeviceClass = 0,
        .bDeviceSubClass = 0,
        .bDeviceProtocol = 0,
        .bMaxPacketSize = 64,
        .idVendor = PSPSTREAM_USB_VID,
        .idProduct = PSPSTREAM_USB_PID,
        .bcdDevice = 0x101,
        .iManufacturer = 0,
        .iProduct = 1,
        .iSerialNumber = 0,
        .bNumConfigurations = 1
    }
};

static struct UsbEpEntry endps_hi[3] = {
    { { 7, 5, 0x81, 2, 512, 0 }, 0, NULL, 0 }, /* EP1 Bulk IN */
    { { 7, 5, 0x02, 2, 512, 0 }, 0, NULL, 0 }, /* EP2 Bulk OUT */
    { { 7, 5, 0x83, 3,  64, 4 }, 0, NULL, 0 }, /* EP3 Interrupt IN (1 ms) */
};

static struct UsbIfEntry ifaces_hi[2] = {
    { { 9, 4, 0, 0, 2, 0xFF, 0x01, 0xFF, 1 }, { 0 }, &endps_hi[0], NULL, 0 },
    { { 9, 4, 1, 0, 1, 0x03, 0x00, 0x00, 1 }, { 0 }, &endps_hi[2], usb_hid_class_desc, HID_DESC_LEN },
};

static struct UsbIfSettings settings_hi[2] = {
    { &ifaces_hi[0], 0, 1 },
    { &ifaces_hi[1], 0, 1 },
};

static struct UsbCfgEntry confdesc_hi = {
    .desc = { 9, 2, TOTAL_CONFIG_LEN, 2, 1, 0, 0xC0, 0 },
    .settings = settings_hi,
    .extra = NULL,
    .extraLength = 0
};

static struct UsbConfBundle bundle_hi = {
    &confdesc_hi,
    settings_hi,
    ifaces_hi,
    endps_hi
};

/* Full-Speed descriptors */
static struct UsbDevEntry devdesc_full = {
    .desc = {
        .bLength = 18,
        .bDescriptorType = 0x01,
        .bcdUSB = 0x200,
        .bDeviceClass = 0,
        .bDeviceSubClass = 0,
        .bDeviceProtocol = 0,
        .bMaxPacketSize = 64,
        .idVendor = PSPSTREAM_USB_VID,
        .idProduct = PSPSTREAM_USB_PID,
        .bcdDevice = 0x101,
        .iManufacturer = 0,
        .iProduct = 1,
        .iSerialNumber = 0,
        .bNumConfigurations = 1
    }
};

static struct UsbEpEntry endps_full[3] = {
    { { 7, 5, 0x81, 2, 64, 0 }, 0, NULL, 0 }, /* EP1 Bulk IN */
    { { 7, 5, 0x02, 2, 64, 0 }, 0, NULL, 0 }, /* EP2 Bulk OUT */
    { { 7, 5, 0x83, 3, 64, 8 }, 0, NULL, 0 }, /* EP3 Interrupt IN (8 ms) */
};

static struct UsbIfEntry ifaces_full[2] = {
    { { 9, 4, 0, 0, 2, 0xFF, 0x01, 0xFF, 1 }, { 0 }, &endps_full[0], NULL, 0 },
    { { 9, 4, 1, 0, 1, 0x03, 0x00, 0x00, 1 }, { 0 }, &endps_full[2], usb_hid_class_desc, HID_DESC_LEN },
};

static struct UsbIfSettings settings_full[2] = {
    { &ifaces_full[0], 0, 1 },
    { &ifaces_full[1], 0, 1 },
};

static struct UsbCfgEntry confdesc_full = {
    .desc = { 9, 2, TOTAL_CONFIG_LEN, 2, 1, 0, 0xC0, 0 },
    .settings = settings_full,
    .extra = NULL,
    .extraLength = 0
};

static struct UsbConfBundle bundle_full = {
    &confdesc_full,
    settings_full,
    ifaces_full,
    endps_full
};

/* UTF-16LE String descriptor for "PSPStream" */
static unsigned char strp[] = {
    20, 0x03,
    'P', 0, 'S', 0, 'P', 0, 'S', 0, 't', 0, 'r', 0, 'e', 0, 'a', 0, 'm', 0
};

/* Endpoint blocks (0: control, 1: EP1 Bulk IN, 2: EP2 Bulk OUT, 3: EP3 Interrupt IN) */
static struct UsbEndpoint endp[4] = {
    { 0, 0, 0 },
    { 1, 0, 0 },
    { 2, 0, 0 },
    { 3, 0, 0 },
};

/* Expect 2 interfaces (0: vendor bulk, 1: HID gamepad) assigned by sceUsbbd */
static struct UsbInterface intp = {
    -1, 0, 2
};

/* Forward declaration */
extern struct UsbDriver g_driver;

static int ep0_send(const void *buf, int len, int req_max)
{
    int n = (req_max > 0 && req_max < len) ? req_max : len;
    if (n <= 0)
        return 0;
    sceKernelDcacheWritebackRange((void *)buf, n);
    memset(&g_ep0_req, 0, sizeof(g_ep0_req));
    g_ep0_req.endp = &endp[0];
    g_ep0_req.data = (void *)buf;
    g_ep0_req.size = n;
    return sceUsbbdReqSend(&g_ep0_req);
}

static uint16_t raw_psp_buttons_hat(uint32_t b)
{
    uint16_t out = 0;
    if (b & PSP_BTN_CROSS)    out |= 1 << 0; /* A */
    if (b & PSP_BTN_CIRCLE)   out |= 1 << 1; /* B */
    if (b & PSP_BTN_SQUARE)   out |= 1 << 2; /* X */
    if (b & PSP_BTN_TRIANGLE) out |= 1 << 3; /* Y */
    if (b & PSP_BTN_L)        out |= 1 << 4; /* LB */
    if (b & PSP_BTN_R)        out |= 1 << 5; /* RB */
    if (b & PSP_BTN_SELECT)   out |= 1 << 6; /* BACK */
    if (b & PSP_BTN_START)    out |= 1 << 7; /* START */

    int dx = ((b & PSP_BTN_RIGHT) ? 1 : 0) - ((b & PSP_BTN_LEFT) ? 1 : 0);
    int dy = ((b & PSP_BTN_DOWN) ? 1 : 0) - ((b & PSP_BTN_UP) ? 1 : 0);
    uint16_t hat = 0xF;
    if (dy < 0)      hat = (dx < 0) ? 7 : (dx > 0) ? 1 : 0;
    else if (dy > 0) hat = (dx < 0) ? 5 : (dx > 0) ? 3 : 4;
    else if (dx < 0) hat = 6;
    else if (dx > 0) hat = 2;

    return out | (uint16_t)(hat << 12);
}

static int usb_request(int arg1, int arg2, struct DeviceRequest *req)
{
    (void)arg1;
    if (!req || arg2 < 0)
        return 0;

    switch (req->bmRequestType) {
    case 0x81: /* Device-to-Host, Standard, Interface */
        if (req->bRequest == USB_REQ_GET_DESCRIPTOR) {
            uint8_t dtype = (req->wValue >> 8) & 0xFF;
            if (dtype == USB_DT_REPORT)
                ep0_send(usb_hid_report_desc_buf, (int)sizeof(hid_report_descriptor_src), req->wLength);
            else if (dtype == USB_DT_HID)
                ep0_send(usb_hid_class_desc, HID_DESC_LEN, req->wLength);
        }
        break;
    case 0xA1: /* Device-to-Host, Class, Interface */
        if (req->bRequest == HID_REQ_GET_REPORT) {
            memcpy(usb_hid_ep0_buf, &g_hid_cur, sizeof(g_hid_cur));
            ep0_send(usb_hid_ep0_buf, (int)sizeof(g_hid_cur), req->wLength);
        }
        break;
    case 0x21: /* Host-to-Device, Class, Interface (SET_IDLE / SET_PROTOCOL) */
        break;
    case 0x40: /* Host-to-Device, Vendor, Device */
        if (req->bRequest == PSP_HID_SET_LOW) {
            g_host_buttons_hat = req->wValue;
            g_host_lx = req->wIndex & 0xFF;
            g_host_ly = (req->wIndex >> 8) & 0xFF;
        } else if (req->bRequest == PSP_HID_SET_HIGH_COMMIT) {
            g_hid_cur.buttons_hat = g_host_buttons_hat;
            g_hid_cur.lx = g_host_lx;
            g_hid_cur.ly = g_host_ly;
            g_hid_cur.rx = req->wValue & 0xFF;
            g_hid_cur.ry = (req->wValue >> 8) & 0xFF;
            g_hid_cur.lt = req->wIndex & 0xFF;
            g_hid_cur.rt = (req->wIndex >> 8) & 0xFF;
            g_hid_host_ts = sceKernelGetSystemTimeLow();
            g_hid_has_host = 1;
            g_hid_dirty = 1;
            if (g_hidevent >= 0)
                sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_SEND);
        }
        break;
    default:
        break;
    }
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

static int hid_req_done(struct UsbdDeviceReq *req, int arg2, int arg3)
{
    (void)req;
    if (g_hidevent >= 0)
        sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_DONE);
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
            g_hid_busy = 0;
            g_hid_has_host = 0;
            sceUsbbdReqCancelAll(&endp[1]);
            sceUsbbdReqCancelAll(&endp[2]);
            sceUsbbdReqCancelAll(&endp[3]);
            sceKernelClearEventFlag(g_mainevent, ~USB_EVENT_ATTACHED);
            if (g_transevent >= 0)
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_DETACH);
            if (g_hidevent >= 0)
                sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_DETACH);
        }

        if (result & USB_EVENT_ATTACH) {
            rx_wr = rx_rd = 0;
            g_aborted = 0;
            g_hid_cur = (struct PspHidReport){ 0xF000, 128, 128, 128, 128, 0, 0 };
            g_host_buttons_hat = 0xF000;
            g_host_lx = 128;
            g_host_ly = 128;
            g_hid_has_host = 0;
            g_hid_busy = 0;
            g_hid_dirty = 1;
            g_connected = 1;
            sceKernelSetEventFlag(g_mainevent, USB_EVENT_ATTACHED);
            if (g_transevent >= 0)
                sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_SPACE_AVAIL);
            if (g_hidevent >= 0)
                sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_SEND);
        }
    }
    return 0;
}

/* Dedicated thread queueing non-blocking HID interrupt IN reports on EP3 */
static int usb_hid_thread(SceSize size, void *argp)
{
    u32 result = 0;
    while (g_driver_started) {
        int ret = sceKernelWaitEventFlag(g_hidevent,
                                         USB_HIDEVENT_SEND | USB_HIDEVENT_DONE |
                                         USB_HIDEVENT_DETACH | USB_HIDEVENT_EXIT,
                                         PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                         &result, NULL);
        if (ret < 0 || (result & USB_HIDEVENT_EXIT))
            break;

        if (result & USB_HIDEVENT_DETACH) {
            g_hid_busy = 0;
            g_hid_dirty = 0;
            continue;
        }
        if (result & USB_HIDEVENT_DONE)
            g_hid_busy = 0;

        if (!g_connected || g_hid_busy || !g_hid_dirty)
            continue;

        int intr = sceKernelCpuSuspendIntr();
        struct PspHidReport snap = g_hid_cur;
        g_hid_dirty = 0;
        sceKernelCpuResumeIntr(intr);

        memcpy(usb_hid_tx_buf, &snap, sizeof(snap));
        sceKernelDcacheWritebackRange(usb_hid_tx_buf, sizeof(snap));

        memset(&g_hid_req, 0, sizeof(g_hid_req));
        g_hid_req.endp = &endp[3]; /* EP3 Interrupt IN */
        g_hid_req.data = usb_hid_tx_buf;
        g_hid_req.size = sizeof(snap);
        g_hid_req.func = hid_req_done;

        g_hid_busy = 1;
        if (sceUsbbdReqSend(&g_hid_req) < 0)
            g_hid_busy = 0;
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

        /* Wait for completion, abort, or detach. */
        u32 result = 0;
        int cancel = 0;
        for (;;) {
            SceUInt wait_time = 100 * 1000; /* 100 ms */
            result = 0;
            r = sceKernelWaitEventFlag(g_transevent,
                                       USB_TRANSEVENT_BULKOUT_DONE | USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH,
                                       PSP_EVENT_WAITOR | PSP_EVENT_WAITCLEAR,
                                       &result, &wait_time);
            if (r >= 0 && (result & USB_TRANSEVENT_BULKOUT_DONE))
                break;
            if ((r >= 0 && (result & (USB_TRANSEVENT_ABORT | USB_TRANSEVENT_DETACH))) ||
                !g_driver_started || !g_connected || g_aborted) {
                cancel = 1;
                break;
            }
            if (r < 0 && r != (int)SCE_KERNEL_ERROR_WAIT_TIMEOUT) {
                cancel = 1;
                break;
            }
        }
        if (cancel) {
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
    memset(usb_hid_report_desc_buf, 0, sizeof(usb_hid_report_desc_buf));
    memcpy(usb_hid_report_desc_buf, hid_report_descriptor_src, sizeof(hid_report_descriptor_src));
    sceKernelDcacheWritebackRange(usb_hid_report_desc_buf, sizeof(usb_hid_report_desc_buf));
    sceKernelDcacheWritebackRange(usb_hid_class_desc, sizeof(usb_hid_class_desc));

    g_driver.devp_hi = &devdesc_hi;
    g_driver.confp_hi = &bundle_hi;
    g_driver.devp = &devdesc_full;
    g_driver.confp = &bundle_full;

    g_mainevent = sceKernelCreateEventFlag("PSUSB_MainEvent", 0x200, 0, NULL);
    if (g_mainevent < 0)
        return g_mainevent;

    g_transevent = sceKernelCreateEventFlag("PSUSB_TransEvent", 0x200, 0, NULL);
    if (g_transevent < 0)
        return g_transevent;

    g_hidevent = sceKernelCreateEventFlag("PSUSB_HidEvent", 0x200, 0, NULL);
    if (g_hidevent < 0)
        return g_hidevent;

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

    g_hid_thid = sceKernelCreateThread("PSUSB_HidThread", usb_hid_thread, 0x16, 0x2000, 0, NULL);
    if (g_hid_thid >= 0)
        sceKernelStartThread(g_hid_thid, 0, NULL);

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
    g_hid_busy = 0;

    if (g_transevent >= 0)
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_ABORT);
    if (g_hidevent >= 0)
        sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_EXIT);
    if (g_mainevent >= 0)
        sceKernelSetEventFlag(g_mainevent, USB_EVENT_DETACH);

    if (g_rx_thid >= 0) {
        sceKernelTerminateDeleteThread(g_rx_thid);
        g_rx_thid = -1;
    }
    if (g_hid_thid >= 0) {
        sceKernelTerminateDeleteThread(g_hid_thid);
        g_hid_thid = -1;
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
    if (g_hidevent >= 0) {
        sceKernelDeleteEventFlag(g_hidevent);
        g_hidevent = -1;
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
    .endpoints = 4,
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

static int user_buf_ok(u32 k1, const void *buf, int len)
{
    u32 addr = (u32)buf;
    return len >= 0 && (int)((addr | (addr + (u32)len) | (u32)len) & k1) >= 0;
}

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
    g_hid_busy = 0;
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
        sceKernelClearEventFlag(g_transevent, 0); /* clears with AND: 0 clears every bit */
        sceKernelSetEventFlag(g_transevent, USB_TRANSEVENT_SPACE_AVAIL);
    }
    pspSdkSetK1(k1);
}

static void maybe_update_hid_fallback(const uint8_t *src, int len)
{
    if (len < 12)
        return;
    /* Protocol v5 request packet starts with "PSC5" + buttons(LE32) + lx + ly */
    if (src[0] != 'P' || src[1] != 'S' || src[2] != 'C' || src[3] != '5')
        return;
    uint32_t now = sceKernelGetSystemTimeLow();
    if (g_hid_has_host && (now - g_hid_host_ts) <= 2000000U)
        return;
    uint32_t btns = (uint32_t)src[4] | ((uint32_t)src[5] << 8) |
                    ((uint32_t)src[6] << 16) | ((uint32_t)src[7] << 24);
    struct PspHidReport next = {
        raw_psp_buttons_hat(btns),
        src[8], src[9],
        128, 128, 0, 0
    };
    int intr = sceKernelCpuSuspendIntr();
    int changed = memcmp(&g_hid_cur, &next, sizeof(next)) != 0;
    if (changed) {
        g_hid_cur = next;
        g_hid_dirty = 1;
    }
    sceKernelCpuResumeIntr(intr);
    if (changed && g_hidevent >= 0)
        sceKernelSetEventFlag(g_hidevent, USB_HIDEVENT_SEND);
}

int pspusb_write(const void *data, int len, unsigned int timeout_us)
{
    if (!g_connected || g_aborted)
        return -1;
    if (len <= 0)
        return 0;

    int k1 = pspSdkSetK1(0);
    if (!user_buf_ok(k1, data, len)) {
        pspSdkSetK1(k1);
        return -1;
    }
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
        if (src == (const uint8_t *)data)
            maybe_update_hid_fallback(usb_tx_buf, chunk);
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
    if (!user_buf_ok(k1, data, len)) {
        pspSdkSetK1(k1);
        return -1;
    }

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
