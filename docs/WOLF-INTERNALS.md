# Como o PSPStream funciona no Wolf

Detalhes técnicos da fonte `--source wolf`. Como instalar e usar:
[WOLF.md](WOLF.md). As referências ao código do Wolf são do branch `stable`
(commit `facb8e0`), lido antes de escrever esta integração.

## Visão geral

```
                    servidor (rede do host)
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ Wolf (processo)                                  PSPStream (container)       │
 │                                                                              │
 │ lobby: compositor ─ interpipesink <lobby>_video   wolf_source.py             │
 │        PulseAudio ─ interpipesink <lobby>_audio     WolfSource (vídeo)       │
 │                         │                           WolfAudio  (som)         │
 │ sessão do PSPStream:    ▼                         wolf_input.py              │
 │   pipeline de vídeo: interpipesrc ! GPU 480x272     WolfInjector (botões)    │
 │     ! I420 ! gdppay ! tcpclientsink ──TCP 127.0.0.1──> tcpserversrc ─┐       │
 │   pipeline de som: interpipesrc ! S16LE                              │       │
 │     ! gdppay ! tcpclientsink ─────────TCP 127.0.0.1──> tcpserversrc ─┤       │
 │                                                                      ▼       │
 │ API (socket Unix /var/run/wolf/wolf.sock) <── HTTP ── wolf_api.py   servidor │
 │ controle virtual (inputtino) ──> jogo do lobby                      do PSP   │
 └──────────────────────────────────────────────────────────────────── │ ───────┘
                                                                      │ UDP/TCP 5123
                                                                     PSP (Wi-Fi)
```

O Wolf não tem uma forma de "exportar" a imagem de um lobby: os produtores
(`interpipesink <id>_video` e `<id>_audio`, em
`streaming/streaming.cpp`) só existem dentro do processo dele, e quem os lê
é um pipeline que o próprio Wolf roda para cada sessão Moonlight. O
PSPStream aproveita isso: cria pela API uma **sessão própria**, como a de
um cliente Moonlight sem o Moonlight, e manda nela **pipelines seus**, que
escutam o lobby e entregam a imagem e o som já prontos para o PSP por TCP
local. Daí em diante, o servidor do PSPStream trata o Wolf como qualquer
outra fonte (portal, KMS): o mesmo H.264 com frames P, o mesmo modelo pull,
o mesmo som.

## A sessão, passo a passo

Tudo isso roda numa thread da `WolfSource` (`server/wolf_source.py`):

1. **Achar o alvo.** `GET /api/v1/lobbies` e `GET /api/v1/sessions`. Sem
   `--wolf-target`, o único lobby aberto; com vários, erro na partida com a
   lista; sem nenhum, espera. Um alvo que é uma sessão Moonlight num lobby
   vira o lobby (o pipeline escuta o que aquela sessão vê).
2. **Limpar sobras.** Uma sessão com `rtsp_fake_ip = "pspstream"` (a marca
   das nossas) que sobrou de um PSPStream que morreu é encerrada: ela teria
   o mesmo id que a nova (item 4).
3. **Abrir a recepção.** `tcpserversrc host=127.0.0.1 port=0 ! gdpdepay`
   para o vídeo e outro para o som; o sistema escolhe as portas.
4. **Criar a sessão.** `POST /api/v1/sessions/add` com `client_ip
   127.0.0.1`, 480x272 a 30 Hz e chaves aleatórias. Sem `app_id`, o Wolf usa
   um app "dummy" (um `sleep` em loop) com um compositor e um sink de som
   próprios; sem `client_id`, um cliente fictício, cujo id é o hash de um
   certificado vazio: **o mesmo para toda sessão assim** (por isso uma por
   Wolf). Devolve o `session_id`.
5. **Mandar os pipelines.** `POST /api/v1/sessions/start` com
   `video_session` e `audio_session` (todos os campos obrigatórios das structs
   `VideoSession` e `AudioSession`), incluindo o `gst_pipeline` de cada um e
   um `rtp_secret_payload` de 16 bytes escolhido aqui.
6. **O ping.** O Wolf só roda os pipelines depois de um ping UDP na porta
   de ping de vídeo (48100) e na de som (48200), que case com a sessão
   (`sessions/moonlight.cpp`, `wait_for_ping`). O PSPStream manda um
   `SS_PING` (o segredo + um número de sequência) às duas portas a cada
   0,5 s até chegar o primeiro frame. O ping de som vai mesmo sem som:
   sem ele, uma thread do Wolf fica esperando para sempre.
7. **O primeiro frame.** Até 10 s. Sem frame (a conversão não serve para a
   GPU daquele Wolf), a sessão é encerrada e, no `auto`, a próxima conversão
   é tentada (nvidia → va → cpu).
