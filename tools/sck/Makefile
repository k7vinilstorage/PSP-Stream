# Auxiliar nativo ScreenCaptureKit para macOS 13+ (tela a 60 fps e áudio do sistema sem BlackHole).
# Compila com clang do Xcode Command Line Tools: make -C tools/sck
CFLAGS ?= -O2 -Wall -Wextra
FRAMEWORKS = -framework Foundation -framework ScreenCaptureKit -framework CoreMedia -framework CoreVideo

pspstream-sck: pspstream-sck.m
	$(CC) $(CFLAGS) -fobjc-arc $(LDFLAGS) -o $@ $< $(FRAMEWORKS)

clean:
	rm -f pspstream-sck

.PHONY: clean
