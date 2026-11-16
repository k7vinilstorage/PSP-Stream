# Proxy universal libSDL2-2.0.0.dylib para CrossOver no macOS (injeta controle Xbox 360 virtual para winebus.so).
# Compila com clang do Xcode Command Line Tools: make -C tools/crossover
CFLAGS ?= -O2 -Wall -Wextra

libSDL2-2.0.0.dylib: pspstream-sdl2.c
	$(CC) $(CFLAGS) -arch x86_64 -arch arm64 -dynamiclib -install_name @rpath/libSDL2-2.0.0.dylib $(LDFLAGS) -o $@ $<
	codesign -f -s - $@

clean:
	rm -f libSDL2-2.0.0.dylib

.PHONY: clean