8. **Os controles.** Com controles ligados e um lobby como alvo, `POST
   /api/v1/lobbies/join` (com o PIN, se houver).
9. **Vigiar.** A cada 2 s, os lobbies e as sessões de novo. A sessão é
   refeita quando: o alvo fecha (e o PSPStream espera outro abrir), a sessão
   some (o Wolf reiniciou), a sessão Moonlight alvo muda de lobby, a taxa ou
   os canais do som mudam, ou o vídeo para de chegar. Se a sessão saiu do
   lobby (o atalho do Wolf UI), ela entra de novo.
10. **Encerrar.** `POST /api/v1/sessions/stop`: o Wolf manda EOS aos
    pipelines (as conexões TCP fecham), tira a sessão do lobby e para o app
    dummy. Acontece ao parar o container (SIGTERM), ao trocar de alvo e
    quando o lobby fecha.

Entre uma sessão e a próxima há 1 s de pausa (a nova tem o mesmo id), e
depois de falhas seguidas a espera cresce até 30 s: cada sessão nova sobe um
compositor no Wolf.

## Os pipelines

Com `-v`, o log mostra o texto exato. Para um lobby `L`, a sessão `S`, NVIDIA
e as portas locais `P1` e `P2`:

```
interpipesrc name=pspstream_S_video listen-to=L_video is-live=true
    stream-sync=restart-ts max-bytes=0 max-buffers=1 leaky-type=downstream
  ! cudaupload ! cudaconvertscale add-borders=true
  ! video/x-raw(memory:CUDAMemory),format=I420,width=480,height=272,pixel-aspect-ratio=1/1
  ! cudadownload
  ! video/x-raw,format=I420,width=480,height=272,pixel-aspect-ratio=1/1
  ! gdppay ! tcpclientsink host=127.0.0.1 port=P1 sync=false

interpipesrc name=pspstream_S_audio listen-to=L_audio is-live=true
    stream-sync=restart-ts max-bytes=0 max-buffers=3 block=false
  ! queue max-size-buffers=3 leaky=downstream ! audioconvert ! audioresample
  ! audio/x-raw,format=S16LE,layout=interleaved,rate=44100,channels=2
  ! gdppay ! tcpclientsink host=127.0.0.1 port=P2 sync=false
```

- O Wolf passa o texto pelo `fmt::format` (com `{session_id}`, `{width}`
  etc.): chaves literais vão escapadas (`{{`), e os ids que entram no texto
  são conferidos (letras, números, `-` e `_`).
- O `interpipesrc` **não** se chama `interpipesrc_S_video`, o nome que o
  Wolf procura para trocar o produtor quando a sessão entra ou sai de um
  lobby (`SwitchStreamProducerEvents`). Assim a imagem segue sempre o alvo,
  mesmo quando a sessão entra no lobby por causa dos controles. Efeito
  colateral: o log do Wolf diz `Failed to get video interpipesrc for ...`
  nessa hora.
- Sem um appsink chamado `wolf_udp_sink`, o Wolf roda o pipeline sem mandar
  nada pelo RTP do Moonlight.

### A conversão

A imagem do lobby chega ao `interpipesrc` na memória em que o produtor a
deixa (`configTOML.cpp`, `producer_buffer_caps`):

| Wolf | memória | `--wolf-video-convert` |
|---|---|---|
| NVIDIA com zero-copy (o padrão) | `video/x-raw(memory:CUDAMemory)` | `nvidia`: `cudaupload ! cudaconvertscale ! ... ! cudadownload` |
| Intel/AMD com zero-copy | `video/x-raw(memory:DMABuf)` | `va`: `vapostproc` |
| `WOLF_USE_ZERO_COPY=FALSE` | `video/x-raw` (memória comum) | `cpu`: `videoconvertscale` |

A API não diz qual é o caso, por isso o `auto` tenta as três e guarda a que
funcionou. A redução para 480x272 acontece na GPU, dentro do Wolf: o que
vem por TCP já é pequeno (~12 MB/s a 60 fps, só no loopback).

### Por que TCP com GDP

- O `interpipe` só funciona dentro do processo do Wolf: a imagem precisa
  sair por outro caminho.
- `shmsink`/`unixfdsink` criariam um arquivo de socket no volume
  compartilhado, como root (o Wolf roda como root), que o container do
  PSPStream não conseguiria abrir sem mudar permissões; o `unixfdsrc` ainda
  pede GStreamer 1.24+.
- O `gdppay` leva junto os limites de cada frame e o formato (caps): do
  outro lado, `gdpdepay` devolve os buffers inteiros.
- O modelo pull continua: o `interpipesrc` guarda 1 buffer (leaky), o
  appsink daqui só o mais novo, e o frame é codificado quando o PSP pede.

