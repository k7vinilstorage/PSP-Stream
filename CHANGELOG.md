# Histórico de versões

As versões do EBOOT e do servidor andam juntas. O protocolo tem a própria
versão (`PSC5` = v5) e só muda quando o formato das mensagens muda; um EBOOT
de outra versão do protocolo é recusado com aviso no log.

## 1.1 (em desenvolvimento)

- **Som do PC no PSP.** O servidor captura o que sai nas caixas (monitor da
  saída padrão do PipeWire/PulseAudio, `pulsesrc`), codifica em IMA ADPCM
  (`adpcmenc`, 4 bits por amostra) e empurra um pacote UDP a cada 20 ms: 44,1
  kHz estéreo (a taxa do PSP), ~46 KB/s, ~2% de um núcleo no PC. O PSP decodifica no CPU
  (somas e deslocamentos, sem o Media Engine do H.264) e toca pelo
  `sceAudioSRC`, com um buffer que se ajusta sozinho entre 30 e 120 ms.
  Pacote perdido vira 20 ms de silêncio; o vídeo e o som não dependem um do
  outro.
- **Liga e desliga pelo PSP:** `audio=1/0` no `server.txt`, item "Som do PC"
  na tela de configuração e SELECT + START + cima durante o stream.
  Desligado, o PSP para de pedir som (`wflags & 0x10`) e o PC para de mandar.
- **Zumbido "de abelha" no som (primeiro teste no PSP-3000):** a thread de
  som reaproveitava o único buffer de saída enquanto o hardware ainda o
  tocava (a saída bloqueante volta quando o pedaço entra na fila, e o DMA lê
  depois), estragando o fim de cada pedaço de 8 ms: ~125 Hz. Agora são dois
  buffers alternados, e cada pedaço sai do cache antes. No PPSSPP não
  aparecia (ele copia na hora da chamada).
- Som a **44,1 kHz** por padrão (era 32 kHz): é a taxa do hardware do PSP,
  então ele não reamostra; numa música de jogo, 0,3-1,8 dB a mais de
  fidelidade e agudos até 22 kHz, por ~12 KB/s a mais.
- O overlay mostra o som (buffer, alvo, perdidos, vazio, pulos), e a linha
  do servidor, os KB/s de som.
- **Frames P pedidos quando o decode começa (`prefetch=auto`).** O relato
  "ligar e desligar o prefetch leva de ~45 a ~60 fps" (Hollow Knight) achou
  dois erros:
  - `prefetch=0` esperava o decode por um semáforo binário que podia guardar
    um sinal velho (ligar/desligar o prefetch, ou dois frames publicados de
    uma vez). Com o sinal, o próximo saía quando o decode começava (~60
    fps); sem ele, depois de exibir (~45 fps). Agora quem decide é o estado
    dos frames, e `0` é sempre "depois de exibir".
  - Com prefetch e frames P, a thread de decode soltava o "pedido adiado"
    antes de contar o pedido que fez; a thread de rede (que olha a cada 1
    ms) podia pedir o mesmo frame de novo, e o pedido fantasma travava o
    seguinte até o RTO. Era o engasgo do prefetch com frames P.
  O padrão `auto` agora é o modo bom, de propósito: com frames P, o próximo
  é pedido quando o decode pega o atual, sem pedido antecipado no meio do
  frame. `1` acrescenta o pedido antecipado (na simulação, ~59 fps contra
  ~55 do `auto` antes da janela, abaixo). SELECT + START + X alterna auto,
  sim e não.
- **Frames P sem pular capturas: janela de 2 frames.** Relato: 52-55 fps
  em vez de 60, e ~35 com `--fps 40`, com o Wi-Fi bem abaixo do limite. O
  pedido do N+1 saía quando o decode pegava o N e tinha de chegar ao PC
  antes da captura seguinte (16,7 ms a 60 fps); com o Wi-Fi oscilando, o
  servidor perdia capturas. Agora, com `prefetch=auto` no UDP, o pedido
  autoriza até o N+2 (FRAME + NACK "até o frame F"; o servidor guarda o
  crédito, no máximo 2 à frente) e o frame sai na hora da captura. Na
  simulação com a ida e volta oscilando: 53-55 → 59,4-60 fps, mesma latência.
  Nesse modo o pedido não é mais repetido depois de 6 ms (o seguinte cobre
  um perdido): um pacote a menos por frame na subida.
