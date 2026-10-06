# PSPStream no Wolf (Games on Whales)

O PSPStream mostra no PSP o que roda num lobby do
[Wolf](https://github.com/games-on-whales/wolf): a imagem, o som e os
controles, ao lado do Moonlight e sem mudar nada no Wolf. Ele roda num
container ao lado do Wolf e fala com a API dele. Como isso funciona por
dentro está em [WOLF-INTERNALS.md](WOLF-INTERNALS.md).

**Estado:** funciona num Wolf `stable` com NVIDIA, gerenciado pelo
Portainer. A instalação do zero (`install.sh`, os compose completos) e as
GPUs Intel/AMD foram testadas contra um Wolf falso, que imita a API e os
pipelines do Wolf, mas não num Wolf de verdade.

Conteúdo:

1. [O que é preciso](#1-o-que-é-preciso)
2. [Instalação do zero com o instalador](#2-instalação-do-zero-com-o-instalador)
3. [Instalação do zero à mão (compose)](#3-instalação-do-zero-à-mão-compose)
4. [Pelo Portainer](#4-pelo-portainer)
5. [Já tenho o Wolf](#5-já-tenho-o-wolf)
6. [Primeiro uso: Moonlight, lobby e PSP](#6-primeiro-uso-moonlight-lobby-e-psp)
7. [Interface web: no servidor ou na rede](#7-interface-web-no-servidor-ou-na-rede)
8. [Configurações (`.env`)](#8-configurações-env)
9. [Atualizar e desfazer](#9-atualizar-e-desfazer)
10. [Problemas](#10-problemas)

---

## 1. O que é preciso

- Um servidor Linux com **Docker** e o plugin **compose**
  (`docker compose version`).
- Uma GPU para o Wolf codificar o vídeo do Moonlight:
  - **NVIDIA**: driver 530.30.02 ou mais novo, o
    [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
    1.16 ou mais novo (`sudo nvidia-ctk runtime configure --runtime=docker
    && sudo systemctl restart docker`) e `nvidia-drm.modeset=1` (`cat
    /sys/module/nvidia_drm/parameters/modeset` deve dizer `Y`);
  - **Intel ou AMD**: nada além do driver do kernel (`/dev/dri`).
- **O PSP e o servidor na mesma rede local.** O PSP só fala 802.11b
  (2,4 GHz). No PSP, use o IP da LAN do servidor (`hostname -I`, algo como
  `192.168.0.10`), e não o do Tailscale (`100.x.y.z`) nem os do Docker
  (`172.x.y.z`).
- O PSPStream no PSP (o mesmo EBOOT de sempre, das
  [Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)).

Portas no firewall do servidor (o instalador libera, se o ufw ou o
firewalld estiverem ativos):

| quem | portas |
|---|---|
| PSPStream (o PSP) | 5123 UDP e TCP |
| interface web na rede (opcional) | 5124 TCP |
| Wolf (o Moonlight) | 47984, 47989, 48010 TCP; 47999, 48100, 48200 UDP |

---

## 2. Instalação do zero com o instalador

Num servidor sem o Wolf. O instalador mostra cada comando e pergunta antes
de mexer no sistema:

```sh
curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
sudo bash install.sh
```

(De um clone do repositório: `sudo docker/install.sh`. Para ver tudo sem
mudar nada: `--dry-run`.)

O que ele faz, em ordem:

1. **Confere o Docker** e se já existe um Wolf no servidor. Com um Wolf de
   outra instalação, ele para e manda para a [seção 5](#5-já-tenho-o-wolf)
   (dois Wolf na rede do host brigariam pelas portas).
2. **Detecta a GPU**: NVIDIA (`nvidia-smi`), Intel/AMD (`/dev/dri`) ou
   nenhuma. Com NVIDIA, confere o Container Toolkit, o runtime no Docker e o
   `modeset`.
3. **Prepara o sistema como a documentação do Wolf pede**: carrega os
   módulos `uinput` e `uhid` (e em cada boot, em
   `/etc/modules-load.d/wolf.conf`), e instala as regras udev do Wolf em
   `/etc/udev/rules.d/85-wolf.rules`, baixadas do repositório do Wolf (elas
   dão ao Wolf o acesso aos controles virtuais e os tiram do desktop do
   servidor).
4. **Libera as portas** no ufw ou no firewalld, se estiverem ativos.
5. **Escreve a configuração** em `/opt/wolf-pspstream`: os compose e um
   `.env` (permissão 600) com a GPU detectada e, se você quiser, a interface
   web aberta para a rede com uma senha gerada na hora.
6. **Baixa as imagens e sobe** o Wolf e o PSPStream. Se a imagem pronta do
   PSPStream não estiver disponível, compila a partir do GitHub.
7. **Mostra os próximos passos**: o pareamento do Moonlight, o IP para o
   PSP e a senha da interface web.

Opções úteis:

| opção | para quê |
|---|---|
| `--gpu nvidia\|intel\|amd\|cpu` | pula a detecção |
| `--dir PASTA` | outra pasta em vez de `/opt/wolf-pspstream` |
| `--ref BRANCH` | os arquivos de outro branch ou tag do repositório |
| `--yes` | sem perguntas (sim para tudo) |
| `--no-host` | não mexe no sistema (módulos, udev, firewall) |
| `--no-start` | só prepara os arquivos |
| `--only-pspstream` | o Wolf já roda em outro lugar: só o PSPStream ([seção 5](#5-já-tenho-o-wolf)) |

Rodar de novo atualiza os compose e as imagens e mantém o `.env`.

Depois: [seção 6](#6-primeiro-uso-moonlight-lobby-e-psp).

---

## 3. Instalação do zero à mão (compose)

O mesmo que o instalador faz, passo a passo.

1. **Sistema** (como pede a documentação do Wolf):

   ```sh
   sudo modprobe uinput uhid
   printf 'uinput\nuhid\n' | sudo tee /etc/modules-load.d/wolf.conf
   sudo curl -fsSL https://raw.githubusercontent.com/games-on-whales/wolf/stable/85-wolf.rules \
     -o /etc/udev/rules.d/85-wolf.rules
   sudo udevadm control --reload-rules && sudo udevadm trigger
   ```

2. **Arquivos**: a pasta [`docker/`](../docker) do repositório tem:

   | arquivo | o quê |
   |---|---|
   | `compose.yml` | Wolf (Intel/AMD) + PSPStream |
   | `compose.nvidia.yml` | Wolf (NVIDIA) + PSPStream |
   | `pspstream.yml` | só o PSPStream (o Wolf já roda) |
   | `build.yml` | compila o PSPStream em vez de baixar a imagem |
   | `.env.example` | as configurações ([seção 8](#8-configurações-env)) |

   ```sh
   sudo mkdir -p /opt/wolf-pspstream && cd /opt/wolf-pspstream
   for f in compose.yml compose.nvidia.yml pspstream.yml build.yml .env.example; do
     sudo curl -fsSLO "https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/$f"
   done
   sudo cp .env.example .env && sudo chmod 600 .env
   ```

3. **Configuração**: no `.env`, `COMPOSE_FILE=compose.nvidia.yml` (NVIDIA) ou
   `compose.yml` (Intel/AMD), e o resto da [seção 8](#8-configurações-env).

4. **Subir**:

   ```sh
   sudo docker compose up -d
   sudo docker compose logs -f pspstream
   ```

---

## 4. Pelo Portainer

**Com o repositório Git (recomendado; atualiza com um clique):** *Stacks* →
*Add stack* → **Repository**:

| campo | valor |
|---|---|
| Repository URL | `https://github.com/k7vinilstorage/PSP-Stream` |
| Repository reference | `refs/heads/main` (antes do merge: `refs/heads/claude/psp-pc-screen-stream-lou5q7`) |
| Compose path | `docker/compose.nvidia.yml` (NVIDIA), `docker/compose.yml` (Intel/AMD) ou `docker/pspstream.yml` (o Wolf já roda) |
| Environment variables | as da [seção 8](#8-configurações-env) que você quiser mudar (ex.: `PSPSTREAM_WEB=0.0.0.0:5124` e `PSPSTREAM_WEB_PASSWORD=...`) |

Para atualizar: *Pull and redeploy* na stack (marcando "re-pull image").

**Com o editor web:** cole o conteúdo de um dos compose em *Web editor* e as
variáveis em *Environment variables*.

O sistema (módulos e regras udev do passo 1 da [seção 3](#3-instalação-do-zero-à-mão-compose))
continua sendo feito no servidor, uma vez.

---

## 5. Já tenho o Wolf

Foi o caso testado: o Wolf já roda (por exemplo na stack `steam` do
Portainer, container `steam-wolf-1`).

1. **No serviço do Wolf, duas linhas**: o socket da API passa a aparecer no
   host, em `/var/run/wolf`. A configuração padrão do Wolf UI já o procura
   lá, então ele continua funcionando.

   ```yaml
   services:
     wolf:
       # ... o que você já tem ...
       environment:
         - WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock
       volumes:
         - /var/run/wolf:/var/run/wolf
   ```

   Atualize a stack (o Wolf reinicia; o Moonlight cai e conecta de novo) e
   confira:

   ```sh
   ls -l /var/run/wolf/       # srwxr-xr-x root root ... wolf.sock
   sudo curl -s --unix-socket /var/run/wolf/wolf.sock http://localhost/api/v1/lobbies; echo
   ```

2. **O PSPStream, numa stack separada** com o
   [`docker/pspstream.yml`](../docker/pspstream.yml), pelo Portainer (seção 4,
   compose path `docker/pspstream.yml`) ou na linha de comando:

   ```sh
   sudo bash install.sh --only-pspstream
   ```

   Se o seu Wolf mudou as portas de ping (`WOLF_VIDEO_PING_PORT`,
   `WOLF_AUDIO_PING_PORT`), repita os valores no `.env` do PSPStream. Para
   saber: `docker inspect steam-wolf-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep WOLF_`.

3. **Firewall**: 5123 UDP e TCP (e 5124 TCP para a interface web na rede).

---

## 6. Primeiro uso: Moonlight, lobby e PSP

1. **Moonlight**: adicione o servidor pelo IP. Na primeira vez, o Moonlight
   mostra um PIN, e o Wolf escreve no log um link para digitá-lo:

   ```sh
   cd /opt/wolf-pspstream && sudo docker compose logs wolf | grep -i pin
   ```

   (troque `localhost` no link pelo IP do servidor). Instalação de uma vez:
   na primeira vez que abre um app, o Wolf baixa a imagem dele, o que pode
   levar alguns minutos com a tela preta.

2. **Abra um jogo pelo Wolf UI**. O Wolf UI cria um **lobby**, de um de dois
   jeitos:

   | botão | lobby | o PSP |
   |---|---|---|
   | **Start** | de um jogador só | **assiste** enquanto o Moonlight estiver nele. Saindo do Moonlight (fechando o stream), o lobby continua rodando, e o PSP entra sozinho e passa a ser **o único controle** (o jogador 1) |
   | **Coop** | de vários jogadores | entra junto e joga como **mais um controle** (o do Moonlight, se houver, é o primeiro) |

   O Wolf UI cria os dois tipos sem "fechar quando todos saírem", então o
   lobby continua aberto até alguém pará-lo pelo Wolf UI.

3. **No PSP**: PSPStream → **Procurar o PC na rede** (ou o IP da LAN do
   servidor, porta 5123). O log do PSPStream mostra:

   ```
   Wolf: sessão ... espelhando lobby <jogo> (...), conversão nvidia
   controles: a sessão do PSP entrou no lobby <jogo> (...); o controle virtual vai para o jogo
   PSP conectado via UDP: 192.168.0.50:...
   ```

### Os controles no jogo

O PSP chega ao jogo como **um controle de Xbox**. Num lobby Coop com o
Moonlight usando um controle, o do PSP é o segundo: o jogo (ou o Steam Big
Picture) pode pedir para escolher qual controle é de qual jogador. Para o
PSP ser o primeiro, use Start e saia do Moonlight, ou deixe o Moonlight sem
controle (sem um gamepad ligado nele, sem os controles na tela e sem a opção
de manter o controle 1 sempre ligado, se o seu Moonlight tiver).

| PSP | perfil `xbox` | segurando SELECT |
|---|---|---|
| X / círculo / quadrado / triângulo | A / B / X / Y | L3 / R3 / BACK / Guide |
| direcional | direcional | analógico direito |
| L / R | LT / RT (gatilho inteiro) | LB / RB |
| START | Start | (SELECT + START é o menu do PSP) |
| analógico | analógico esquerdo | analógico esquerdo |

Um toque rápido no SELECT sozinho vale BACK. Outros perfis
(`PSPSTREAM_PROFILE`):
- `xbox-camera`: X/círculo/quadrado/triângulo viram o analógico direito
  (câmera) e o direcional vira A/B/X/Y.
- `xbox-ombros`: L/R = LB/RB, e SELECT + L/R = LT/RT.

START + cima + RB juntos é o atalho do Wolf UI e tira a sessão do lobby. No
perfil `xbox-ombros`, isso é START + cima + R no PSP. Se acontecer, o
PSPStream entra no lobby de novo em até 2 s.

---

## 7. Interface web: no servidor ou na rede

A interface web mostra o estado (o que está sendo espelhado, FPS, latência,
som, controles) e muda as configurações com o PSP conectado. As mudanças
ficam no volume `pspstream-config`.

**Só no servidor (o padrão):** `PSPSTREAM_WEB=127.0.0.1:5124`. De outro PC,
por um túnel SSH: `ssh -L 5124:127.0.0.1:5124 usuário@servidor` e
http://localhost:5124.

**Na rede local:** no `.env`,

```sh
PSPSTREAM_WEB=0.0.0.0:5124
PSPSTREAM_WEB_PASSWORD=uma-senha-longa
```

e libere a porta 5124/tcp no firewall. Acesse **http://IP-do-servidor:5124**;
o navegador pede usuário (qualquer um) e senha. Na rede local a senha vai
sem criptografia (HTTP): serve para a casa, não para uma rede em que você
não confia. Sem senha, quem alcança a porta muda as configurações.

A interface web só aceita endereços que conhece: `localhost`, o nome do
servidor (e `nome.local`) e qualquer IP. Outro nome, como um do DNS do
roteador, vai em `PSPSTREAM_WEB_HOSTS` (separados por vírgula); sem isso, a
página responde `endereço não permitido`. É uma proteção contra ataques de
DNS rebinding.

---

## 8. Configurações (`.env`)

Ficam no `.env` da pasta dos compose (ou nas variáveis da stack do
Portainer). Depois de mudar: `docker compose up -d`. O que a interface web
muda vale mais que o `.env` e fica gravado no volume.

| variável | padrão | o quê |
|---|---|---|
| `COMPOSE_FILE` | `compose.yml` | qual compose: `compose.yml`, `compose.nvidia.yml` ou `pspstream.yml` |
| `PSPSTREAM_IMAGE` | `ghcr.io/k7vinilstorage/pspstream:nightly` | `nightly` (a `main`), `latest` (a última versão), `1.2` (uma versão) ou `pspstream:local` (compilada) |
| `PSPSTREAM_VIDEO_CONVERT` | `auto` (`nvidia` no compose da NVIDIA) | como o Wolf desce a imagem da GPU: `nvidia`, `va` (Intel/AMD), `cpu` (Wolf com `WOLF_USE_ZERO_COPY=FALSE`) ou `auto` (tenta nessa ordem, ~10 s por tentativa que falha) |
| `PSPSTREAM_WOLF_TARGET` | vazio = o único lobby aberto | o nome (ou id) do lobby a espelhar; com vários abertos, o log lista os nomes |
| `PSPSTREAM_WOLF_PIN` | vazio | o PIN do lobby, se ele pede |
| `PSPSTREAM_PROFILE` | `xbox` | `xbox`, `xbox-camera` ou `xbox-ombros` |
| `PSPSTREAM_WEB` | `127.0.0.1:5124` | `0.0.0.0:5124` abre para a rede ([seção 7](#7-interface-web-no-servidor-ou-na-rede)) |
| `PSPSTREAM_WEB_PASSWORD` | vazio | senha da interface web |
| `PSPSTREAM_WEB_HOSTS` | vazio | nomes aceitos além do IP e do nome do servidor (ex.: um do DNS do roteador), separados por vírgula |
| `WOLF_IMAGE` | `ghcr.io/games-on-whales/wolf:stable` | a imagem do Wolf |
| `WOLF_VIDEO_PING_PORT`, `WOLF_AUDIO_PING_PORT` | 48100, 48200 | só se você mudou as do Wolf |

Para outras opções do servidor (`--fps`, `--codec`, `-v`...), acrescente ao
`command` do serviço `pspstream` no compose, por exemplo
`command: ["--source", "wolf", "--fps", "30", "-v"]`.

### A imagem do PSPStream

O CI publica a imagem em `ghcr.io/k7vinilstorage/pspstream` a cada push na
`main` (`nightly` e `sha-<commit>`) e a cada versão (`latest` e `1.2`). Antes
de o código ir para a `main`, ou se a imagem não baixar, compile:

```sh
cd /opt/wolf-pspstream
sudo docker build -t pspstream:local "https://github.com/k7vinilstorage/PSP-Stream.git#main"
# no .env: PSPSTREAM_IMAGE=pspstream:local
```

(ou, de um clone: `docker compose -f compose.yml -f build.yml up -d --build`).

---

## 9. Atualizar e desfazer

**Atualizar:** rode o instalador de novo, ou:

```sh
cd /opt/wolf-pspstream && sudo docker compose pull && sudo docker compose up -d
```

No Portainer com Git: *Pull and redeploy*. O EBOOT do PSP só muda se a nota
da versão disser.

**Desfazer:**

```sh
cd /opt/wolf-pspstream && sudo docker compose down -v
```

Isso apaga também o volume das configurações da interface web. O Wolf guarda
o estado dele (pareamentos, apps) em `/etc/wolf`, que continua lá.
Num Wolf que já existia, tire também as duas linhas do serviço dele.

---

## 10. Problemas

| no log do PSPStream | o que fazer |
|---|---|
| `o socket da API do Wolf não existe: /var/run/wolf/wolf.sock` | o Wolf não tem as duas linhas (`WOLF_SOCKET_PATH` e o volume `/var/run/wolf`) ou ainda está subindo: `ls -l /var/run/wolf/` |
| `sem permissão para abrir /var/run/wolf/wolf.sock` | o serviço `pspstream` precisa de `user: "0:0"` (já vem nos compose); `ls -ld /var/run/wolf` deve ser `drwxr-xr-x root root` |
| `ninguém atende em /var/run/wolf/wolf.sock` | o Wolf está parado ou reiniciando: `docker compose logs wolf` |
| `nenhum lobby aberto no Wolf; esperando um` | abra um jogo pelo Wolf UI |
| `há vários lobbies abertos no Wolf` | `PSPSTREAM_WOLF_TARGET=<nome>`; a mensagem lista os nomes |
| `nenhum frame com a conversão ...` | a conversão não bate com a GPU do Wolf: `PSPSTREAM_VIDEO_CONVERT` (`nvidia`, `va`, `cpu`) e o erro em `docker compose logs wolf` |
| `... entrar no lobby: ... Lobby is full` | lobby de um jogador (Start) com o Moonlight dentro: o PSP assiste até o Moonlight sair. Para jogar junto, use Coop |
| `... Invalid PIN` | `PSPSTREAM_WOLF_PIN` |
| `o alvo é uma sessão Moonlight avulsa` | o alvo aponta para uma sessão fora de um lobby: só dá para assistir |

| sintoma | o que fazer |
|---|---|
| o PSP não acha o servidor | firewall (5123 UDP e TCP), IP da LAN, "isolamento de clientes" no roteador |
| tela preta no PSP | o log diz `espelhando lobby`? Se diz `nenhum frame`, veja a conversão acima |
| imagem ok, os botões não fazem nada | o log diz `entrou no lobby`? Num lobby Start com o Moonlight dentro, o PSP só assiste. No jogo, o PSP pode ser o segundo controle ([seção 6](#os-controles-no-jogo)) |
| a interface web pede senha e não aceita | a senha está em `PSPSTREAM_WEB_PASSWORD` no `.env` (o usuário pode ser qualquer um) |
| `endereço não permitido` na interface web | um nome que ela não conhece: use o IP ou ponha o nome em `PSPSTREAM_WEB_HOSTS` |
| o Wolf UI parou de abrir | em `/etc/wolf/cfg/config.toml`, o app Wolf UI deve montar `/var/run/wolf/wolf.sock:/var/run/wolf/wolf.sock` e ter `WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock` (é o padrão do Wolf) |

**O que mandar se não funcionar:**

```sh
cd /opt/wolf-pspstream    # ou a pasta/stack de vocês
sudo docker compose logs --tail 80 pspstream
sudo docker compose logs --since 10m wolf 2>&1 | grep -iE "pspstream|gstreamer|pipeline|error|warn|lobby|api" | tail -80
ls -l /var/run/wolf/
```

Com `-v` no `command` do `pspstream`, o log mostra os pipelines que o
PSPStream pede ao Wolf.
