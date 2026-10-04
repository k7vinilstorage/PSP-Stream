# Histórico de versões

As versões do EBOOT e do servidor andam juntas. O protocolo tem a própria
versão (`PSC5` = v5) e só muda quando o formato das mensagens muda; um EBOOT
de outra versão do protocolo é recusado com aviso no log.

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
