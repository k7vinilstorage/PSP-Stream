# Protocolo PSPStream v5

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
  quem limita o FPS é o mais lento dos dois, não a soma. Com `prefetch=auto`
  (padrão), o PSP pede antes do fim do frame que chega no JPEG e no H.264 só
  com quadros completos; com frames P, pede o próximo quando o decode pega o
  atual (~60 fps lisos no PSP-3000). `prefetch=0` pede depois de exibir.

## PSP -> PC: pedido (52 bytes)

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSC5"` (v5; um EBOOT antigo, `"PSC1"` a `"PSC4"`, é recusado com aviso no log) |
| 4 | u32 | buttons | máscara `PSP_CTRL_*` (Marco 4) |
| 8 | u8 | lx | analógico X, 0..255 (128 = centro) |
| 9 | u8 | ly | analógico Y |
| 10 | u16 | flags | `0x1` FRAME = quero o próximo frame; `0x2` HELLO = primeira mensagem; `0x4` NACK, `0x8` BYE e `0x10` PING (só UDP, abaixo); `0x20` IDR = frames P sem referência, mande um IDR (abaixo) |
| 12 | u32 | ack_frame | último frame **exibido** (0 = nenhum ainda) |
| 16 | u32 | echo_ts | `send_ts` desse frame, devolvido como veio |
| 20 | u16 | net_t | 0,1 ms: pedido enviado -> frame recebido por inteiro |
| 22 | u16 | local_t | 0,1 ms: frame recebido -> exibido (espera + decode + flip) |
| 24 | u16 | since_t | 0,1 ms: frame exibido -> envio desta mensagem |
| 26 | u16 | decode_t | 0,1 ms: só o decode |
| 28 | u16 | first_t | 0,1 ms: pedido -> primeiro pedaço/byte do frame (ida e volta + reação do servidor) |
| 30 | u16 | burst_t | 0,1 ms: primeiro -> último pedaço (dá a vazão real do enlace) |
| 32 | u8 | signal | sinal do Wi-Fi do PSP, % |
| 33 | u8 | wflags | `0x1` = "Economia de energia WLAN" ligada no XMB; `0x2` = esperando pacotes por consulta, não `select()`; `0x4` = decodifica H.264; `0x8` = aceita frames P (v0.9); `0x10` = toca o som (v1.1, só UDP; abaixo) |
| 34 | u16 | lost | UDP: frames abandonados incompletos desde o início do stream |
| 36 | u32 | hdr_have | UDP: id do cabeçalho JPEG guardado no PSP (0 = nenhum) |
| 40 | u16 | ping_select | 0,1 ms: ida e volta pura medida no início, esperando com `select()` |
| 42 | u16 | ping_poll | 0,1 ms: o mesmo, consultando o socket a cada 0,5 ms (0 = não medido) |
| 44 | u16 | ping_live | 0,1 ms: ping a cada 1 s **durante** o stream, média móvel (0 = ainda não) |
| 46 | u16 | ping_live_min | 0,1 ms: o menor dos últimos 8 |
| 48 | i16 | idle_t | 0,1 ms: último pedaço do frame anterior -> 1º pedaço deste ("tempo morto"; negativo = chegou antes de o anterior completar, em fila). `-32768` = não medido (TCP) |
| 50 | u16 | early_b | UDP: bytes que faltavam no frame atual quando o PSP pediu o próximo (0 = só no fim) |

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

O payload é um JPEG **baseline 4:2:0**, de no máximo 480x272, ou, com
`--codec h264`, um access unit H.264 Annex B (SPS + PPS + IDR, 480x272). O
PSP distingue pelos primeiros bytes: `FF D8` é JPEG, `00 00 00 01` ou
`00 00 01` é H.264. Se for menor, o
PSP centraliza. 4:2:0 é exigência do decoder de hardware (`sceJpeg`); o
decoder em software aceita qualquer amostragem.

### Frames P (`--codec h264p`, PSP com `wflags & 0x8`)

O payload é **AUD + frame + AUD + cópia + AUD + cópia** (AUD =
`00 00 00 01 09 F0`). O frame é IDR (com SPS + PPS) ou P; as cópias são P
sem mudança (~20 bytes). O decoder do PSP só solta o frame N depois de
receber o N+2, e as duas cópias empurram o frame real para a saída: o PSP
decodifica os 3 AUs, sem `sceMpegAvcDecodeStop` (que zera as referências), e
mostra o que sai da última chamada. O PSP reconhece o pacote pelo AUD no
início e o tipo pela NAL seguinte (7 ou 5 = IDR, senão P).

Regras, porque cada P depende do anterior:

