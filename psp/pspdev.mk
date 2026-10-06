# Acha o pspdev mesmo sem o export no shell (o zsh, por exemplo, não lê o
# ~/.bashrc). Ordem: psp-config no PATH, $PSPDEV, ~/pspdev,
# /usr/local/pspdev. Outro lugar: make PSPDEV=/caminho/do/pspdev
PSPCONFIG := $(shell command -v psp-config 2>/dev/null)
ifeq ($(PSPCONFIG),)
  PSPDEV ?= $(firstword $(wildcard $(HOME)/pspdev /usr/local/pspdev))
  PSPCONFIG := $(wildcard $(PSPDEV)/bin/psp-config)
  export PSPDEV
  export PATH := $(PSPDEV)/bin:$(PATH)
endif
ifeq ($(PSPCONFIG),)
  $(error pspdev not found: psp-config is neither in PATH nor in ~/pspdev. Install it as the wiki says (Installation) or run make PSPDEV=/path/to/pspdev)
endif
PSPSDK := $(shell $(PSPCONFIG) --pspsdk-path)
