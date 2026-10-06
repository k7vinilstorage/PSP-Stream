# PSPStream no Wolf (Games on Whales), passo a passo

Este guia põe o PSPStream num container ao lado do Wolf, para o PSP ver e
jogar o que roda num lobby do Wolf, com som e controles. Foi escrito para um
Wolf em Docker gerenciado pelo **Portainer**, com placa **NVIDIA**, mas os
comandos valem para qualquer `docker compose`.

> **Experimental.** Tudo foi testado contra um Wolf falso (que imita a API e
> os pipelines conforme o código do Wolf `stable`), inclusive a imagem
> Docker e este compose, mas ainda não num Wolf de verdade. Se algo falhar,
> a seção [O que mandar se não funcionar](#o-que-mandar-se-não-funcionar)
> diz o que juntar.

Resumo do que vai mudar:

| onde | o quê |
|---|---|
| serviço `wolf` | 2 linhas: o socket da API passa a aparecer no host, em `/var/run/wolf` |
| serviço novo `pspstream` | o servidor do PSPStream, na rede do host |
| firewall do servidor | porta 5123, UDP e TCP |
| PSP | nada (o EBOOT de sempre) |

```
 Moonlight ──┐                          ┌── PSP (Wi-Fi 802.11b, porta 5123)
             ▼                          ▼
 ┌────────────── Wolf ──────────────┐  ┌──── PSPStream ────┐
 │ lobby (jogo) ─ imagem e som ─────┼──┼> 480x272, H.264   │
 │ controle virtual <── API ────────┼──┼─ botões do PSP    │
 └──────────── /var/run/wolf/wolf.sock (API) ─────────────┘
```

---

## 0. Antes de começar

Confira:

1. **O Wolf funciona com o Moonlight**, incluindo o Wolf UI (a tela de
   escolher o jogo).
2. **O PSP tem o PSPStream** instalado (o mesmo EBOOT de sempre; não muda
   nada no PSP) e conecta no Wi-Fi da casa.
3. **O IP da LAN do servidor**, que é o que o PSP vai usar:

   ```sh
   hostname -I
   ```

   Use o da rede da casa (ex.: `192.168.0.10`), e não o do Tailscale
   (`100.x.y.z`) nem os do Docker (`172.x.y.z`): o PSP só fala 802.11b na
   rede local.

4. **Como o seu Wolf está configurado**. Rode e guarde a saída:

   ```sh
   docker inspect steam-wolf-1 --format '{{range .Config.Env}}{{println .}}{{end}}' \
     | grep -E '^(WOLF_|XDG_RUNTIME_DIR|NVIDIA)'
   ```

   O que interessa:

   | variável | o que muda para o PSPStream |
   |---|---|
   | `WOLF_USE_ZERO_COPY=FALSE` | a conversão é `cpu` em vez de `nvidia` |
   | `WOLF_VIDEO_PING_PORT`, `WOLF_AUDIO_PING_PORT` | se existirem, repita no serviço do PSPStream (passo 2) |
   | `WOLF_SOCKET_PATH` | se já existir, use o mesmo caminho no passo 1 |

   Sem nada disso (o mais comum), siga o guia como está.

---

## 1. Mostrar o socket da API do Wolf no host

Hoje o Wolf cria o socket da API dentro do container, num volume anônimo
(`/run/user/wolf/wolf.sock`). O PSPStream precisa dele num lugar
compartilhado.

**No Portainer:** *Stacks* → `steam` → aba **Editor**. No serviço `wolf`,
acrescente uma linha em `environment` e uma em `volumes` (não apague nada
do que já existe):

```yaml
services:
  wolf:
    image: ghcr.io/games-on-whales/wolf:stable
    # ... o que você já tem continua igual ...
    environment:
      # ... as variáveis que você já tem ...
      - WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock
    volumes:
      # ... os volumes que você já tem ...
      - /var/run/wolf:/var/run/wolf
```

Se o seu `environment` estiver no formato de mapa (`CHAVE: valor`), use:

```yaml
    environment:
      WOLF_SOCKET_PATH: /var/run/wolf/wolf.sock
```

Clique em **Update the stack** (sem "Re-pull image"). O Wolf reinicia:
quem estiver jogando pelo Moonlight cai e conecta de novo.

> **Se a stack veio de um repositório Git** (Portainer → *Repository*), a
> aba Editor não aparece: mude o arquivo compose no repositório.

### Conferir

```sh
ls -l /var/run/wolf/
```

Deve aparecer o socket, do root:

```
srwxr-xr-x 1 root root 0 ... wolf.sock
```

E a API responde (o `sudo` é porque o socket é do root):

```sh
sudo curl -s --unix-socket /var/run/wolf/wolf.sock http://localhost/api/v1/lobbies; echo
```

Algo como `{"success":true,"lobbies":[]}` (a lista enche quando houver um
jogo aberto).

Por fim, abra o **Wolf UI pelo Moonlight**, para ver que ele continua
funcionando. A configuração padrão do Wolf UI já monta
`/var/run/wolf/wolf.sock`, que agora existe. Se o Wolf UI parar de abrir,
veja [Problemas](#problemas).

> **Cuidado:** esse socket dá controle total do Wolf (parear clientes,
> iniciar apps). Monte `/var/run/wolf` só no container do PSPStream, e nunca
> o exponha pela rede.

---

## 2. Compilar a imagem do PSPStream

No servidor, um comando só (baixa o código do GitHub e compila; alguns
minutos na primeira vez, ~900 MB):

```sh
docker build -t pspstream:latest \
  "https://github.com/k7vinilstorage/PSP-Stream.git#claude/psp-pc-screen-stream-lou5q7"
```

Depois que o branch entrar na `main`, troque o fim por `#main`.

Se o `docker build` reclamar do git, clone e compile na pasta:

```sh
git clone -b claude/psp-pc-screen-stream-lou5q7 https://github.com/k7vinilstorage/PSP-Stream
docker build -t pspstream:latest PSP-Stream
```

Teste rápido:

```sh
docker run --rm pspstream:latest --version
```

Deve mostrar `PSPStream 1.1`.

> **Alternativa:** deixar o Portainer compilar, com a linha
> `build: https://github.com/k7vinilstorage/PSP-Stream.git#claude/psp-pc-screen-stream-lou5q7`
> no serviço (como no `docker/compose.yml` do repositório). Se o Portainer
> reclamar de `build`, use o comando acima e deixe só `image:`.

---

## 3. Acrescentar o serviço `pspstream` à stack

De novo em *Stacks* → `steam` → **Editor**. Cole o serviço abaixo dentro de
`services:` (no mesmo nível do `wolf`), e o bloco `volumes:` do fim no nível
de cima do arquivo. Se você já tem um `volumes:` no nível de cima, só
acrescente a linha `pspstream-config:` nele.

```yaml
  pspstream:
    image: pspstream:latest
    network_mode: host
    restart: unless-stopped
    depends_on:
      - wolf
    user: "0:0"
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    read_only: true
    tmpfs:
      - /tmp
    volumes:
      - /var/run/wolf:/var/run/wolf:ro
      - pspstream-config:/config
    environment:
      - WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock
    command: ["--source", "wolf", "--wolf-video-convert", "nvidia"]

volumes:
  pspstream-config:
```

O que cada parte faz:

| linha | por quê |
|---|---|
| `network_mode: host` | o PSP fala com a porta 5123 do servidor, e o Wolf manda a imagem e o som para `127.0.0.1`: precisa da rede do host (como o Wolf) |
| `depends_on: wolf` | sobe depois do Wolf e para antes dele (assim encerra a sessão no Wolf). Use o nome do serviço do Wolf na sua stack |
| `user: "0:0"` | o socket do Wolf é só do root (por isso o próprio Wolf UI roda como root) |
| `cap_drop: ALL`, `no-new-privileges`, `read_only` | o uid 0 aqui não tem nenhum poder de root: só conecta no socket, de que é dono |
| `/var/run/wolf:/var/run/wolf:ro` | o diretório do socket (o Wolf o recria a cada início), só leitura |
| `pspstream-config:/config` | o que você mudar na interface web fica gravado |
| `--wolf-video-convert nvidia` | como o Wolf desce a imagem da GPU. `nvidia` com NVIDIA; `cpu` se o seu Wolf tem `WOLF_USE_ZERO_COPY=FALSE`; `va` para Intel/AMD; `auto` testa as três (~10 s por tentativa que falha) |

Se no passo 0 apareceram `WOLF_VIDEO_PING_PORT` ou `WOLF_AUDIO_PING_PORT`,
copie as mesmas linhas para o `environment` do `pspstream`.

Clique em **Update the stack**. O Wolf não reinicia de novo (não mudou
nada nele); só o `pspstream` sobe.

---

## 4. Liberar a porta no firewall

O PSP precisa chegar na porta **5123, UDP e TCP** do servidor. Veja se há
firewall:

```sh
sudo ufw status
```

Se disser `active`:

```sh
sudo ufw allow 5123/udp && sudo ufw allow 5123/tcp
```

Com firewalld: `sudo firewall-cmd --permanent --add-port=5123/udp
--add-port=5123/tcp && sudo firewall-cmd --reload`.

O roteador também não pode isolar os aparelhos do Wi-Fi ("AP isolation",
rede de convidados).

---

## 5. Abrir um jogo e ver o log

1. **No Moonlight, abra o Wolf UI e inicie um jogo.** Isso cria um lobby.
   Deixe o Moonlight conectado no primeiro teste: dependendo de como o
   lobby foi criado, ele fecha quando o último jogador sai.
2. **Acompanhe o log do PSPStream** (Portainer → *Containers* →
   `steam-pspstream-1` → *Logs*, ou):

   ```sh
   docker logs -f steam-pspstream-1
   ```

O que deve aparecer, em ordem:

```
controles: perfil 'xbox' (controle de Xbox virtual no Wolf, ...)
som: do Wolf (o som do alvo, pela sessão do PSPStream), 44100 Hz estéreo, ...
PSPStream 1.1: aguardando o PSP em 192.168.0.10:5123, TCP e UDP ...
Wolf: sessão ... espelhando lobby <nome do jogo> (...), conversão nvidia
controles: a sessão do PSP entrou no lobby <nome do jogo> (...); o controle virtual vai para o jogo
```

Antes de abrir o jogo, o normal é:

```
Wolf: nenhum lobby aberto no Wolf; esperando um (abra um jogo pelo Wolf UI)
```

Quando você abrir o jogo, o PSPStream cria a sessão sozinho; não precisa
reiniciar nada.

---

## 6. Conectar o PSP

No PSP, abra o PSPStream e escolha **Procurar o PC na rede**. Se não achar,
digite o IP da LAN do servidor (passo 0) na tela de configuração, porta
5123.

Quando o PSP conectar, o log mostra:

```
PSP conectado via UDP: 192.168.0.50:...
controles: controle de Xbox virtual ligado na sessão ... do Wolf
som: ligado no PSP (44100 Hz, estéreo, ~46 KB/s)
captura: a fonte entrega 60.0 fps (...)
```

No jogo, os botões do PSP chegam como **um segundo controle de Xbox** (o
primeiro é o do Moonlight). Alguns jogos e o Steam Big Picture pedem para
escolher qual controle é de qual jogador.

### Botões (perfil `xbox`)

Os mesmos do PSPStream no PC (README, seção Controles). Segurando
**SELECT**, os outros botões mudam de função; um toque rápido no SELECT
sozinho vale BACK.

| PSP | `xbox` | segurando SELECT |
|---|---|---|
| X / círculo / quadrado / triângulo | A / B / X / Y | L3 / R3 / BACK / Guide |
| direcional | direcional | analógico direito |
| L / R | LT / RT (gatilho inteiro) | LB / RB |
| START | Start | (SELECT + START é o menu do PSP) |
| analógico | analógico esquerdo | analógico esquerdo |

Outros perfis, pelo `--profile` (passo 8):

- `xbox-camera`, para jogos 3D: X/círculo/quadrado/triângulo viram o
  analógico direito (câmera) e o direcional vira A/B/X/Y.
- `xbox-ombros`: L/R = LB/RB, e SELECT + L/R = LT/RT.

> **Atalho do Wolf UI:** START + cima + RB juntos tira a sessão do lobby,
> como no Moonlight. No perfil `xbox-ombros` isso é START + cima + R no PSP
> (nos outros, o RB precisa do SELECT, e SELECT + START é o menu do PSP). Se
> acontecer, o PSPStream entra no lobby de novo em até 2 s.

---

## 7. Interface web (opcional)

A interface web fica em `http://127.0.0.1:5124` **no servidor** (por
segurança, só no próprio servidor). De outro PC, abra um túnel:

```sh
ssh -L 5124:127.0.0.1:5124 joao@torezan-server
```

e acesse **http://localhost:5124** no navegador desse PC. A página mostra
uma linha **Wolf** (o que está espelhando, a conversão, se os controles
estão no lobby), o FPS no PSP, a latência, o som, e deixa mudar qualidade,
FPS, perfil, alvo do Wolf etc. As mudanças ficam no volume
`pspstream-config`.

O que estiver no `command` do compose vale mais que a página (ela marca
esses campos como "linha de comando"). Para mudar pela página, tire a opção
do `command`.

---

## 8. Opções úteis no `command`

Mude a lista do `command` no compose e clique em **Update the stack**.

| opção | quando usar |
|---|---|
| `--wolf-target "Nome do lobby"` | com mais de um lobby aberto (o log lista os nomes e ids); também aceita o id |
| `--wolf-pin 1234` | o lobby foi criado com PIN (sem ele, o PSP só assiste) |
| `--profile xbox-camera` | outro perfil de controle (`xbox`, `xbox-camera`, `xbox-ombros`) |
| `--no-input` | só assistir: a sessão do PSP não entra no lobby |
| `--no-audio` | sem som no PSP |
| `--fps 30` | menos frames, menos banda no Wi-Fi do PSP |
| `--codec jpeg` | se o H.264 der problema (precisa de mais banda) |
| `-v` | log detalhado (mostra os pipelines que o PSPStream manda o Wolf rodar) |

Exemplo:

```yaml
    command: ["--source", "wolf", "--wolf-video-convert", "nvidia", "--wolf-target", "Steam", "--profile", "xbox-camera"]
```

---

## 9. Atualizar o PSPStream

Compile de novo e recrie o container:

```sh
docker build -t pspstream:latest \
  "https://github.com/k7vinilstorage/PSP-Stream.git#claude/psp-pc-screen-stream-lou5q7"
```

Depois, no Portainer, **Update the stack** (ou, no terminal,
`docker rm -f steam-pspstream-1` e *Update the stack*). O EBOOT do PSP só
muda se a nota da versão disser.

## 10. Desfazer

1. Apague o serviço `pspstream` (e o volume `pspstream-config`) da stack.
2. Tire as duas linhas do serviço `wolf` (passo 1).
3. **Update the stack**.

---

## Como funciona, em poucas linhas

- O PSPStream cria uma **sessão própria** no Wolf pela API, como a de um
  cliente Moonlight, só que sem o Moonlight.
- O Wolf roda nessa sessão um **pipeline do PSPStream**, que escuta a
  imagem e o som do lobby, reduz a imagem para 480x272 na GPU e manda tudo
  por TCP em `127.0.0.1`. O PSPStream codifica para o PSP como sempre.
- Com os controles ligados, a sessão **entra no lobby**, e o Wolf liga o
  controle virtual dela no jogo.
- Uma thread consulta a API a cada 2 s: se o lobby fecha, a sessão é
  encerrada e o PSPStream espera outro abrir. Parando o container, a sessão
  é encerrada no Wolf.

Limites (do Wolf, que só com a API não dá para mudar):

- **Um PSPStream por Wolf.** O Wolf dá o mesmo id a toda sessão criada pela
  API.
- Cada sessão do PSP faz o Wolf subir um app "dummy" (um compositor e um
  sink de som que ninguém vê) e criar uma pasta `<uuid>/dummy`, vazia, em
  `/etc/wolf`. Cuidado se for limpar: as pastas dos clientes Moonlight de
  verdade têm nomes no mesmo formato (e guardam os jogos salvos).
- Só controle de Xbox (sem teclado e mouse), sem vibração.
- No lobby, o PSP conta como jogador: num lobby que fecha quando todos
  saem, se o PSP for o último, o lobby fecha.
- Quando a sessão do PSP entra no lobby, o log do Wolf mostra `Failed to get
  video interpipesrc for ...`: é esperado.

---

## Problemas

| no log do PSPStream | o que fazer |
|---|---|
| `o socket da API do Wolf não existe: /var/run/wolf/wolf.sock` | o passo 1 não pegou: `ls -l /var/run/wolf/`, e confira `WOLF_SOCKET_PATH` no serviço `wolf` |
| `sem permissão para abrir /var/run/wolf/wolf.sock` | falta `user: "0:0"` no `pspstream`; e `ls -ld /var/run/wolf` deve ser `drwxr-xr-x root root` |
| `ninguém atende em /var/run/wolf/wolf.sock` | o Wolf está parado ou reiniciando: `docker logs steam-wolf-1` |
| `nenhum lobby aberto no Wolf; esperando um` | abra um jogo pelo Wolf UI |
| `há vários lobbies abertos no Wolf; escolha um com --wolf-target` | passo 8 (`--wolf-target "Nome"`); a mensagem lista os lobbies |
| `nenhum frame com a conversão nvidia` | a conversão não bate com o seu Wolf: tente `cpu` (ou `auto`) e mande o log do Wolf (abaixo) |
| `o Wolf não deixou a sessão do PSP entrar no lobby: Lobby is full` | o lobby é de um jogador só e já tem alguém; o PSP só assiste até vagar |
| `... Invalid PIN` | `--wolf-pin` com o PIN do lobby |
| `o alvo é uma sessão Moonlight avulsa` | o `--wolf-target` aponta para uma sessão fora de um lobby: só dá para assistir |

| sintoma | o que fazer |
|---|---|
| o PSP não acha o servidor | firewall (passo 4), IP da LAN (passo 0), "AP isolation" no roteador; `network_mode: host` no `pspstream` |
| o PSP conecta, mas a tela fica preta | o log diz `espelhando lobby`? Se diz `nenhum frame`, veja a linha da conversão acima |
| imagem ok, sem controles | o log diz `entrou no lobby`? Se não, veja as linhas de lobby acima; no jogo, o PSP é o segundo controle |
| o Wolf UI parou de abrir depois do passo 1 | em `/etc/wolf/cfg/config.toml`, o app Wolf UI deve montar `/var/run/wolf/wolf.sock:/var/run/wolf/wolf.sock` e ter `WOLF_SOCKET_PATH=/var/run/wolf/wolf.sock` (é o padrão do Wolf) |
| o `pspstream` reinicia em loop | `docker logs steam-pspstream-1`: o erro está nas primeiras linhas |

## O que mandar se não funcionar

```sh
docker logs steam-pspstream-1 2>&1 | tail -80
docker logs steam-wolf-1 --since 10m 2>&1 | grep -iE "pspstream|gstreamer|pipeline|error|warn|lobby|api" | tail -80
ls -l /var/run/wolf/
```

Para ver os pipelines que o PSPStream pediu ao Wolf, ponha `-v` no
`command` antes de juntar o log.
