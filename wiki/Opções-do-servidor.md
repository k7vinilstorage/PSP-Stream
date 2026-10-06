As opções mais usadas do servidor (`python3 server/pspstream.py` ou
`pspstream`). Todas, com a explicação de cada uma: `--help`. Boa parte também
muda pela [Interface web](Interface-web), com o PSP conectado.

## Opções

| opção | o que faz |
|---|---|
| `--source kms` | direto da placa de vídeo: 60 fps, sem cursor (recomendado no GNOME 50); `--kms-monitor 1` escolhe o segundo monitor |
| `--source portal` | portal do Wayland (padrão); `--window` captura uma janela, `--forget` pergunta de novo o que capturar |
| `--source test` / `static --image arq.png` | padrão animado / imagem fixa (testes) |
| `--source x11` / `gst --gst-src "..."` | sessão X11 / pipeline GStreamer próprio |
| `--source wolf` | o que roda num lobby do Wolf, pela API dele; `--wolf-target`, `--wolf-pin`, `--wolf-video-convert` (ver [Wolf](Wolf#8-configurações-env)) |
| `--profile xbox` | controles: `jogo` (padrão), `desktop`, `setas`, `xbox`, `xbox-camera`, `xbox-ombros` ([Controles](Controles)) |
| `--codec auto` | `h264p` (padrão, se houver openh264), `h264` (só quadros completos) ou `jpeg` |
| `--h264-encoder auto` | libopenh264 direto, com o GStreamer de reserva (padrão); `gstreamer` força o caminho antigo |
| `--fixed-quality -q 70` | qualidade fixa em vez de adaptativa |
| `--target-fps 20`, `--q-min 25 --q-max 90` | alvo e limites da qualidade adaptativa |
| `--fps 60` | taxa máxima de captura. De uma tela de 60 Hz, 30 é um frame sim, um não (uniforme); 40 é 2 de cada 3 (intervalos de 17 e 33 ms: a média dá 40, mas o movimento é menos uniforme que a 30 ou 60) |
| `--scale bilinear2` | filtro de redução (padrão; `lanczos` deixa o texto um pouco mais nítido) |
| `--input-dry-run` | só mostrar no log o que seria injetado |
| `--dscp ef` | marca os pacotes para a fila de voz do Wi-Fi (WMM); `0` desliga |
| `--p-redundancy-ms 6` | frames P: o último pedaço de cada frame vai de novo depois disso, e a perda dele não trava o stream; `0` desliga |
| `--no-audio` | sem som (o PSP também desliga pelo `audio=0` ou SELECT + START + cima) |
| `--audio-device NOME` | fonte do som: `monitor` (padrão, o que sai nas caixas), `test` (tom de 440 Hz) ou uma fonte do `pactl list short sources` |
| `--audio-rate 44100`, `--audio-mono` | taxa (22050, 32000, 44100 ou 48000 Hz; 44100 é a do PSP) e mono: ~46 KB/s a 44,1 kHz estéreo, ~34 a 32 kHz, metade em mono |
| `--bench 30,50,70,90` | varre qualidades com o PSP conectado e grava uma tabela |
| `--web 127.0.0.1:5124`, `--no-web` | endereço da [Interface web](Interface-web) (`0.0.0.0:5124` = rede local; senha em `PSPSTREAM_WEB_PASSWORD`) ou nenhuma |
| `--web-allow-host NOME` | outro nome aceito no endereço da interface web, além do IP e do nome do PC |
| `--config ARQUIVO` | configurações gravadas pela interface web (padrão `~/.config/pspstream/server.json`) |
| `--check`, `--setup` | confere as dependências e diz o comando da sua distribuição; `--setup` também instala e configura, perguntando antes de cada passo |
| `-v` | log detalhado |

## A linha de estatística

A cada 2 s, o servidor mostra:

```
41.3 fps (fonte 59.8) | 1.2 KB/frame | Wi-Fi 52 KB/s | latência 29.1 ms (p95 41.0) ~ captura 1.0 + idade 3.4 + rede 11.8 + psp 12.9 | ...
```

"Latência" vai da captura no PC até o frame aparecer no PSP, medida só com
o relógio do servidor (ver [Protocolo](Protocolo#medição-de-latência-sem-sincronizar-relógios)).

Quando houve, a linha termina com os **engasgos**: 50 ms ou mais entre dois
frames, com a causa provável:

```
... | engasgos 3 (pior 74 ms: 2 perda, 1 pedido atrasado)
```

| causa | o que é |
|---|---|
| `perda` | um pedaço ou frame se perdeu no Wi-Fi e foi reenviado |
| `IDR` | o PSP perdeu a corrente de frames P e pediu um quadro completo |
| `pedido atrasado` | o pedido do PSP demorou a chegar: Wi-Fi lento naquele instante, ou o PSP ocupado |
| `captura` | o PC demorou a ter frame novo (o jogo ou a captura engasgou no PC) |
