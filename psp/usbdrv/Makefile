TARGET = pspstream_usb
OBJS = main.o exports.o

USE_KERNEL_LIBS = 1

INCDIR =
CFLAGS = -O2 -G0 -Wall -Wextra -Wno-unused-parameter -std=gnu11
CXXFLAGS = $(CFLAGS) -fno-exceptions -fno-rtti
ASFLAGS = $(CFLAGS)

LIBDIR =
LIBS = -lpspusbbus_driver -lpspusb_driver

PSPSDK = $(shell psp-config --pspsdk-path)
include $(PSPSDK)/lib/build_prx.mak