## O som

O pipeline de som da sessão escuta `<lobby>_audio` (o monitor do sink do
PulseAudio do lobby, dentro do Wolf), converte para S16LE na taxa e nos
canais do PSP e manda por TCP. Aqui, a `AudioCapture` de sempre
(`server/audio.py`) recebe com `tcpserversrc ! gdpdepay` no lugar do
`pulsesrc` e codifica o IMA ADPCM em blocos de 20 ms.

Foi escolhido em vez de ler o PulseAudio do Wolf direto: o socket dele fica
num volume que o Wolf cria, e o som não acompanharia as reconexões. Cada
sessão do Wolf tem a sua captura; a `WolfAudio` é a interface que o servidor
vê, e continua a numeração dos pacotes entre uma sessão e a outra (o PSP
trata um número menor como pacote atrasado). Mudar a taxa ou o mono refaz a
sessão, porque o pipeline do Wolf é fixo.

## Os controles

`server/wolf_input.py`. O `WolfInjector` é o mesmo `GamepadInjector` do
controle de Xbox virtual do PC (perfis do `keymap.json`, a camada do SELECT,
a zona morta, o `--input-timeout`), com a saída trocada: em vez do
`/dev/uinput`, pacotes de controle do Moonlight mandados por `POST
/api/v1/sessions/input` (`{session_id, input_packet_hex}`), numa thread que
manda só o estado mais novo.

O Wolf converte o hex direto na struct `INPUT_PKT`, sem conferir o tamanho
(`api/endpoints.cpp`), então o pacote vai sempre inteiro:

```
06 02 | 22 00 | 00 00 00 1e | 0c 00 00 00 | 1a 00 00 00 01 00 14 00 00 10 00 00 ...
 tipo   resto    dados (BE)    CONTROLLER_   headerB, controle 0, máscara 1, midB,
 0x0206 (LE)                   MULTI (LE)    botões 0x1000 (A), gatilhos, eixos...
```

- **`CONTROLLER_ARRIVAL`** (`0x55000004`) primeiro, a cada sessão nova:
  controle 0, tipo Xbox, gatilhos analógicos, os botões que existem. No Wolf
  o campo das capacidades tem 1 byte e no Moonlight 2; vai o formato do
  Moonlight (o que os clientes de verdade mandam), e o Wolf lê o byte baixo.
- **`CONTROLLER_MULTI`** (`0x0C`) com o estado: o eixo Y é o do XInput (para
  cima é positivo), porque o inputtino (`joypad_xbox.cpp`, `set_stick`)
  grava `ABS_Y = -y`. O pacote acima é byte a byte o exemplo dos testes do
  Wolf (`tests/testWolfAPI.cpp`).
- Ao sair, um `CONTROLLER_MULTI` sem o bit do controle na máscara: o Wolf
  desliga o controle virtual.

O Wolf cria o controle virtual (inputtino) na sessão do PSPStream. Para ele
chegar ao jogo, a sessão precisa estar **no lobby**: o `LobbyJoin` migra os
controles da sessão para o container do jogo, e os criados depois vão
direto para lá (`sessions/lobbies.cpp`). Regras do `LobbyJoin`:

- um lobby de um jogador (`multi_user = false`) com alguém dentro recusa
  (`Lobby is full`). O Wolf UI cria assim os lobbies do botão **Start**, e
  como de vários jogadores os do **Coop**;
- um lobby com PIN pede o PIN (`--wolf-pin`);
- num lobby que fecha quando todos saem (`stop_when_everyone_leaves`), o
  PSP conta como jogador. O Wolf UI cria os lobbies sem isso.

Recusado, o PSPStream fica só na visualização e tenta de novo a cada 2 s: no
lobby Start, entra assim que o Moonlight sai. START + cima + RB no controle
é o atalho do Wolf UI e tira a sessão do lobby; o PSPStream entra de novo.

Em geral, os jogos numeram os jogadores na ordem em que os controles
aparecem no container do lobby: o do Moonlight, que entra primeiro, vem
antes do PSP. Para o PSP ser o jogador 1: um lobby Start e o Moonlight fora
dele, ou o Moonlight sem controle.

## Limites (do Wolf, que só com a API não dá para mudar)

- **Uma sessão de PSPStream por Wolf.** Todas as sessões criadas sem
  `client_id` têm o mesmo id (`state/config.hpp`, `get_client_id`: o hash do
  certificado vazio). Dentro de um processo, uma trava faz a captura nova
  esperar a velha encerrar (a interface web sobe a nova antes de parar a
  velha).
- **O app dummy** de cada sessão sobe um compositor e um sink de som (que
  ninguém vê) e cria uma pasta vazia `<uuid>/dummy` no diretório de estado
  do Wolf (`state/sessions.hpp`, `create_stream_session`).
