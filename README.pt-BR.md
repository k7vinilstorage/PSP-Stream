# PSPStream

[![build](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml/badge.svg)](https://github.com/k7vinilstorage/PSP-Stream/actions/workflows/build.yml)

[English](README.md) · **Português**

Transmite a tela e o som do PC para um PSP pelo Wi-Fi e manda os botões do
PSP de volta ao PC, como teclado e mouse ou como um controle de Xbox. O vídeo
vai em H.264 com frames P, decodificado pelo hardware do PSP, e o som em IMA
ADPCM. Inspirado no
[RNDS-Stream](https://github.com/gavff64/RNDS-Stream), que faz o mesmo para o
Nintendo DSi.

```
 PC (Linux, Wayland)                                     PSP (homebrew)
 ┌───────────────────────────────────────┐   Wi-Fi     ┌──────────────────────────────┐
 │ captura KMS (60 fps) ou portal        │   802.11b   │ thread de rede: pedaços UDP, │
 │ GPU: reduz para 480x272               │ ──H.264──>  │   NACK, fila em ordem        │
 │ openh264: frame P na hora do pedido   │             │ Media Engine: decode H.264   │
 │ qualidade adaptativa à banda          │ <─pedido──  │   direto na VRAM             │
 │ uinput: teclado/mouse ou Xbox virtual │  + botões   │ tela de configuração         │
 └───────────────────────────────────────┘             └──────────────────────────────┘
```

A documentação completa está na **[wiki](https://github.com/k7vinilstorage/PSP-Stream/wiki)**
(em inglês).

## Em português

O PSPStream fala inglês por padrão, e tudo tem a versão em português:

| onde | como |
|---|---|
| servidor e interface web | `--lang pt`, `PSPSTREAM_LANG=pt` ou **Language** na interface web (vale na hora e fica gravado) |
| PSP | `lang=pt` no `server.txt` ou o item **Language / Idioma** na tela de configuração |
| instalador do Docker | `sudo bash install.sh --lang pt` (também grava `PSPSTREAM_LANG=pt` no `.env`) |
| Docker / Portainer | `PSPSTREAM_LANG=pt` no `.env` ou nas variáveis da stack |

Os perfis de controle ganharam nomes em inglês (`game`, `arrows`,
`xbox-shoulders`); os nomes antigos (`jogo`, `setas`, `xbox-ombros`)
continuam valendo.

## Recursos

- **Perto de 60 fps lisos no PSP-3000** com H.264 com frames P,
  decodificado pelo Media Engine do PSP. Com a tela parada, cada frame tem
  ~100 bytes.
- **Captura a 60 fps** pela KMS (direto da placa de vídeo) ou pelo portal do
  Wayland (GNOME, KDE).
- **Som do PC** no PSP: IMA ADPCM a 44,1 kHz estéreo, ~46 KB/s.
- **Controles**: um controle de Xbox 360 virtual, como no Sunshine, ou
  teclado e mouse.
- **UDP com recuperação de perdas** e qualidade adaptativa à vazão do Wi-Fi.
- **Tela de configuração no PSP**, com "Procurar o PC na rede".
- **Interface web** no PC para mudar as configurações com o PSP conectado.
- **Wolf (Games on Whales)**: o lobby do Wolf no PSP, com som e controles,
  num container ao lado dele.
- **Servidor para Windows (experimental)**: um zip com o `pspstream.exe`,
  captura da tela pelo Desktop Duplication, som, teclado/mouse e o controle
  de Xbox (com o driver ViGEmBus).

## Como funciona

O PSP **pede** cada frame, e o servidor responde com o mais recente,
codificado na hora (o modelo "pull" do RNDS-Stream). Assim nunca se forma
fila na rede, e a latência fica perto de um frame.

1. O servidor captura a tela, reduz para 480x272 na GPU e codifica em H.264
   (ou JPEG, para EBOOTs antigos) quando o pedido chega.
2. O frame vai em pedaços UDP. Um pedaço perdido é pedido de novo (NACK), e
   a qualidade se ajusta à vazão do Wi-Fi.
3. O PSP decodifica no Media Engine, direto na memória de vídeo, e já pede o
   próximo enquanto decodifica o atual.
4. Os botões vão junto com cada pedido e viram teclado, mouse ou controle de
   Xbox no PC (uinput).
5. O som vai à parte, em pacotes de 20 ms, e não depende do vídeo.

Os detalhes estão nas páginas
[Protocol](https://github.com/k7vinilstorage/PSP-Stream/wiki/Protocol) e
[Design Decisions](https://github.com/k7vinilstorage/PSP-Stream/wiki/Design-Decisions)
da wiki.

## Início rápido

É preciso:
- um PSP com firmware customizado;
- um PC com Linux (Fedora, Ubuntu, Debian, Arch, openSUSE), ou Windows
  10/11 (experimental, [Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows));
- um roteador com Wi-Fi 2,4 GHz em modo misto b/g/n, porque o PSP só fala
  802.11b.

### 1. No PSP

1. Baixe o
   [`PSPStream-EBOOT.zip`](https://github.com/k7vinilstorage/PSP-Stream/releases/latest/download/PSPStream-EBOOT.zip)
   e copie a pasta `PSP` para a raiz do memory stick.
2. No XMB, crie a conexão com o roteador e, em **Ajustes de economia de
   energia**, deixe a **Economia de energia WLAN** desligada.
3. Para as telas em português: `lang=pt` no `server.txt`, ou o item
   **Language / Idioma** na tela de configuração.

### 2. No PC

Com os pacotes das
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases)
(`sudo apt install ./pspstream_*.deb` ou `sudo dnf install ./pspstream-*.rpm`),
ou pelo código, em qualquer distribuição:

```sh
git clone https://github.com/k7vinilstorage/PSP-Stream && cd PSP-Stream
python3 server/pspstream.py --setup --lang pt      # instala o que falta, perguntando antes
python3 server/pspstream.py --source kms --profile xbox --lang pt
```

Com o pacote, o comando é `pspstream`. O `--setup` cuida dos pacotes, da
permissão dos controles, do firewall (porta 5123 UDP e TCP) e da captura
KMS. Para só conferir, use `--check`. Escolhido na interface web, o idioma
fica gravado e o `--lang` não precisa ir toda vez.

### 3. Conectar

Abra o PSPStream no PSP, escolha **Find the PC on the network** ("Procurar
o PC na rede", em português) com X e aperte **START**. Das próximas vezes,
ele conecta sozinho. As configurações do PC ficam em
**http://localhost:5124**.

### No Windows (experimental)

Baixe o `PSPStream-Setup-x64.exe` das
[Releases](https://github.com/k7vinilstorage/PSP-Stream/releases) e rode: o
assistente (em português) instala o PSPStream, libera o firewall e, se você
quiser o controle de Xbox, instala o driver ViGEmBus. Depois abra o
**PSPStream** pelo menu Iniciar e escolha **Language** na interface web.
Também há um zip portátil. Detalhes na página
[Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows) da wiki.

### Com o Wolf (Games on Whales)

Num servidor com Docker, o instalador sobe o Wolf e o PSPStream:

```sh
curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
sudo bash install.sh --lang pt
```

Para pôr o PSPStream ao lado de um Wolf que já existe, ou subir pelo
Portainer, veja a página
[Wolf](https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf).

## Atalhos no PSP

Segure **SELECT + START** e aperte:

| botão | faz |
|---|---|
| triângulo | liga/desliga o overlay (FPS, KB por frame, tempos) |
| cima | liga/desliga o som |
| R | abre a tela de configuração |
| L | troca o transporte TCP/UDP |

Os outros atalhos e o overlay estão em
[Using the PSP](https://github.com/k7vinilstorage/PSP-Stream/wiki/Using-the-PSP).
Os botões do controle de Xbox estão em
[Controls](https://github.com/k7vinilstorage/PSP-Stream/wiki/Controls).

## Documentação

As páginas da wiki, em inglês:

| página | o que tem |
|---|---|
| [Installation](https://github.com/k7vinilstorage/PSP-Stream/wiki/Installation) | requisitos, pacotes por distribuição, firewall, uinput, captura KMS, compilar o EBOOT |
| [Using the PSP](https://github.com/k7vinilstorage/PSP-Stream/wiki/Using-the-PSP) | tela de configuração, atalhos, overlay, `server.txt` |
| [Controls](https://github.com/k7vinilstorage/PSP-Stream/wiki/Controls) | controle de Xbox, teclado e mouse, perfis próprios |
| [Web Interface](https://github.com/k7vinilstorage/PSP-Stream/wiki/Web-Interface) | as configurações pelo navegador, na rede com senha |
| [Wolf](https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf) | Docker, instalador, Portainer, lobbies Start e Coop |
| [Server Options](https://github.com/k7vinilstorage/PSP-Stream/wiki/Server-Options) | a linha de comando, o idioma e a linha de estatística |
| [Troubleshooting](https://github.com/k7vinilstorage/PSP-Stream/wiki/Troubleshooting) | sintoma e o que fazer |
| [Performance](https://github.com/k7vinilstorage/PSP-Stream/wiki/Performance) | FPS, latência e banda medidos no PSP-3000 |
| [Windows](https://github.com/k7vinilstorage/PSP-Stream/wiki/Windows) | o servidor no Windows (experimental) |
| [Development](https://github.com/k7vinilstorage/PSP-Stream/wiki/Development) | builds, releases, testes, traduções, estrutura do código |

Todas as páginas, inclusive o protocolo, as medições e as limitações, estão
na [wiki](https://github.com/k7vinilstorage/PSP-Stream/wiki). Elas são
geradas da pasta [`wiki/`](wiki) deste repositório. O histórico de versões
está no [CHANGELOG.md](CHANGELOG.md).

## Créditos

- [RNDS-Stream](https://github.com/gavff64/RNDS-Stream): a ideia do modelo
  pull e do servidor + homebrew.
- [pspdev](https://github.com/pspdev): toolchain e PSPSDK.
- [openh264](https://www.openh264.org/) (Cisco), libjpeg-turbo, GStreamer,
  PPSSPP (testes no emulador).
- PMP Mod/PMPlayer: o caminho de decode H.264 cru no PSP (`sceMpegBasePESpacketCopy`).
- [Sunshine](https://github.com/LizardByte/Sunshine): referência para a
  captura KMS e o controle virtual.
- [Wolf](https://github.com/games-on-whales/wolf) (Games on Whales): a API e
  os lobbies que o `--source wolf` usa.
- [Moonlight](https://moonlight-stream.org/): o formato dos pacotes de
  controle que o Wolf recebe.

## Licença

[MIT](LICENSE), © 2026 João Torezan.
