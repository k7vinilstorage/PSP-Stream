# PSPStream - cliente PSP (pspdev/PSPSDK)
#   make            -> EBOOT.PBP
#   make dist       -> dist/PSP/GAME/PSPStream/ pronto para copiar no memory stick

TARGET = PSPStream
OBJS = src/main.o src/config.o src/net.o src/display.o src/decode.o

INCDIR =
CFLAGS = -O2 -G0 -Wall -Wextra -Wno-unused-parameter -std=gnu11
CXXFLAGS = $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS = $(CFLAGS)

BUILD_PRX = 1

LIBDIR =
LDFLAGS =
# NÃO listar -lpspnet_inet nem -lpsputility aqui: o psp-gcc já os acrescenta
# depois da libc. Listados aqui também, os stubs de um mesmo módulo seriam
# puxados em dois blocos separados, e o carregador (PSP e PPSSPP) leria NIDs
# errados. Foi assim que socket()/connect() quebraram no primeiro teste.
LIBS = -lturbojpeg -lpspjpeg -lpsppower

EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSPStream

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build.mak

DIST_DIR = dist/PSP/GAME/PSPStream

.PHONY: dist
dist: EBOOT.PBP
	mkdir -p $(DIST_DIR)
	cp EBOOT.PBP $(DIST_DIR)/
	[ -f $(DIST_DIR)/server.txt ] || cp server.txt.example $(DIST_DIR)/server.txt
	@echo "Copie a pasta dist/PSP para a raiz do memory stick e edite PSP/GAME/PSPStream/server.txt"