- Frame P perdido inteiro com a janela: o seguinte chega antes do pedido
  repetido; o PSP nota o buraco na numeração e pede o reenvio na hora, em
  vez de um IDR.
- **`--fps` exato.** O limite contava a vez do frame seguinte a partir do
  frame que chegou, com 25% de tolerância: com os horários da captura
  tremendo 2-3 ms, uma tela de 60 Hz dava 55-59 fps com `--fps 60` (o
  padrão) e 38-39 com `--fps 40` (75 Hz com `--fps 60`: ~56; 60 Hz com
  `--fps 50`: 46-48). Agora é uma grade fixa: a taxa pedida, e um frame
  atrasado não empurra os seguintes.
- `--fps 40` de uma tela de 60 Hz é 2 de cada 3 frames: intervalos de 17 e
  33 ms. Para um movimento uniforme, `--fps 30` ou 60 (wiki, Opções do servidor).
- **Som que não voltava depois de mexer na configuração** ("erro" até
  reiniciar o app): o PSP só solta o canal de som com a fila vazia, o erro
  era ignorado e o canal ficava preso; o stream seguinte não conseguia
  reservá-lo. Agora espera a fila esvaziar antes de soltar, e tenta de novo
  ao reservar. Reproduzido e corrigido no PPSSPP: o
  `tools/emu_audio_test.py` confere o log do PSP, liga e desliga o som e
  volta da tela de configuração.
- `fake_client`: `--rtt-jitter-ms` (ida e volta oscilando, exponencial),
  `--no-window` (sem a janela) e "P perdidos inteiros".
