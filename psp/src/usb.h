#ifndef PSPSTREAM_USB_H_USER
#define PSPSTREAM_USB_H_USER

#include <stdint.h>

/* Inicia o módulo USB e ativa o driver. Devolve 0 se OK, < 0 se erro. */
int usb_init(const char *dir);

/* 1 se o cabo USB está conectado e o PC configurou a interface. */
int usb_is_attached(void);

/* Espera o PC conectar por até timeout_us (0 = bloqueante). Devolve 1 se conectado, 0 se timeout, < 0 se erro. */
int usb_wait_attached(unsigned int timeout_us);

/* Envia dados para o PC (Bulk IN). Devolve 0 se OK, < 0 se erro. */
int usb_send_all(const void *buf, int len);

/* Recebe dados do PC (Bulk OUT). Devolve 0 se OK, < 0 se erro. */
int usb_recv_all(void *buf, int len);

/* Desbloqueia operações pendentes. */
void usb_abort(void);

/* Limpa buffers. */
void usb_clear(void);

/* Encerra e descarrega o driver. */
void usb_term(void);

#endif
