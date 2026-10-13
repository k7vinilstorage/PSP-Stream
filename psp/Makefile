# PSPStream - cliente PSP (pspdev/PSPSDK)
#   make            -> EBOOT.PBP
#   make dist       -> dist/PSP/GAME/PSPStream/ pronto para copiar no memory stick

TARGET = PSPStream
OBJS = src/main.o src/config.o src/net.o src/display.o src/decode.o src/stream.o src/menu.o src/audio.o src/ima.o src/usb.o src/pspstream_usb_stub.o

INCDIR =
# -MMD -MP: recompila o .c quando um .h muda (o build.mak não rastreia isso)
CFLAGS = -O2 -G0 -Wall -Wextra -Wno-unused-parameter -std=gnu11 -MMD -MP
CXXFLAGS = $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS = $(CFLAGS)

BUILD_PRX = 1

LIBDIR =
LDFLAGS =
# NÃO listar -lpspnet_inet nem -lpsputility aqui: o psp-gcc já os acrescenta
# depois da libc. Listados aqui também, os stubs de um mesmo módulo seriam
# puxados em dois blocos separados, e o carregador (PSP e PPSSPP) leria NIDs
# errados. Foi assim que socket()/connect() quebraram no primeiro teste.
LIBS = -lturbojpeg -lpspmpeg -lpspmpegbase -lpspjpeg -lpsppower -lpspaudio -lpspkubridge

EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSPStream

include pspdev.mk
include $(PSPSDK)/lib/build.mak

-include $(OBJS:.o=.d)
EXTRA_CLEAN = $(OBJS:.o=.d)

DIST_DIR = dist/PSP/GAME/PSPStream

all: usbdrv/pspstream_usb.prx

usbdrv/pspstream_usb.prx: $(wildcard usbdrv/*.c usbdrv/*.h usbdrv/*.exp) usbdrv/Makefile
	$(MAKE) -C usbdrv

.PHONY: dist clean-usbdrv
dist: EBOOT.PBP usbdrv/pspstream_usb.prx
	mkdir -p $(DIST_DIR)
	cp EBOOT.PBP $(DIST_DIR)/
	cp usbdrv/pspstream_usb.prx $(DIST_DIR)/
	[ -f $(DIST_DIR)/server.txt ] || cp server.txt.example $(DIST_DIR)/server.txt
	@echo "Copy the dist/PSP folder to the root of the memory stick. The PC IP is set on the PSP itself"
	@echo "(settings screen, 'Find the PC on the network') or in PSP/GAME/PSPStream/server.txt."

clean: clean-usbdrv

clean-usbdrv:
	$(MAKE) -C usbdrv clean
