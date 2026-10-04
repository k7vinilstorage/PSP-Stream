# Protocolo PSPStream v1

TCP, porta padrão **5123**. Todos os inteiros são **little-endian** (PSP e PC
x86 são LE, então não há conversão de ordem de bytes). Definições em
`psp/src/protocol.h` e `server/protocol.py`, que precisam ficar em sincronia.

## Modelo "pull"

O PSP **pede** cada frame, e o servidor responde com o **mais recente**
disponível. Frames que ficaram velhos enquanto o PSP estava ocupado são
descartados no PC e nunca entram na rede.

```
PSP                                   PC
 |-- REQ (HELLO|FRAME) ------------->  |
 |  <------------- FRAME 1 (hdr+jpeg)  |   o mais novo naquele instante
 |-- REQ (FRAME, ack=0) ------------>  |   pede o próximo ANTES de decodificar
 |   decodifica e exibe o 1            |   ... o 2 vem pela rede enquanto isso
 |  <------------- FRAME 2             |
 |-- REQ (FRAME, ack=1) ------------>  |   ack = último frame EXIBIDO
 |   decodifica e exibe o 2            |
```

- Só existe **um frame em trânsito** por vez, então o TCP nunca acumula fila.
  Fila é o que faz a latência crescer em streams de vídeo comuns.
- O frameskip é automático. Se o Wi-Fi ou o decode ficarem lentos, o PSP pede
  menos vezes e recebe sempre o frame mais atual.
- Pedir antes de decodificar ("prefetch") sobrepõe rede e decode. Nesse modo,
  quem limita o FPS é o mais lento dos dois, não a soma.

## PSP -> PC: pedido (28 bytes)

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSC1"` |
| 4 | u32 | buttons | máscara `PSP_CTRL_*` (Marco 4) |
| 8 | u8 | lx | analógico X, 0..255 (128 = centro) |
| 9 | u8 | ly | analógico Y |
| 10 | u16 | flags | `0x1` FRAME = quero o próximo frame; `0x2` HELLO = primeira mensagem |
| 12 | u32 | ack_frame | último frame **exibido** (0 = nenhum ainda) |
| 16 | u32 | echo_ts | `send_ts` desse frame, devolvido como veio |
| 20 | u16 | net_t | 0,1 ms: pedido enviado -> frame recebido por inteiro |
| 22 | u16 | local_t | 0,1 ms: frame recebido -> exibido (espera + decode + flip) |
| 24 | u16 | since_t | 0,1 ms: frame exibido -> envio desta mensagem |
| 26 | u16 | decode_t | 0,1 ms: só o decode |

Uma mensagem sem a flag FRAME só atualiza controles e estatísticas. No Marco 4
ela permite mandar botões com mais frequência que os frames.

## PC -> PSP: frame (16 bytes + JPEG)

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSF1"` |
| 4 | u32 | frame_no | começa em 1 e cresce a cada envio na conexão |
| 8 | u32 | size | bytes de JPEG que seguem (máx. 256 KB) |
| 12 | u32 | send_ts | relógio monotônico do servidor em ms (32 bits, dá a volta) |

O payload é um JPEG **baseline 4:2:0**, de no máximo 480x272. Se for menor, o
PSP centraliza. 4:2:0 é exigência do decoder de hardware (`sceJpeg`); o
decoder em software aceita qualquer amostragem.

## Medição de latência sem sincronizar relógios

O servidor calcula, só com o relógio dele:

```
caminho = (agora - echo_ts) - since_t     -> envio -> exibido no PSP (+ subida do ack)
latência = idade + caminho                -> frame pronto no PC -> exibido no PSP
```

`idade` é quanto tempo o frame ficou pronto no servidor antes de ser enviado.
`net_t`, `local_t` e `decode_t` servem para decompor esse total. A latência
real "vidro a vidro" soma ainda o tempo de captura no PC (compositor +
GStreamer, alguns ms) e o scanout do LCD do PSP. Isso só se mede filmando as
duas telas (ver README).

## Conexão

- Um PSP por vez. Uma nova conexão derruba a anterior (o PSP pode ter
  reiniciado o app e deixado um socket meio aberto).
- Se nenhum frame novo surgir em 1 s (tela parada no Wayland), o servidor
  reenvia o último para a conexão continuar viva.
- Se o PSP passar 10 s sem enviar nada, o servidor encerra a sessão e volta a
  esperar conexões.
