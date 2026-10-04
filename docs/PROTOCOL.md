# Protocolo PSPStream v2

TCP **ou** UDP, porta padrão **5123** (o servidor atende os dois ao mesmo
tempo; o PSP escolhe com `transport=` no `server.txt`). Todos os inteiros são **little-endian** (PSP e PC
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

## PSP -> PC: pedido (36 bytes)

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSC2"` (v2; um EBOOT antigo, `"PSC1"`, é recusado com aviso no log) |
| 4 | u32 | buttons | máscara `PSP_CTRL_*` (Marco 4) |
| 8 | u8 | lx | analógico X, 0..255 (128 = centro) |
| 9 | u8 | ly | analógico Y |
| 10 | u16 | flags | `0x1` FRAME = quero o próximo frame; `0x2` HELLO = primeira mensagem; `0x4` NACK e `0x8` BYE (só UDP, abaixo) |
| 12 | u32 | ack_frame | último frame **exibido** (0 = nenhum ainda) |
| 16 | u32 | echo_ts | `send_ts` desse frame, devolvido como veio |
| 20 | u16 | net_t | 0,1 ms: pedido enviado -> frame recebido por inteiro |
| 22 | u16 | local_t | 0,1 ms: frame recebido -> exibido (espera + decode + flip) |
| 24 | u16 | since_t | 0,1 ms: frame exibido -> envio desta mensagem |
| 26 | u16 | decode_t | 0,1 ms: só o decode |
| 28 | u16 | first_t | 0,1 ms: pedido -> primeiro pedaço/byte do frame (ida e volta + reação do servidor) |
| 30 | u16 | burst_t | 0,1 ms: primeiro -> último pedaço (dá a vazão real do enlace) |
| 32 | u8 | signal | sinal do Wi-Fi do PSP, % |
| 33 | u8 | wflags | `0x1` = "Economia de energia WLAN" ligada no XMB |
| 34 | u16 | lost | UDP: frames abandonados incompletos desde o início do stream |

`first_t` e `burst_t` separam o tempo de rede em ida e volta (fixo por frame)
e transferência (proporcional ao tamanho). Cada parte tem um remédio
diferente.

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

## UDP

O modelo é o mesmo (pull, um frame em trânsito). Muda o enquadramento:

**PC -> PSP:** cada frame vai em pedaços de até 1400 bytes de JPEG, um por
datagrama (20 + 1400 + 28 de IP/UDP = 1448 bytes, cabe nos 1500 do Wi-Fi):

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSU1"` |
| 4 | u32 | frame_no | |
| 8 | u32 | size | tamanho total do JPEG |
| 12 | u32 | send_ts | como no TCP |
| 16 | u16 | chunk | índice deste pedaço (bytes `chunk*1400 ...`) |
| 18 | u16 | count | total de pedaços (`ceil(size/1400)`, máx. 256) |

**PSP -> PC:** o mesmo pedido de 36 bytes, um por datagrama. Com a flag
`NACK` (0x4), vem logo depois:

| offset | tipo | campo | descrição |
|---|---|---|---|
| 36 | u32 | frame_no | frame incompleto |
| 40 | u32[8] | missing | bit `i` = pedaço `i` faltando |

O servidor reenvia só esses pedaços. Ele guarda os últimos 4 frames enviados.
`BYE` (0x8) avisa que o app do PSP está saindo: o servidor solta as teclas e
encerra a sessão na hora (no UDP não existe "fechar conexão").

**Tempos no PSP** (`psp/src/stream.c`):

| situação | ação |
|---|---|
| chegou o último pedaço e faltam outros | NACK na hora (os pedaços vêm em ordem: os que faltam se perderam) |
| frame incompleto sem pedaço novo por média + 4 desvios do intervalo entre pedaços (20-50 ms) | NACK com os que faltam (o fim do frame se perdeu) |
| depois de um NACK | espera a resposta por média + 4 desvios da ida e volta (pedido -> 1º pedaço, 30-200 ms); cada pedaço reenviado que chega adia a espera |
| chegou o último pedaço do reenvio e ainda faltam outros | NACK de novo na hora |
| 3 NACKs sem completar | desiste do frame (conta em "perdidos") e pede outro |
| pedido sem nenhuma resposta por 200 ms | reenvia o pedido |
| 3 s sem completar nenhum frame | o pedido vai com HELLO (o servidor pode ter reiniciado) |
| pedaço de frame mais antigo ou duplicado | ignorado |

**Pedido antecipado** (`early_kb`, experimental, padrão 0 = desligado): quando
faltam `early_kb` KB do frame atual, o PSP já pede o próximo. No PSP-3000
medido, não aumentou o FPS e piorou a latência (ver MEASUREMENTS.md). Ele chega logo atrás do atual, e o
rádio não fica parado durante a ida e volta do pedido (~21 ms medidos). Por
isso podem existir **dois frames em remontagem**. Quando um mais novo
completa, o mais velho incompleto é abandonado: mostrar o N depois do N+1 não
serve para nada, e esperar o NACK atrasaria o N+1. Na prática, uma perda no fim
de um frame vira um pulo de frame em vez de uma travada. A "rede" reportada
conta a partir de quando o rádio ficou livre para aquele frame
(`max(pedido, frame anterior completo)`).

Do lado do servidor, há no máximo **um pedido pendente**: pedidos repetidos
enquanto ele espera um frame novo não viram uma rajada de frames. Uma sessão
UDP começa com um HELLO (ou com qualquer pedido, se não houver sessão ativa)
e é identificada pelo IP:porta do PSP.

**Controles no UDP:** cada mudança é mandada duas vezes (na amostra seguinte
de novo). Enquanto algo está segurado, o estado é reafirmado a cada ~100 ms,
em TCP e UDP.

## Conexão

- Um PSP por vez. Uma nova conexão (TCP, ou HELLO por UDP) derruba a anterior
  (o PSP pode ter reiniciado o app e deixado uma sessão meio aberta).
- Se nenhum frame novo surgir em 1 s (tela parada no Wayland), o servidor
  reenvia o último para a conexão continuar viva. Esse reenvio fica fora da
  média de latência (a imagem não mudou) e aparece como "reenvios".
- Se o PSP passar 10 s sem enviar nada, o servidor encerra a sessão e volta a
  esperar conexões.
- **Tecla presa:** se o PSP ficar 500 ms sem mandar nada enquanto há tecla ou
  analógico segurado (`--input-timeout`), o servidor solta tudo. Como o PSP
  reafirma o estado a cada ~100 ms, isso só acontece se a rede travar.
