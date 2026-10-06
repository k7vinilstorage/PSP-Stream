Como o projeto é compilado, testado e publicado, e onde fica cada coisa no
código. As escolhas de projeto estão em [Decisões técnicas](Decisões-técnicas)
e o formato das mensagens, em [Protocolo](Protocolo).

## Builds e releases (GitHub Actions)

[`.github/workflows/build.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/build.yml),
a cada push e pull request:
- compila o EBOOT com o pspdev do Ubuntu;
- roda os testes e o `ruff`;
- gera o `.deb` (num Ubuntu 22.04) e o `.rpm` (num contêiner Fedora 43), e
  instala cada um para testar (`packaging/smoke-test.sh`: `--check`, stream
  com o PSP falso, interface web).

Os arquivos ficam nos "artifacts" da execução. Um push na `main` refaz a
pré-release `nightly`. O job "imagem Docker" compila a imagem do servidor,
a testa contra o Wolf falso e confere os compose e o instalador
(`packaging/docker-test.sh`, `compose-check.sh`); num push na `main`, publica
`ghcr.io/k7vinilstorage/pspstream:nightly`, e numa tag, `:1.2` e
`:latest`. Na primeira publicação, o pacote do ghcr.io nasce privado: em
GitHub → perfil → *Packages* → `pspstream` → *Package settings*, mude a
visibilidade para *Public*, para baixar sem login.

Uma versão estável sai de uma tag:

```sh
# no CHANGELOG.md, "## 1.2 (em desenvolvimento)" vira "## 1.2"; VERSION em
# server/pspstream.py e PSPSTREAM_VERSION em psp/src/version.h
git tag v1.2 && git push origin v1.2
```

A release usa a seção da versão no `CHANGELOG.md` como notas.

Para gerar os pacotes à mão: `packaging/build-deb.sh` (precisa de
`dpkg-deb`, `gcc`, `make`, `pkg-config` e `libdrm-dev`) e
`packaging/build-rpm.sh` (`rpm-build`, `gcc`, `make`, `libdrm-devel`). A
estrutura instalada é a mesma nos dois (`packaging/install-tree.sh`).

## Esta wiki

As páginas ficam na pasta
[`wiki/`](https://github.com/k7vinilstorage/PSP-Stream/tree/main/wiki) do
repositório, e o workflow
[`wiki.yml`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/.github/workflows/wiki.yml)
as publica aqui a cada push na `main` que mude a pasta. Edite lá, num pull
request: o que for mudado direto na wiki é substituído na publicação
seguinte.

- O nome do arquivo é o nome da página (`Uso-no-PSP.md` vira "Uso no PSP").
- Entre páginas, o link é o nome, sem `.md`: `[Controles](Controles)` ou
  `[x](Wolf#10-problemas)`. Para arquivos do repositório, o endereço
  completo (`https://github.com/k7vinilstorage/PSP-Stream/blob/main/...`).
- `tests/test_docs.py` confere os links do README e da wiki (páginas,
  títulos e arquivos) e que toda página está na Home e no menu lateral
  (`_Sidebar.md`).

Na primeira vez, a wiki precisa existir: em *Settings* → *Features*, marque
*Wikis*, abra a aba *Wiki* e crie uma página qualquer (ela é substituída).
Depois, rode o workflow em *Actions* → *wiki* → *Run workflow*.

## Testes

```sh
python3 -m unittest discover tests                 # servidor: protocolo, encoders, controles, captura, links da documentação
python3 server/pspstream.py --source test &
python3 tools/fake_client.py --transport udp --h264p --seconds 10 --kbps 450 --decode-ms 11
```

`tools/fake_client.py` imita as threads do PSP (fila em ordem, NACK, IDR,
pedido antecipado e repetido, `--prefetch auto|on|off` como no `server.txt`,
janela de 2 frames; `--no-window` tira a janela) e confere que nenhum frame
P é decodificado sem o anterior; `--kbps`, `--rtt-ms`, `--rtt-jitter-ms`
(ida e volta oscilando, como no Wi-Fi), `--loss`, `--loss-up`,
`--loss-burst-ms` (rajadas de interferência) e `--decode-ms` simulam o Wi-Fi
e o PSP, e o resumo conta os engasgos. `FAKE_TRACE=1` mostra cada pedido,
pedaço, perda e NACK. Os números dele são simulados.

**PPSSPPHeadless** (compile o PPSSPP com `cmake -DHEADLESS=ON`; o H.264
precisa de `tools/ppsspp-pmp-fix.patch`):

```sh
PPSSPP_HEADLESS=/caminho/PPSSPPHeadless tools/emu_test.sh tela.png --source static
PPSSPP_HEADLESS=... python3 tools/emu_input_test.py   # controles; precisa de: pip install websocket-client
PPSSPP_HEADLESS=... python3 tools/emu_menu_test.py    # tela de configuração: procurar o PC, salvar, conectar
PPSSPP_HEADLESS=... python3 tools/emu_audio_test.py   # som: o PSP pede, desliga e liga pelo atalho
```

No emulador, a imagem e a lógica valem, mas os **tempos não**: o relógio
emulado pula o tempo ocioso (com frames P, corre ~30x o real), e a banda e as
perdas do 802.11b não são simuladas.

**Wolf**: `tests/test_wolf.py` roda contra `tests/fake_wolf.py`, que imita
a API num socket Unix (campos obrigatórios, `fmt::format` do pipeline, id
igual para as sessões sem cliente), o ping e os pipelines da sessão, com
`videotestsrc` e `audiotestsrc` no lugar do `interpipesrc`.
`packaging/docker-test.sh` faz o mesmo com a imagem Docker. Os bytes dos
pacotes de controle são conferidos contra as structs do Wolf. Detalhes em
[Wolf por dentro](Wolf-por-dentro).

## Teste do decoder H.264 no hardware (`psp/probe`)

EBOOT separado (`PSP/GAME/PSPStreamH264/`) que decodifica clipes embutidos
pelo Media Engine e grava `resultado_h264.txt` antes de cada passo (se o PSP
travar, a próxima rodada pula o passo e roda os outros). Foi assim que se
mediu o decoder (segura 2 frames, ~3,5-4 ms por chamada) e que se achou por
que a primeira versão dos frames P desligava o PSP: um IDR no meio de uma
sequência de P sem `sceMpegAvcDecodeStop` antes. Os clipes vêm de
`tools/h264_probe_clips.py`. Os resultados estão em [Medições](Medições).

## Estrutura

```
psp/                   cliente (C, pspdev)
  src/main.c           ciclo de vida, decode + exibição, overlay, atalhos, controles
  src/stream.c         thread de rede, slots, modelo pull, fila em ordem dos frames P
  src/decode.c         sceJpeg (hw), libjpeg-turbo (sw) e H.264 (sceMpegAvcDecode)
  src/menu.c           tela de configuração (IP, Wi-Fi, opções, procurar o PC)
  src/audio.c, ima.c   som: anel com buffer adaptativo, sceAudioSRC, decoder IMA ADPCM
  src/net.c            módulos de rede, Wi-Fi (apctl), TCP/UDP, broadcast
  src/display.c        framebuffer 8888, triple buffering, texto
  src/config.c         server.txt (ler e gravar)
  src/protocol.h       formato das mensagens (espelhado em server/protocol.py)
  probe/               teste do decoder H.264 no hardware
server/                servidor (Python 3)
  pspstream.py         sessões, linha de comando, benchmark
  capture.py           monta captura, som e controles (na partida e pela interface web)
  distro.py, doctor.py distribuição e pacotes; --check e --setup
  paths.py             arquivos no repositório ou instalados pelo pacote
  settings.py          configurações gerais: esquema, server.json, validação
  control.py           aplica as configurações com o servidor rodando
  web.py, web/         interface web (http.server da biblioteca padrão; HTML, CSS e JS sem dependências)
  gst_source.py        pipeline GStreamer (captura -> 480x272 -> JPEG/H.264/I420)
  kms.py, portal.py    captura KMS e pelo portal ScreenCast
  audio.py             som: captura (pulsesrc), IMA ADPCM (adpcmenc), pacotes
  h264.py, openh264.py encoders H.264 (libopenh264 direto por ctypes; GStreamer de reserva)
  transports.py        TCP e UDP (pedaços, NACK, reenvio)
  adaptive.py          qualidade adaptativa
  inject.py, gamepad.py, keymap.json   controles (uinput)
  wolf_api.py          Wolf: cliente da API (HTTP no socket Unix, só biblioteca padrão)
  wolf_source.py       Wolf: sessão, pipelines de vídeo e som, alvo, lobby, reconexão
  wolf_input.py        Wolf: controle de Xbox em pacotes do Moonlight (sessions/input)
  stats.py, sources.py, jpeginfo.py, protocol.py, netcheck.py
Dockerfile, docker/    imagem do servidor; compose (Wolf + PSPStream, NVIDIA, só o PSPStream), .env, install.sh
packaging/             .deb e .rpm (install-tree.sh, build-deb.sh, pspstream.spec, build-rpm.sh);
                       docker-test.sh (a imagem contra o Wolf falso), compose-check.sh, publish-wiki.sh
.github/workflows/     build.yml (EBOOT, testes, pacotes, imagem, releases); wiki.yml (publica a wiki)
tools/                 fake_client.py, emu_*.py/sh, h264_probe_clips.py, kms/ (auxiliar KMS)
wiki/                  as páginas desta wiki
tests/                 testes do servidor, da interface web e dos links da documentação; fake_wolf.py imita o Wolf
```