- **Sem vibração**: o Wolf manda a vibração pelo canal de controle do
  Moonlight, que a sessão do PSPStream não tem (ele só registra um aviso).
- **Sem teclado e mouse**: só o controle de Xbox (um perfil de teclado vira
  `xbox`).

## Segurança

- **O socket da API dá controle total do Wolf** (parear clientes, iniciar
  apps). O PSPStream só usa: listar lobbies e sessões, criar, iniciar e
  encerrar a própria sessão, mandar pacotes de controle, entrar e sair do
  lobby. O socket só é montado no container do PSPStream (`:ro`, o
  diretório), nunca exposto por TCP.
- **uid 0 sem poderes.** O Wolf cria o socket como root, sem mudar a
  permissão (`srwxr-xr-x`), e o recria a cada início: só o dono conecta, e
  por isso o próprio Wolf UI roda como root. A imagem roda como usuário comum
  (10001), e os compose a rodam com o uid 0, mas com `cap_drop: ALL`,
  `no-new-privileges` e o sistema de arquivos só leitura: o processo é o
  dono do socket e de mais nada. Uma ACL no socket (`setfacl`) também
  funciona com o usuário comum, mas se perde a cada início do Wolf; uma ACL
  padrão no diretório não serve, porque o Wolf cria o socket sem escrita para
  os outros (o umask vale para sockets).
- **O pipeline que o Wolf executa** (root, com a GPU) só leva elementos
  fixos e ids conferidos. A conversão própria (`--wolf-video-convert
  "..."`) só vale pela linha de comando ou pela variável; a interface web só
  escolhe entre `auto`, `nvidia`, `va` e `cpu`.
- **A interface web** escuta em 127.0.0.1 por padrão. Na rede, com
  `PSPSTREAM_WEB_PASSWORD` (autenticação básica do HTTP, 1 s de espera a
  cada senha errada). Ela recusa endereços que não conhece (contra DNS
  rebinding: só localhost, o nome do servidor, IPs e
  `PSPSTREAM_WEB_HOSTS`), pedidos POST sem `application/json` e com Origin
  diferente do Host.

## Onde está cada coisa

| arquivo | o quê |
|---|---|
| `server/wolf_api.py` | cliente da API: HTTP/1.0 no socket Unix, só biblioteca padrão, erros claros |
| `server/wolf_source.py` | `WolfSource` (sessão, pipelines, ping, alvo, lobby, reconexão), `WolfAudio`, textos para a API |
| `server/wolf_input.py` | pacotes de controle e o `WolfInjector` |
| `server/capture.py` | monta a fonte, o som e os controles do Wolf a partir das opções |
| `tests/fake_wolf.py` | o Wolf falso: a API, o ping e os pipelines (com `videotestsrc` no lugar do `interpipesrc`) |
| `tests/test_wolf.py` | testes contra o Wolf falso e os bytes dos pacotes |
| `Dockerfile`, `docker/` | a imagem, os compose, o `.env.example` e o `install.sh` |
| `packaging/docker-test.sh`, `compose-check.sh` | a imagem contra o Wolf falso; os compose e o instalador (no CI) |

## O que foi conferido no código do Wolf

| fato | onde |
|---|---|
| os produtores `interpipesink <id>_video` e `<id>_audio` | `streaming/streaming.cpp`, `start_video_producer`, `start_audio_producer` |
| as rotas da API e o HTTP/1.0 com Content-Length, uma requisição por conexão | `api/endpoints.cpp`, `api/unix_socket_server.cpp` |
| o socket em `WOLF_SOCKET_PATH`, ou `$XDG_RUNTIME_DIR/wolf.sock` | `api/api.cpp` |
| o pipeline pelo `fmt::format` e os placeholders aceitos | `streaming.cpp`, `start_streaming_video/audio` |
| o ping obrigatório, o casamento pelo segredo, as portas 48100/48200 | `sessions/moonlight.cpp`, `rtp/udp-ping.cpp`, `state/data-structures.hpp` |
| a sessão sem app e sem cliente: o app dummy, o id igual | `api/endpoints.cpp` (`StreamSessionAdd`), `state/config.hpp` |
| `LobbyJoin`: a migração dos controles, lobby cheio, PIN | `sessions/lobbies.cpp`, `api/endpoints.cpp` (`check_lobby_pin`) |
| o formato dos pacotes de controle | `moonlight/control.hpp`, `control/input_handler.cpp`, moonlight-common-c `Input.h` |
| o eixo Y invertido | inputtino `fd136cf` (a versão do Wolf), `joypad_xbox.cpp` |
| os lobbies do Wolf UI: Start e Coop | wolf-ui `App.cs` (`OnStartPressed`, `OnCoopPressed`) |
