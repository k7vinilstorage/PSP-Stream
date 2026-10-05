# Auxiliar da captura KMS (--source kms). No Fedora: sudo dnf install gcc libdrm-devel
#   make            compila
#   make cap        dá a permissão de ler a tela (sudo); refaça depois de cada make
CFLAGS ?= -O2 -Wall -Wextra
DRM_CFLAGS := $(shell pkg-config --cflags libdrm)
DRM_LIBS := $(shell pkg-config --libs libdrm)

pspstream-kms: pspstream-kms.c
	$(CC) $(CFLAGS) $(DRM_CFLAGS) -o $@ $< $(DRM_LIBS)

cap: pspstream-kms
	sudo setcap cap_sys_admin+ep pspstream-kms
	getcap pspstream-kms

clean:
	rm -f pspstream-kms

.PHONY: cap clean
