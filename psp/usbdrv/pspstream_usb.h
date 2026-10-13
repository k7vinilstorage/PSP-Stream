#ifndef PSPSTREAM_USB_H
#define PSPSTREAM_USB_H

#include <stdint.h>

#define PSPSTREAM_USB_DRIVERNAME "PSPStreamUSBDriver"
#define PSPSTREAM_USB_VID 0x054C
#define PSPSTREAM_USB_PID 0x02D2

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Driver management (called from user-mode through kernel exports):
 */
int pspusb_start(void);
int pspusb_stop(void);
int pspusb_is_attached(void);
int pspusb_wait_attached(unsigned int timeout_us);
int pspusb_write(const void *data, int len, unsigned int timeout_us);
int pspusb_read(void *data, int len, unsigned int timeout_us);
int pspusb_abort(void);
void pspusb_clear_buffers(void);

#ifdef __cplusplus
}
#endif

#endif /* PSPSTREAM_USB_H */