- O servidor só codifica o frame que vai mandar (nunca pula um frame já
  codificado), e numera em sequência.
- O PSP decodifica todos, em ordem: prontos ficam numa fila, e um frame
  completo que chega antes de um mais velho incompleto espera o reenvio dele.
  O próximo é pedido quando o decode pega o último da fila; no UDP, com
  `prefetch=auto`, esse pedido autoriza até 2 frames à frente (a janela, em
  [UDP](#udp)); com `prefetch=1`, também antes do fim do frame que chega;
  com `prefetch=0`, depois de exibir o atual. O pedido feito pela
  thread de decode é contado antes de o "pedido adiado" ser solto: na ordem
  inversa, a thread de rede via "ninguém pediu" e pedia o mesmo frame de
  novo, o pedido fantasma travava o seguinte até o RTO (o engasgo do
  prefetch com frames P até a v1.0).
- Frame abandonado depois de 3 NACKs, buraco na numeração que o reenvio não
  cobriu, ou erro de decode: os P seguintes ficam sem referência. O PSP pula esses P e manda `IDR`
  (0x20) em todo pedido até decodificar um IDR. O servidor ignora pedidos de
  IDR por 150 ms depois de mandar um (é o que ainda está a caminho).
- Um EBOOT sem `0x8` recebe todo frame IDR (como `--codec h264`).

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

**PC -> PSP:** cada frame vai em pedaços de até 1400 bytes, um por
datagrama (24 + 1400 + 28 de IP/UDP = 1452 bytes, cabe nos 1500 do Wi-Fi):

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `"PSU2"` |
| 4 | u32 | frame_no | |
| 8 | u32 | size | tamanho total do payload |
| 12 | u32 | send_ts | como no TCP |
| 16 | u16 | chunk | índice deste pedaço (bytes `chunk*1400 ...` do payload) |
| 18 | u16 | count | total de pedaços (`ceil(size/1400)`, máx. 256) |
| 20 | u32 | hdr | bits 0-30: id do cabeçalho deste JPEG (CRC32); bit 31: o payload veio **sem** ele |

**Cabeçalho JPEG uma vez só.** O "cabeçalho" vai do SOI até o fim do
segmento SOS (tabelas de quantização e Huffman, ~620 bytes no `jpegenc`). Ele
é igual em todo frame da mesma qualidade e tamanho. O PSP guarda os dois
últimos que recebeu inteiros e informa o mais novo em `hdr_have`. Se o
cabeçalho do frame tem esse id, o servidor manda só o resto (bit 31) e o PSP
copia o cabeçalho de volta no início do buffer. Quando a qualidade muda, o
primeiro frame vai inteiro. Como o id é um CRC do conteúdo, um cabeçalho
guardado vale mesmo depois de reiniciar o servidor ou o PSP.

**Ping.** Antes do HELLO, o PSP manda 16 pedidos com a flag `PING` (0x10) e
`echo_ts` = um token. O servidor responde na hora, sem passar pela sessão,
com 8 bytes: `"PSO1"` + o token. Metade dos pings espera a resposta com
`select()` e metade consultando o socket a cada 0,5 ms. As medianas vão em
`ping_select`/`ping_poll` de todo pedido. Durante o stream, o PSP manda um
ping por segundo (token com o bit 31 ligado = horário de envio) e reporta a
média e o mínimo em `ping_live`/`ping_live_min`. Comparar com o "1º pedaço"
separa o tempo do rádio sob o tráfego do stream do tempo das respostas com
frame. Com `rxwait=auto`, o PSP passa a
esperar por consulta se isso for mais de 1 ms mais rápido.

**PSP -> PC:** o mesmo pedido de 52 bytes, um por datagrama. Com a flag
`NACK` (0x4), vem logo depois:

| offset | tipo | campo | descrição |
|---|---|---|---|
| 52 | u32 | frame_no | frame incompleto |
| 56 | u32[8] | missing | bit `i` = pedaço `i` faltando |

O servidor reenvia só esses pedaços. Ele guarda os últimos 4 frames enviados.

**FRAME + NACK** (frames P): pedido de um frame com o número dele (todos os
pedaços marcados). Um frame P pequeno cabe num pacote; se ele some, o PSP nem
sabe que o frame existiu, e um frame novo chegaria sem a referência. Se o
servidor já mandou esse frame (há mais de 15 ms), reenvia o mesmo e não
manda outro; se mandou há menos de 15 ms, ele ainda está a caminho e o
pedido é ignorado; se ainda não mandou, vale como pedido normal (e se já há
um pedido esperando frame novo, os dois viram um só).

Desde a v1.0, com frames P no UDP, **todo pedido de frame novo** vai assim,
com o número seguinte ao maior frame já visto, e vai **de novo 6 ms depois**
se nenhum pedaço desse frame chegou. Pelas regras acima, a cópia nunca vira
um frame a mais: ou ela se junta ao pedido que está esperando, ou chega com o
frame já no ar. Mas se o original se perdeu na subida, a cópia é o pedido, e
o frame sai 6 ms depois em vez de esperar o RTO (>= 30 ms) com o stream
parado (um P não pode ser pulado). O pedido repetido por falta de resposta
continua igual, com o NACK do último completo + 1.

**Janela de 2 frames** (v1.1, frames P com `prefetch=auto` no UDP): o
número no FRAME + NACK passa a valer como "pode mandar **até** este frame".
Quando a thread de decode pega o frame N, o PSP manda FRAME + NACK(N+2). O
servidor guarda o crédito (`want_upto`, no máximo o último enviado + 2) e,
enquanto `frame_no < want_upto`, manda cada frame novo na hora em que ele é
capturado. Um pedido simples (sem NACK) continua valendo "o seguinte"; cópias
e pedidos velhos não somam (o crédito só sobe). Com só o N+1 autorizado, o
pedido saía quando o N chegava e tinha de chegar ao PC, e o frame ser
codificado, antes da captura seguinte (16,7 ms a 60 fps); com o Wi-Fi
oscilando, o servidor perdia capturas (52-55 fps no PSP-3000 com a fonte a
60). Nesse modo o pedido **não** vai de novo depois de 6 ms: o frame sai na
captura, não na hora do pedido, e o pedido seguinte cobre um perdido.

**Frame perdido inteiro com a janela:** o N+1 pode sumir e o N+2 chegar antes
de qualquer pedido repetido. O servidor numera os frames em sequência e manda
em ordem, então o PSP sabe: chegou o `done + 2` sem nenhum pedaço do
`done + 1`. Ele manda na hora um NACK com todos os pedaços do `done + 1`
(tamanho ainda desconhecido; o servidor reenvia os que existem), e o `done +
2` completo espera por ele, como espera um mais velho incompleto. Se o
reenvio não vier depois de 3 NACKs, pede IDR.

**Último pedaço em dobro** (frames P, servidor v1.0): o último pedaço de cada
pacote P vai de novo 6 ms depois (`--p-redundancy-ms`; o PSP ignora o que já
tem). Perder o último pedaço era o caso lento: sem pedaço seguinte, o PSP só
nota pelo silêncio (20-50 ms) e o NACK leva mais uma ida e volta. Um frame P
pequeno é um pedaço só, então a cópia cobre também o frame perdido inteiro.
Custa 1 pacote por frame: ~40 KB/s a 60 fps num clipe de jogo (+27-53% sobre os
frames P), ainda bem abaixo dos 350-450 KB/s do H.264 só com quadros completos.
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
| hora do NACK, mas um frame mais novo já está chegando | desiste do frame sem NACK: o reenvio viria na fila atrás do mais novo (frames P: manda o NACK, o mais novo depende dele) |
| frames P: pedido de frame novo sem nenhum pedaço dele em 6 ms | manda o mesmo pedido de novo, uma vez (ver acima; com a janela, não) |
| frames P: chega o `done + 2` sem nada do `done + 1` | NACK do `done + 1` inteiro na hora; o `done + 2` espera (ver acima) |
| pedido sem nenhuma resposta por uma ida e volta medida (média + 4 desvios do pedido -> 1º pedaço, 30-200 ms) | reenvia o pedido (frames P: com NACK do frame esperado, ver acima) |
| frames P: frame pronto esperando o decode | o próximo é pedido pela thread de decode quando ela pega esse frame (com a janela, até 2 à frente); até lá não há pedido para repetir |
| 3 s sem completar nenhum frame | o pedido vai com HELLO (o servidor pode ter reiniciado) |
| pedaço de frame mais antigo ou duplicado | ignorado |

**Pedido antecipado** (`early_kb`, padrão `auto`): quando faltam `early_kb`
KB do frame atual, o PSP já pede o próximo. Com `auto`, o limite é a ida e
volta vezes a vazão: o ping mediano do início dividido pelo intervalo médio
entre pedaços, vezes 1400 bytes (~2-3 KB no PSP-3000, teto de 8 KB). Assim o
1º pedaço do próximo chega logo depois do último do atual, sem rádio parado
e sem fila. Valores fixos de 6-14 KB, testados no PSP-3000 com JPEG, pediam
cedo demais: o frame seguinte esperava inteiro na fila do roteador e a
latência subia. Um frame com buraco não antecipa, para o próximo não ficar na
frente do reenvio. `early_kb=0` pede só no fim, como até a v0.7. Com pedido
antecipado podem existir **dois frames em remontagem**. Quando um mais novo
completa, o mais velho incompleto é abandonado: mostrar o N depois do N+1 não
serve para nada, e esperar o NACK atrasaria o N+1. Na prática, uma perda no fim
de um frame vira um pulo de frame em vez de uma travada. A "rede" reportada
conta a partir de quando o rádio ficou livre para aquele frame
(`max(pedido, frame anterior completo)`).

Do lado do servidor, há no máximo **um pedido pendente**: pedidos repetidos
enquanto ele espera um frame novo não viram uma rajada de frames. A exceção
é a janela dos frames P (acima): um crédito de até 2 frames, dado pelo
número no pedido. Uma sessão
UDP começa com um HELLO (ou com qualquer pedido, se não houver sessão ativa)
e é identificada pelo IP:porta do PSP.

**Controles no UDP:** cada mudança é mandada duas vezes (na amostra seguinte
de novo). Enquanto algo está segurado, o estado é reafirmado a cada ~100 ms,
em TCP e UDP.

## Som (UDP, v1.1)

O som não segue o modelo pull: enquanto o pedido mais recente do PSP tiver
`wflags & 0x10`, o servidor **empurra** um pacote a cada ~20 ms para o
endereço da sessão UDP. Sem o bit (`audio=0` no `server.txt`, ou desligado
com SELECT + START + cima), nenhum pacote de som sai, e um EBOOT antigo
nunca recebe som.

| offset | tipo | campo | descrição |
|---|---|---|---|
| 0 | char[4] | magic | `PSA1` |
| 4 | u32 | seq | +1 por pacote; um buraco é um pacote perdido |
| 8 | u32 | pos | amostra (por canal) do início do bloco |
| 12 | u16 | rate | Hz: 22050, 32000, 44100 (padrão, a do PSP) ou 48000 |
| 14 | u8 | channels | 1 ou 2 |
| 15 | u8 | codec | `1` = IMA ADPCM, bloco do WAV (o `adpcmenc` do GStreamer, layout dvi) |
| 16 | u16 | samples | amostras por canal no bloco (881 a 44,1 kHz: 1 + 8 x 110) |
| 18 | u16 | reservado | 0 |
| 20 | | bloco | por canal, 4 bytes (1ª amostra int16, índice do passo, 0); depois grupos de 4 bytes (8 amostras) alternando os canais, nibble baixo primeiro |

Cada bloco decodifica sozinho. O PSP decodifica num anel e toca pelo
`sceAudioSRC` (canal com conversão de taxa), em pedaços de 256 amostras,
alternando dois buffers (o hardware lê o pedaço enquanto toca). O
anel começa a tocar com 40 ms e se ajusta: +10 ms a cada vez que esvazia,
-5 ms a cada 10 s sem faltar, entre 30 e 120 ms. Pacote perdido (até 5
seguidos) vira silêncio do mesmo tamanho; um buraco maior, ou o `seq`
voltando (servidor reiniciado), recomeça o anel. Som acima de alvo + 40 ms é
descartado até o alvo, para o atraso não crescer (rajadas depois de um
atraso, ou o relógio do PC um pouco mais rápido que o do PSP).

## Conexão

- Um PSP por vez. Uma nova conexão (TCP, ou HELLO por UDP) derruba a anterior
  (o PSP pode ter reiniciado o app e deixado uma sessão meio aberta).
- Se nenhum frame novo surgir em 1 s (tela parada no Wayland), o servidor
  reenvia o último para a conexão continuar viva (com frames P, um P sem
  mudança, ~100 bytes). Esse reenvio fica fora da média de latência (a
  imagem não mudou) e aparece como "reenvios".
- **Procurar o PC na rede** (tela de configuração do PSP): o PSP manda o
  pedido de 52 bytes com `PING` (0x10) para 255.255.255.255 e para o
  broadcast da sub-rede, na porta configurada, a cada 200 ms por até 2 s. O
  servidor responde ping de qualquer endereço, sem sessão, e o IP de origem
  do `ps_pong_t` vira o IP do PC. O `echo_ts` do ping de busca tem o bit 30
  ligado, para não ser confundido com os pings do stream.
- Se o PSP passar 10 s sem enviar nada, o servidor encerra a sessão e volta a
  esperar conexões.
- **Tecla presa:** se o PSP ficar 500 ms sem mandar nada enquanto há tecla ou
  analógico segurado (`--input-timeout`), o servidor solta tudo. Como o PSP
  reafirma o estado a cada ~100 ms, isso só acontece se a rede travar.
