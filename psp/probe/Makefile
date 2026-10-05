# PSPStream - teste do decoder H.264 de hardware
#   make        -> EBOOT.PBP (copie para ms0:/PSP/GAME/PSPStreamH264/)
# Os clipes (clips.bin) vêm de tools/h264_probe_clips.py e ficam no repositório.

TARGET = PSPStreamH264
OBJS = main.o clips.o

CFLAGS = -O2 -G0 -Wall -Wextra -Wno-unused-parameter -std=gnu11
ASFLAGS = $(CFLAGS)

BUILD_PRX = 1
LIBS = -lpspmpeg -lpspmpegbase -lpsppower

EXTRA_TARGETS = EBOOT.PBP
PSP_EBOOT_TITLE = PSPStream teste H.264

include ../pspdev.mk
include $(PSPSDK)/lib/build.mak

clips.o: clips.bin