- **Interface web das configurações gerais** (http://localhost:5124): captura,
  codec, qualidade, som, controles e rede mudam com o PSP conectado. A
  captura nova sobe antes de a velha parar e entra na mesma sessão (a
  numeração dos frames continua, e o primeiro frame P é um IDR); se não
  subir, a velha continua. Estado do stream e log na página. As mudanças
  ficam em `~/.config/pspstream/server.json`; a linha de comando vale mais
  que o arquivo. Só no próprio PC por padrão (`--web`, `--no-web`), com
  proteção contra pedidos de outros sites. Só a biblioteca padrão do Python.
- Servidor reorganizado para isso: `capture.py` (montagem da captura, som e
  controles), `settings.py`, `control.py`, `web.py`. As ferramentas do
  emulador rodam o servidor com `--config` próprio e `--no-web`.
- **Qualquer Linux, com guia do Ubuntu** ([Guia do Ubuntu](https://github.com/k7vinilstorage/PSP-Stream/wiki/Guia-do-Ubuntu)).
  `--check` confere tudo o que o servidor usa (Python, PyGObject, cada
  elemento do GStreamer, libopenh264, portal, uinput, som, auxiliar KMS,
  firewall, porta) e termina com o comando para a distribuição detectada
  (apt, dnf, pacman ou zypper). `--setup` roda esses passos, mostrando cada
  comando e perguntando antes. As mensagens de erro ("falta o X") também dão
  o comando da distribuição, em vez do `dnf` fixo.
- Compatibilidade com versões mais antigas: o `n-threads` do `videoscale` e
  o `always-copy` do `pipewiresrc` só entram se existirem (GStreamer 1.20 do
  Ubuntu 22.04); testes também no Python 3.10 e 3.13.
- A `libopenh264` também é procurada em `~/.local/lib`, em `lib/` do projeto
  e em `PSPSTREAM_OPENH264`: a do Cisco serve onde a distribuição não tem o
  pacote.
- No PSP, "Sem resposta do PC" sugere o `--check` em vez do comando do
  firewalld.
- **EBOOT e pacotes prontos no GitHub** (`.github/workflows/build.yml`): a
  cada push, o CI compila o EBOOT, roda os testes e gera o `.deb` (Ubuntu
  22.04+, Debian 12+) e o `.rpm` (Fedora), instalando cada um para testar.
  Um push na `main` refaz a pré-release `nightly`; uma tag `v*` publica a
  release estável. O pacote traz o comando `pspstream`, a permissão do
  `/dev/uinput`, o atalho no menu, o serviço de usuário do systemd e a regra
  de firewall (ufw/firewalld); a permissão da captura KMS fica para o
  `pspstream --setup`.
- Licença MIT (`LICENSE`).
- Servidor: `--no-audio`, `--audio-device` (`monitor`, `test` ou uma fonte do
  PipeWire), `--audio-rate`, `--audio-mono`.
- Só pelo UDP (o padrão).
- `fake_client --audio`, `tools/emu_audio_test.py`, e o decoder do PSP
  (`psp/src/ima.c`) testado no PC contra a referência.
- O README dizia `--p-redundancy-ms 4`; o padrão é 6.
- **Wolf (Games on Whales), vídeo** (`--source wolf`, experimental): o
  PSPStream cria uma sessão no Wolf pela API (socket Unix, `--wolf-socket`)
  e o Wolf roda um pipeline do PSPStream, que escuta o lobby
  (`interpipesrc`), reduz para 480x272 I420 e manda por TCP em 127.0.0.1;
  daí em diante é igual às outras fontes (JPEG, h264, h264p). O alvo é o
  único lobby aberto, ou `--wolf-target` (id ou nome do lobby, ou id da
  sessão); a conversão na GPU é `--wolf-video-convert` (`nvidia`, `va`,
  `cpu` ou `auto`, que tenta nessa ordem). Se o lobby fecha, a sessão é
  encerrada e a fonte espera ele voltar, sem derrubar o servidor. Ping na
  porta 48100 do Wolf (`--wolf-rtp-port`, `WOLF_VIDEO_PING_PORT`).
  Testado só contra um Wolf falso (`tests/fake_wolf.py`), não num Wolf de
  verdade.
- **Wolf, som:** o som do alvo vem pelo mesmo caminho do vídeo (o pipeline
  de som da sessão escuta `<lobby>_audio`, converte para S16LE na taxa do
  PSP e manda por TCP) e vira o IMA ADPCM de sempre. Com `--source wolf`, o
  padrão `--audio-device monitor` já usa o som do Wolf (ou
  `--audio-device wolf`); a numeração dos pacotes continua quando a sessão
  do Wolf é refeita, e mudar a taxa ou o mono refaz a sessão. Escolhido em
  vez de montar o PulseAudio do Wolf no container: nada a mais para
  compartilhar, e reconecta junto com o vídeo.
- **Wolf, controles:** com `--source wolf`, os botões do PSP vão para o
  jogo como um controle de Xbox virtual criado pelo Wolf: os mesmos perfis
  do `keymap.json` (`xbox`, `xbox-camera`, `xbox-ombros`; um perfil de
  teclado vira `xbox`), em pacotes `CONTROLLER_ARRIVAL` e
  `CONTROLLER_MULTI` do Moonlight mandados por `sessions/input`. A sessão
  do PSPStream entra no lobby sozinha (e de novo, se o atalho START + cima +
  RB do Wolf UI a tirar); lobby cheio ou com PIN (`--wolf-pin`) fica só na
  visualização, tentando de novo. Um alvo que é uma sessão Moonlight avulsa
  é só visualização. O `--input-timeout` solta tudo, e ao sair o controle é
  desligado no Wolf. Os bytes foram conferidos contra as structs do Wolf e
  o exemplo dos testes dele.
- **Wolf, Docker:** `Dockerfile` (Ubuntu 24.04, só o servidor, o GStreamer e
  a `libopenh264-7` do universe; roda como usuário comum) e
  `docker/compose.yml` com o serviço `pspstream` ao lado do `wolf` e as duas
  linhas que mudam no serviço do Wolf (`WOLF_SOCKET_PATH` e o volume
  `/var/run/wolf`). O socket da API do Wolf é só do root, então o compose
  usa o uid 0 sem nenhuma capability, sem ganhar privilégios e com o sistema
  de arquivos só leitura (o usuário comum com `setfacl` também foi testado).
  O CI compila a imagem e a testa contra o Wolf falso
  (`packaging/docker-test.sh`). Página "Wolf" na wiki; a interface web
  mostra uma linha "Wolf" com o que está sendo espelhado.
- **Wolf, instalação do zero:** `docker/install.sh` instala o Wolf e o
  PSPStream num servidor com Docker (detecta a GPU, prepara o sistema como a
  documentação do Wolf pede: módulos `uinput`/`uhid` e as regras udev do
  Wolf; libera as portas no ufw/firewalld; escreve um `.env`; sobe tudo),
  mostrando cada comando e perguntando antes. Compose completos em
  `docker/compose.yml` (Intel/AMD) e `compose.nvidia.yml`, `pspstream.yml`
  para quem já tem o Wolf, `build.yml` para compilar, e `.env.example` com
  as configurações; o CI confere que o serviço do PSPStream é o mesmo nos
  três e que o instalador gera um `.env` válido.
- **Imagem pronta no ghcr.io:** o CI publica
  `ghcr.io/k7vinilstorage/pspstream` (`nightly` a cada push na `main`, a
  versão e `latest` a cada tag), depois de testá-la contra o Wolf falso.
- **Interface web na rede, com senha:** `PSPSTREAM_WEB_PASSWORD`
  (autenticação básica do HTTP, na página e na API) e `--web-allow-host` /
  `PSPSTREAM_WEB_HOSTS` para outros nomes do servidor (como um do DNS do
  roteador). O padrão continua só no próprio PC.
- Padrões por variável de ambiente para o Docker (a interface web ainda
  muda): `PSPSTREAM_WEB`, `PSPSTREAM_WOLF_TARGET`, `PSPSTREAM_VIDEO_CONVERT`,
  `PSPSTREAM_WOLF_PIN`, `PSPSTREAM_PROFILE`.
- Documentação do Wolf: as páginas "Wolf" (instalação, primeiro uso,
  lobbies Start e Coop do Wolf UI e a ordem dos controles, interface web,
  configurações, problemas) e "Wolf por dentro" (como funciona por dentro e
  o que foi conferido no código do Wolf) da wiki.
- **Documentação na wiki.** O README ficou curto: recursos, como funciona,
  início rápido, atalhos e créditos. O resto (instalação, uso no PSP,
  controles, interface web, Wolf, opções, problemas, desempenho, protocolo,
  medições, desenvolvimento) foi para a
  [wiki do projeto](https://github.com/k7vinilstorage/PSP-Stream/wiki), gerada da pasta `wiki/`
  pelo workflow `wiki.yml` a cada push na `main`; `docs/` saiu.
  `tests/test_docs.py` confere os links do README e da wiki. Os pacotes
  `.deb` e `.rpm` levam o README, o CHANGELOG e a licença.
- O servidor encerra direito no SIGTERM (`docker stop`, `systemctl stop`),
  como no Ctrl+C.
- **Captura KMS, "sem permissão para ler a tela":** a mensagem (no log e na
  interface web) diz qual auxiliar e o comando certo (o `setcap` do pacote
  ou o `make -C tools/kms cap` do repositório), e avisa quando a permissão
  existe mas o kernel a ignora (partição `nosuid`, ou o servidor rodando com
  `no_new_privs`, como num terminal de Flatpak). Os pacotes `.deb` e `.rpm`
  guardam a permissão nas atualizações: antes, cada atualização trocava o
  auxiliar e ela sumia. O `--check` lê a permissão sem precisar do `getcap`.

## 1.0

Primeira versão.

- **openh264 chamado direto** (`server/openh264.py`, ctypes), sem o
  GStreamer no caminho do encode: do pedido do PSP ao 1º pacote do frame P,
  2,8 -> 1,8 ms (mediana; p95 4,2 -> 2,7 ms). A qualidade adaptativa muda o
  QP sem IDR (antes, cada troca refazia o encoder e esperava até 3 s). O
  fluxo é o mesmo do openh264enc, byte a byte; se a biblioteca faltar ou o
  layout dela não bater, o servidor volta para o GStreamer sozinho.
  `--h264-encoder auto|openh264|gstreamer`.
- `--codec auto` passa a escolher **H.264 com frames P**, validado no
  PSP-3000 em gameplay. Um EBOOT anterior à v0.9 recebe todo frame IDR.
- **Menos engasgos com frames P.** Cada P depende do anterior, então um
  pacote perdido parava o stream até o reenvio: 30-60 ms por perda (o
  decode, 10,6 ms, cabe nos 16,7 ms de um frame a 60 fps e não era o
  limite). Agora:
  - o servidor manda o **último pedaço de cada frame P de novo** 6 ms depois
    (`--p-redundancy-ms`): perder o último pedaço era o caso lento, só
    notado pelo silêncio, e um P pequeno é um pedaço só;
  - o PSP **repete o pedido de frame novo** depois de 6 ms, com o número do
    frame (o servidor reconhece a cópia e não manda frame a mais): um pedido
    perdido na subida esperava o RTO, >= 30 ms;
  - o PSP não repete mais um pedido que ainda nem fez (o próximo frame
    adiado para a thread de decode): era um pedido a mais, e inflava o
    `repet` do overlay;
  - **sem IDR periódico** (era a cada 30 s): a volta dos contadores do
    openh264 passou na sonda v4.1.
  Na simulação com 1-2% de perda, os engasgos (>= 50 ms entre frames) caíram
  de 4-12 para 0-3 a cada 16 s; a cópia do último pedaço custa ~40 KB/s.
- **`prefetch=auto` (padrão):** sem prefetch com frames P, com prefetch no
  resto. Com frames P e o prefetch desligado, o próximo frame só é pedido
  depois de exibir o atual: um por vez, num ritmo regular, e foi o que deixou
  o Hollow Knight liso e perto de 60 fps no PSP-3000. No JPEG e no H.264 só com quadros
  completos, o prefetch continua (1,2-1,7x de FPS, medido). `prefetch=1` ou
  `0` força; SELECT + START + X inverte o que está valendo; a tela de
  configuração tem auto/sim/não; o overlay diz quando o próximo é pedido.
- A linha de estatística do servidor mostra os **engasgos com a causa
  provável** (perda, IDR, pedido atrasado, captura) e os IDR.
- `fake_client`: rajadas de perda (`--loss-burst-ms`), contagem de
  engasgos, `--prefetch auto|on|off`, `FAKE_TRACE=1`; corrigida a contagem do
  pedido feito pelo "decode", que criava engasgos falsos na simulação.
- `--version`; documentação reorganizada para quem instala pela primeira
  vez; este histórico.

## 0.9

- **H.264 com frames P** (`--codec h264p`): o servidor codifica o frame na
  hora do pedido, e cada pacote leva o frame e 2 cópias (o decoder do PSP só
  solta o frame N depois do N+2). No PSP, fila em ordem, frames completos
  esperando o reenvio de um mais velho, IDR pedido (`PS_REQ_IDR`) quando a
  corrente quebra, e o próximo frame pedido só quando o decode pega o atual.
- Frame pequeno perdido inteiro volta pelo pedido repetido com NACK do frame
  esperado, sem precisar de IDR.
- `sceMpegAvcDecodeStop` antes de todo IDR que chega com frames P no
  decoder: sem ele, o PSP **desligava** (achado pela sonda `psp/probe`
  v4.1). IDR a cada 30 s.
- **Controle de Xbox 360 virtual** (`--profile xbox`, `xbox-camera`,
  `xbox-ombros`), com camada no SELECT.
- **Tela de configuração no PSP** (IP, porta, perfil de Wi-Fi, opções;
  grava o `server.txt`) e **Procurar o PC na rede** (ping em broadcast).
  Abre com SELECT + START + R durante o stream.
- `scePowerTick`: o modo de espera automático não suspende mais o PSP no
  meio do stream.
- O atalho SELECT + START + L não se repete depois de reconectar.

## 0.8

- Pedido antecipado automático (ida e volta x vazão, ~2-3 KB no PSP-3000),
  protocolo v5 (`early_b`, `idle_t`).
- Captura: sem o `videorate`, que cortava o portal de 60 para ~38 fps.
- **Captura KMS** (`--source kms`, auxiliar `tools/kms` com
  `CAP_SYS_ADMIN`): 58-60 fps no GNOME 50, cujo portal fica em ~40 fps.
- `--dmabuf` (experimental): redução da tela no OpenGL.
- O servidor diz se o PC divide o canal de 2,4 GHz com o PSP.

## 0.7

- Pedido sem resposta repetido depois de uma ida e volta medida (eram 200 ms
  fixos): o FPS do H.264 dobrou no PSP-3000.

## 0.6

- Ping durante o stream (protocolo v4); mínimo e mediana do 1º pedaço no
  benchmark.

## 0.5

- **H.264 pelo decoder de hardware do PSP** (`--codec h264`, todo frame IDR
  + Stop): ~3,7 ms de decode e ~40% dos bytes do JPEG na mesma qualidade.
  Padrão do `--codec auto`.
- `psp/probe`: teste do decoder H.264 no hardware (v1-v3).

## 0.1 - 0.4

- Stream MJPEG no modelo pull (RNDS-Stream), decode sceJpeg/libjpeg-turbo
  direto na VRAM, overlay, qualidade adaptativa à banda.
- Controles do PSP como teclado e mouse (uinput), com proteção contra tecla
  presa.
- Transporte UDP (padrão) com NACK; protocolo v3: cabeçalho JPEG enviado uma
  vez, ping no início, DSCP.
- Benchmark de qualidades com o PSP conectado, cliente falso e testes no
  PPSSPP.
