# Medições (Marco 3)

Cada número aqui tem uma origem, e elas não se misturam:

| marca | origem | vale para o PSP? |
|---|---|---|
| **[PC]** | medido de verdade no PC de desenvolvimento (CPU x86, 4 núcleos) | sim, para a parte do PC (tamanho de frame, custo de captura/encode) |
| **[SIM]** | `tools/fake_client.py` com Wi-Fi e decode simulados | não: é um modelo do pipeline |
| **[EMU]** | PPSSPPHeadless | não: o tempo emulado não é o tempo do hardware |
| **[PSP]** | PSP-3000 real (ARK-4) + Fedora 44, medido pelo usuário | sim |

## 0. Números reais do PSP [PSP]

### Decode (`bench=1`, frame 480x272)

| decoder | q20 | q70 | q90 |
|---|---|---|---|
| hw (sceJpeg) | ~7,9 ms | 7,91 ms | ~7,9 ms |
| sw (libjpeg-turbo) | ~34 ms | 34 ms | ~34 ms |

O hardware é ~4,3x mais rápido, e o tempo quase não depende da qualidade.
Com o hw, o decode limitaria o stream só acima de ~120 fps, então o gargalo
fica todo na rede. O `decoder=auto` já usa o hw.

### Primeiro `--bench` (TCP, `--source portal`, tela real)

| q | KB/frame | FPS | Wi-Fi (KB/s) | latência média (ms) | p95 (ms) | rede (ms) | decode (ms) | PSP recebido->exibido (ms) |
|---|---|---|---|---|---|---|---|---|
| 30 | 11.1 | 3.5 | 235 | 129.9 | 775.4 | 83.2 | 7.6 | 18.5 |
| 50 | 15.2 | 6.7 | 315 | 90.7 | 106.0 | 58.5 | 7.6 | 9.4 |
| 70 | 20.7 | 6.2 | 356 | 110.5 | 163.7 | 81.3 | 7.7 | 8.9 |
| 90 | 36.4 | 3.0 | 316 | 214.2 | 960.1 | 173.4 | 8.7 | 10.0 |

Leitura:

- **Vazão real do Wi-Fi: ~235-356 KB/s** (mediana por frame). Ficou dentro da
  faixa esperada para 802.11b com TCP.
- **O FPS (3-7) não é limite da rede.** Em q50, 58 ms de rede + 8 ms de decode
  dariam ~15 fps. Os ~80 ms que faltam por frame são o servidor **esperando
  frame novo do portal**: o GNOME só manda frame quando a tela muda. Essa
  versão da tabela ainda não mostrava essa espera; as colunas "fonte (fps)" e
  "espera por frame novo" foram adicionadas depois deste teste.
- **O p95 de 775/960 ms é artefato**: com a tela parada, o servidor reenvia o
  último frame a cada 1 s, e esse reenvio entrava na média com "idade" de
  ~1 s. Agora ele fica de fora e é contado em "reenvios 1 s".
- A rede é a maior parte da latência que sobra (58-81 ms em q50-q70). Por
  isso vale comparar com o transporte UDP.

### TCP x UDP (`--bench`, `--source static`, mesma imagem)

| q | KB/frame | UDP: FPS | UDP: latência / p95 | UDP: rede | UDP: pedaços reenviados | TCP: FPS | TCP: latência / p95 | TCP: rede |
|---|---|---|---|---|---|---|---|---|
| 30 | 7.4 | 26.9 | 34 / 55 ms | 36 ms | 0.9% | 0.5* | 1314 / 6445 ms* | 53 ms |
| 50 | 9.9 | 21.8 | 42 / 63 ms | 50 ms | 1.3% | 8.5 | 203 / 805 ms | 169 ms |
| 70 | 13.1 | 19.6 | 47 / 72 ms | 51 ms | 2.1% | 14.1 | 67 / 66 ms | 59 ms |
| 90 | 24.2 | 13.9 | 74 / 151 ms | 72 ms | 2.7% | 3.0 | 265 / 1038 ms | 257 ms |

\* A fase q30 do TCP inclui uma pausa de ~6 s do benchmark de decode
(`bench=1` ficou ligado). Depois disso, o frame do benchmark deixou de entrar
nas estatísticas.

- **O Wi-Fi perde 1-3% dos pacotes** (coluna "pedaços reenviados"). Com um
  frame em trânsito, cada perda no TCP vira um timeout de retransmissão, com
  travadas de centenas de ms a segundos. O UDP recupera com NACK em
  milissegundos. **O UDP virou o padrão.**
- No uso real com TCP (portal, jogo rodando), o servidor registrou três vezes
  "PSP sem mandar nada há ~505 ms, soltando tudo". Era a pilha TCP do PSP
  parada, com os controles presos atrás. Essa era a causa da tecla presa.
- A captura do jogo pelo portal entrega **~37 fps** ("fonte"). O limite
  estava no transporte, não na captura.
- Ajustando uma reta na coluna "rede" do UDP (7,4 KB -> 36 ms, 24,2 KB ->
  72 ms): **enlace de ~470 KB/s + ~21 ms fixos por frame** (ida e volta do
  pedido). Esses 21 ms com o rádio parado motivaram o pedido antecipado.

### Pedido antecipado (`early_kb`): a simulação errou, o PSP decidiu [PSP]

A hipótese: os ~21 ms fixos por frame (da reta acima) seriam rádio parado
esperando o pedido ir e voltar. Pedir o próximo frame antes de o atual
terminar esconderia esse tempo. A simulação (`fake_client --rtt-ms 21
--kbps 470`) previa 17,8 -> 28,9 fps com a mesma latência.

No PSP-3000 (UDP, `--source static`, mesma imagem; ordem das rodadas: 10, 14, 0, 6):

| early_kb | q30: fps / lat / p95 | q50: fps / lat / p95 | q70: fps / lat / p95 | q90: fps / lat / p95 |
|---|---|---|---|---|
| **0** | 20.3 / **38** / **89** | **19.7** / **46** / **75** | 16.6 / **51** / **80** | 10.9 / 90 / 162 |
| 6 | 20.7 / 48 / 105 | 16.5 / 61 / 143 | 15.8 / 67 / 115 | 10.5 / 89 / 160 |
| 10 | 22.4 / 49 / 100 | 16.0 / 64 / 126 | 15.1 / 73 / 134 | 11.4 / 105 / 175 |
| 14 | 14.9 / 65 / 137 | 14.0 / 76 / 155 | 17.5 / 76 / 126 | 12.2 / 114 / 188 |

**O FPS não subiu e a latência piorou 10-30 ms (p95 quase dobrou).** O
modelo da simulação estava errado: aqueles ~21 ms não são rádio parado. O
802.11b é half duplex. O pedido antecipado disputa o ar com o frame que
ainda está chegando, e o frame seguinte só fica esperando na fila do
roteador (é a latência extra). Os 21 ms são custo de ar por frame (disputa
do meio, ACKs), não espera. **`early_kb=0` voltou a ser o padrão.**

Outras observações destas rodadas:

- **Variação entre rodadas:** a mesma configuração (sem pedido antecipado)
  deu q30 = 26,9 fps no primeiro dia e 20,3 fps agora. O Wi-Fi varia uns 20%.
  Só diferenças maiores que isso contam, como o p95, que dobrou de forma
  consistente.
- **Perda cresce com o tamanho do frame:** ~1% dos pedaços em q30 (6
  pedaços por frame) e 4-8% em q90 (18 pedaços). Isso aponta para rajadas
  estourando algum buffer (fila do roteador ou do PSP). Teste possível:
  `--udp-pace 450` espaça os pedaços na velocidade do enlace.
- **Alvo da qualidade adaptativa:** até q30 leva ~46 ms de rede, então o
  antigo `--target-fps 30` era inalcançável e jogava a qualidade para o
  mínimo. O padrão agora é **20 fps**, que leva a ~q55: ~20 fps e ~48 ms.

### Plano de otimização: onde está o tempo e o que cada ideia rende

Modelo tirado das medições no PSP (UDP): **cada frame custa ~21-25 ms fixos
+ tamanho / (360-470 KB/s)**. Na q50 (~10 KB), metade do tempo é a parte
fixa. Isso decide o que vale a pena:

| ideia | ataca | medido aqui [PC] | ganho estimado na q50 |
|---|---|---|---|
| enviar o cabeçalho JPEG (623 bytes) só uma vez (**feito, v0.4**) | parte proporcional | 6,0% do frame em q30, 4,5% em q50, 3,3% em q70 (jogos + desktop) | ~3-5% de FPS |
| tabelas Huffman otimizadas | parte proporcional | 4-6% do frame | ~2-3% de FPS |
| 400x228 + ampliação no PSP (sceGu) | parte proporcional | 67% dos bytes | ~+20% de FPS, imagem mais macia |
| 360x204 + ampliação | parte proporcional | 59% dos bytes | ~+25% de FPS |
| redução multithread no PC (feito) | captura no PC | 4,0 -> 2,5 ms/frame em 2240x1400 | -1,5 ms de latência |
| NACK rápido + espera de uma ida e volta (feito) | perdas | piso de 6 ms **piorou no PSP**; corrigido (abaixo) | p95 menor com perda |
| pedido antecipado | parte fixa | **piorou no PSP** | desligado |
| **descobrir os ~25 ms fixos** | parte fixa | `first_t` (v2) + ping no início (v0.4) | até ~2x de FPS se for algo corrigível |
| reação do servidor (pedido -> 1º pedaço enviado) | parte fixa | **0,6 ms** (mediana; p95 1,2-2,4 ms) em localhost, fonte estática e ao vivo | nada a ganhar: não é o servidor |
| esperar pacotes consultando o socket em vez de `select()` (`rxwait=auto`, v0.4) | parte fixa | o PSP mede os dois ao conectar | depende de quanto o `select()` do PSP demora para acordar |
| DSCP EF / fila de voz do WMM (`--dscp`, v0.4) | parte fixa (fila na placa do PC e no roteador) | não medido | provavelmente pequeno numa rede doméstica vazia |
| H.264 no Media Engine do PSP em vez de MJPEG | parte proporcional | não medido (ver abaixo) | 2-3x menos bytes na mesma qualidade |

Por que a parte fixa vem primeiro: num Wi-Fi normal, a ida e volta leva 2-5
ms, não 25. Suspeitos, cada um com um remédio:

1. **Economia de energia do Wi-Fi do PSP** (o roteador segura os pacotes até
   o PSP acordar). O servidor agora loga o estado que o PSP reporta.
2. **PC no Wi-Fi**, sobretudo com o power save da placa ligado (padrão do
   NetworkManager em muitos notebooks). O servidor agora avisa na partida.
   O usuário descartou esse suspeito: a mesma rede segura streaming pesado
   (Moonlight) entre PCs sem problema.
3. **Pilha de rede do PSP** entregando com atraso: aparece como `first_t`
   alto mesmo com os dois acima descartados.
4. Enlace a 5,5/2 Mbps (sinal ruim, interferência): aparece como "vazão na
   rajada" baixa (< 300 KB/s) e não como `first_t` alto.

O próximo `--bench` traz as colunas "1º pedaço" e "vazão na rajada". Com
elas se sabe qual caso é antes de mexer em mais código.

### Primeiro `--bench` com `first_t` (protocolo v2): uma regressão minha [PSP]

UDP, `--source static`, mesma imagem. Na partida, o servidor avisou: **PC no
Wi-Fi (`wlp0s20f3`) com power save LIGADO**. O PSP informou sinal de 100% e
economia de energia WLAN desligada.

| q | KB/frame | FPS | latência / p95 (ms) | rede (ms) | 1º pedaço (ms) | rajada (ms) | vazão na rajada (KB/s) | pedaços reenviados | frames perdidos |
|---|---|---|---|---|---|---|---|---|---|
| 30 | 7.4 | 19.4 | 41.8 / 99.2 | 52.1 | 36.6 | 15.7 | 448 | 19.3% | 2 |
| 50 | 9.9 | 13.5 | 60.3 / 127.4 | 75.6 | 51.0 | 24.8 | 394 | 42.9% | 12 |
| 70 | 13.1 | 10.1 | 89.4 / 184.5 | 95.4 | 57.5 | 38.0 | 356 | 56.5% | 14 |
| 90 | 24.2 | 7.5 | 170.7 / 362.1 | 131.1 | 66.8 | 64.4 | 364 | 66.1% | 13 |

Na rodada anterior, com o mesmo `early_kb=0`, eram q50 = 19,7 fps e 1-8%
reenviados. **A culpa é da versão 8ab5795**, que trouxe o "NACK rápido +
intervalo adaptativo":

- Depois de um NACK, o PSP esperava só o intervalo entre pedaços (piso de
  6 ms) antes do próximo. A resposta leva uma ida e volta inteira (20-40 ms).
  Resultado: 3 NACKs pelos mesmos pedaços em ~18 ms, frame abandonado antes
  de o primeiro reenvio chegar ("frames perdidos" 12-14), e o servidor
  mandando cada pedaço faltante até 3 vezes ("reenviados" 19-66%).
- Esses reenvios repetidos ocupam o ar e a fila do roteador **na frente do
  frame seguinte**. Por isso o "1º pedaço" cresce com a qualidade (36 -> 67
  ms) junto com os reenvios. Neste teste, ele não mede a ida e volta limpa.
- O intervalo médio entre pedaços também era mal medido: o PSP lê em
  sequência os pedaços já enfileirados, com intervalo ~0. A média caía, e 4x
  a média batia no piso de 6 ms. Qualquer pausa normal do Wi-Fi virava "perda".

O simulador não pegou o problema porque ainda usava a espera fixa de 20 ms do
PSP antigo.

**Correção (v0.3.1):**

- Depois de um NACK, o PSP espera média + 4 desvios da ida e volta medida
  (pedido -> 1º pedaço, como o RTO do TCP; 30-200 ms).
- O silêncio que indica "fim do frame perdido" é média + 4 desvios do
  intervalo entre pedaços, de 20 a 50 ms. O desvio cobre as leituras em
  rajada.
- O `fake_client` agora segue a mesma lógica, e um teste com 30 ms de ida e
  volta e 5% de perda exige zero pedaços repetidos. Com a espera antiga, o
  mesmo teste dá 34-35 repetidos em 3 s [SIM].

O que estes números já dizem, mesmo com a regressão:

- **Vazão na rajada de 356-448 KB/s**: o enlace está bom (suspeito 4
  descartado). A parte proporcional ao tamanho é o 802.11b fazendo o que dá.
- **PSP sem economia de energia e com sinal de 100%** (suspeito 1 descartado).
- **Falta medir a parte fixa sem os reenvios repetidos e sem o power save do
  PC.** Nas rodadas anteriores o power save do PC provavelmente já estava
  ligado. Então ele não explica tudo, mas é o suspeito que sobra (2), e o
  teste é barato.

### H.264: o Moonlight-PSP e o decoder de hardware

**O [Moonlight-PSP](https://github.com/k4idyn/Moonlight-PSP) usa H.264, mas
decodificado em software** (OpenH264 na CPU principal; o Media Engine só
converte as cores). Pelo changelog dele, em 480x272 o resultado é 15-18 fps
(o preset "Quality" usa 10 fps), com o limite no decode e não na rede (500
kbps). Hoje o MJPEG com o `sceJpeg` já dá ~20 fps em 480x272. Copiar esse
caminho seria um passo para trás. O autor tentou o decoder de hardware
(`sceMpeg`) e desistiu: o ringbuffer espera MPEG-PS em ordem, e o RTP do
Moonlight entrega pedaços fora de ordem com FEC no meio. **Isso não se aplica
ao PSPStream**, que entrega cada frame inteiro e em ordem (NACK).

**O decoder de hardware é rápido.** O PPSSPP mediu num PSP real
(pspautotests `video/mpeg/playertiming`): ~3,4 ms para decodificar um frame
480x272, mais 2,4 ms para converter para RGBA. É menos que os 7,9 ms do
`sceJpeg`. A questão era como chamá-lo com H.264 cru:

- O **PMP Mod / PMPlayer** (2006, código do magiK) fazia isso: um ringbuffer
  vazio, `sceMpegBasePESpacketCopy` levando o frame (Annex B) para a memória
  do Media Engine em blocos de 4095 bytes, e `sceMpegAvcDecode`, que já
  devolve RGBA 8888 com largura 512. O PPSSPP emula esse caminho.
- O pspautotests `video/mp4/mp4timing` mostra outro (sem ringbuffer, com
  `sceMpegAvcResourceInit`), testado em hardware, mas o PPSSPP só o roda com
  o `mpeg.prx` original do firmware.

**`psp/probe` usa o caminho do PMP** [EMU]: no PPSSPP (com
`tools/ppsspp-pmp-fix.patch`), os 60 frames de cada clipe (baseline/CAVLC e
main/CABAC) decodificam, inclusive direto na VRAM, e o número desenhado em
cada frame confere com o AU entregue. O emulador decodifica com FFmpeg em
modo de baixa latência e com tempo fixo, então **não responde** às perguntas
que importam: o tempo real e se o PSP segura frames. Dois erros meus que
travariam o PSP apareceram no emulador e foram corrigidos: o
`SceMpegRingbuffer` do pspsdk tem 44 bytes e a biblioteca escreve 48, e uma
leitura de u32 desalinhada.

#### Resultado no PSP-3000 (6.61 ARK-4), teste v1 [PSP]

| passo | AUs ok | decode por frame (RGBA incluso) | frames segurados |
|---|---|---|---|
| baseline/CAVLC na RAM | 60/60 | 4,05 ms (3,02-4,13) | **2** |
| main/CABAC na RAM | 60/60 | 4,17 ms (3,10-4,26) | **2** |
| baseline direto na VRAM | 60/60 | 3,55 ms (3,00-3,61) | **2** |
| baseline sem o frame 20 | 59/60, sem erro nem travada | 4,05 ms | 2 a 8 (imagem errada até o próximo IDR, como esperado) |

- **Funciona** chamado de um app comum, e é rápido: metade do `sceJpeg`
  (7,9 ms), inclusive gravando direto na VRAM. CABAC custa só ~3% a mais.
- **Mas o frame N só sai depois de entregar o N+2**: as duas primeiras
  chamadas voltam sem imagem, e depois sai sempre o de 2 AUs atrás. O SPS já
  diz `max_num_reorder_frames=0` e `max_dec_frame_buffering=1`, então não é
  reordenação pedida pelo stream: é uma profundidade fixa de pipeline do
  decoder. Com um frame por chamada, a 20 fps, seriam +100 ms de latência.
- Se o atraso é contado em chamadas, dá para empurrar o frame com chamadas
  baratas. A v2 do teste compara três jeitos: o frame + 2 cópias (P sem
  mudança, ~300 bytes no clipe), o frame + 2 AUs só com o AUD, e o frame +
  `sceMpegAvcDecodeStop`.

#### Teste v2: soltando os 2 frames presos [PSP]

| jeito | chamadas ok | tempo por frame mostrado | atraso |
|---|---|---|---|
| 1 chamada por frame | 60/60 | 4,05 ms | 2 frames |
| **frame + 2 cópias** | 180/180 | **12,1 ms** (cada cópia ~4,0 ms) | **0** |
| frame + 2 AUs só com AUD | erro `80628002` | (26 ms por chamada com erro) | 2 |
| frame + `sceMpegAvcDecodeStop` | 60/60; o Stop solta 1 imagem, 1,12 ms | 4,2 + 1,1 ms | 0 no 1º frame, **até 58 depois** |

- As cópias funcionam, mas cada chamada custa ~4 ms mesmo sem nada para
  decodificar: 12 ms por frame, contra 7,9 ms do JPEG.
- O Stop solta a imagem na hora e é barato, mas zera as referências. O 1º
  frame (IDR) saiu certo, e os P seguintes saíram errados. **Com todo frame
  IDR, o Stop não teria o que quebrar.**

#### H.264 só com IDR (intra) x JPEG, mesma qualidade [PC]

Os mesmos 12 frames (2 de desktop, 10 de jogos) reduzidos para 480x272.
JPEG do `jpegenc` (o do servidor) e x264 baseline com `keyint=1`, no menor
tamanho com SSIM igual ou maior:

| JPEG | KB (JPEG) | KB (H.264 intra, mesma SSIM) |
|---|---|---|
| q50 | 13,7 | **6,8 (49%)** |
| q70 | 18,5 | **8,4 (46%)** |

**Metade dos bytes, e cada frame continua independente** (como no MJPEG: uma
perda estraga só aquele frame, e o servidor pode pular frames à vontade). Se
"IDR + Stop" sair na hora no PSP, são ~5,3 ms de decode (menos que os 7,9 ms
do JPEG) com metade dos bytes na rede. Pelo modelo de custo por frame
(~21 ms fixos + tamanho / ~400 KB/s), na q50 seriam ~38 ms em vez de ~55 ms
por frame. A v3 do teste confere isso no PSP: intra com e sem Stop, CABAC,
um frame pulado e o Stop gravando direto na VRAM.

## 1. Tamanho de frame [PC]

Mesmo pipeline do servidor (`videoscale` -> I420 -> `jpegenc`), saída 480x272
4:2:0. "FPS teto" = só o limite da banda (vazão / tamanho), sem contar decode
nem perdas. A vazão real do PSP ainda precisa ser medida; 300-500 KB/s é a
faixa esperada para 802.11b.

### Jogos de PSP (30 frames reais do `frametests` do PPSSPP, já em 480x272)

LocoRoco, Monster Hunter 3rd, Final Fantasy Zero, Need for Speed Carbon, GTA,
Pursuit Force, Valkyria Chronicles, Project Diva e outros. É o conteúdo mais
próximo de "jogo na tela do PSP".

| qualidade | KB/frame (mediana) | min - max | FPS teto @ 300 KB/s | @ 400 KB/s | @ 500 KB/s |
|---|---|---|---|---|---|
| 30 | 11.1 | 3.9 - 18.2 | 26.6 | 35.5 | 44.3 |
| 40 | 13.1 | 4.2 - 20.9 | 22.5 | 29.9 | 37.4 |
| 50 | 15.1 | 4.4 - 23.5 | 19.6 | 26.1 | 32.6 |
| 60 | 17.3 | 4.8 - 26.4 | 17.0 | 22.7 | 28.4 |
| 70 | 20.8 | 5.3 - 30.6 | 14.2 | 18.9 | 23.7 |
| 80 | 25.6 | 6.1 - 38.5 | 11.5 | 15.4 | 19.2 |
| 90 | 38.7 | 8.5 - 55.7 | 7.6 | 10.2 | 12.7 |

### Desktop 1080p reduzido para 480x272 (IDE com código + página web), filtro bilinear2

| qualidade | KB/frame (mediana) | min - max | FPS teto @ 300 KB/s | @ 400 KB/s | @ 500 KB/s |
|---|---|---|---|---|---|
| 30 | 8.0 | 5.8 - 10.3 | 36.7 | 48.9 | 61.1 |
| 40 | 9.2 | 6.6 - 11.8 | 32.0 | 42.6 | 53.3 |
| 50 | 10.2 | 7.3 - 13.1 | 28.9 | 38.5 | 48.2 |
| 60 | 11.4 | 8.2 - 14.7 | 25.8 | 34.5 | 43.1 |
| 70 | 13.3 | 9.6 - 17.1 | 22.2 | 29.5 | 36.9 |
| 80 | 16.1 | 11.6 - 20.6 | 18.3 | 24.4 | 30.5 |
| 90 | 22.4 | 16.2 - 28.5 | 13.2 | 17.6 | 22.0 |

### Filtro de redução: a escolha que mais mudou o resultado [PC]

Desktop 1080p -> 480x272:

| filtro | KB/frame q50 | KB/frame q70 | texto | custo extra no PC |
|---|---|---|---|---|
| bilinear (2 taps, padrão do GStreamer) | 14.0 | 18.1 | serrilhado, letras quebradas | — |
| **bilinear2** (multi-tap, padrão do PSPStream) | **10.2** | **13.3** | liso, legível | ~2 ms/frame |
| lanczos | 10.9 | 14.3 | liso, um pouco mais nítido | ~4 ms/frame |

O bilinear de 2 taps não filtra ao reduzir 4x. O serrilhado vira ruído de alta
frequência, que o JPEG comprime mal. O `bilinear2` deixa o texto legível
**e** gera frames 27% menores. Na mesma banda, isso significa menos tempo de
rede por frame, ou seja, mais FPS e menos latência.

## 2. Custo no PC [PC]

| etapa | custo |
|---|---|
| reduzir 1080p BGRx -> 480x272 (bilinear / bilinear2 / lanczos) | ~1 / ~3 / ~5 ms |
| reduzir 1440p -> 480x272 (bilinear / lanczos) | ~2.5 / ~7.6 ms |
| jpegenc 480x272 | ~1 ms |
| captura -> JPEG pronto (fonte de teste ao vivo 720p60, medido pelo PTS) | ~9 ms |
| PipeWire 1080p60 BGRx -> JPEG (nó de teste, mesmo caminho do portal) | 59 fps sustentados |

A idade média do frame quando é enviado fica em ~7-8 ms com captura a
60 fps, que é meio intervalo de captura.

## 3. Pipeline completo, com rede e decode simulados [SIM]

`fake_client.py --kbps 400 --decode-ms 11` imita o PSP, com thread de rede
recebendo durante o decode. A vazão de 400 KB/s é uma suposição, e os 11 ms
de decode vêm do tempo que o PPSSPP atribui ao `sceJpeg`.

Qualidade adaptativa (padrão), desktop 1080p, começando em q90:

| tempo | q | KB/frame | FPS | latência captura->exibido |
|---|---|---|---|---|
| 0-2 s | 90 -> 63 | 20.0 | 18.5 | 65 ms |
| 2-4 s | 63 -> 51 | 13.9 | 24.6 | 49 ms |
| depois | 51 (estável) | 13.2 | 28.2 | 47 ms |

Benchmark de qualidade fixa (`--bench 30,60,90`, mesmo cenário):

| q | KB/frame | FPS | latência média | rede | decode |
|---|---|---|---|---|---|
| 30 | 10.3 | 36.2 | 38.8 ms | 27.4 ms | 11.0 ms |
| 60 | 14.7 | 25.5 | 50.6 ms | 39.2 ms | 11.0 ms |
| 90 | 28.5 | 13.2 | 87.1 ms | 75.8 ms | 11.0 ms |

Fonte de teste ao vivo (720p60), mesmo modelo: ~47 ms, decompostos em
captura 9 + idade 7 + rede 19 + PSP 11.

**Conclusão provisória** (antes dos testes no PSP): com 802.11b, a rede
domina a latência. Por isso o padrão é a qualidade adaptativa, que escolhe a
maior qualidade cuja transferência cabe no orçamento de tempo por frame. O
alvo virou 20 fps depois das medições no PSP (seção 0).

## 4. Emulador [EMU] (não representativo)

`bench=1` no PPSSPPHeadless, frame 480x272 de 17 KB:

| decoder | tempo emulado |
|---|---|
| sw (libjpeg-turbo) | 20.5 ms |
| hw (sceJpeg) | 10.85 ms |

O valor do hw é o atraso que o próprio PPSSPP atribui ao `sceJpeg`
(300 µs + w·h/14 + w·h/110). O do sw é uma estimativa por contagem de
instruções, sem os stalls de cache e memória do Allegrex real. **Não use esses
números para decidir nada.** Eles só confirmam que os dois caminhos funcionam.

## 5. Como medir no hardware [PSP]

Os resultados são salvos em arquivos para colar aqui.

### 5.1 Decode: hardware x software

1. No `server.txt` do PSP, adicione `bench=1`.
2. No PC: `python3 server/pspstream.py --source static --fixed-quality -q 70`
3. O PSP mostra `sw: X ms/frame` e `hw: Y ms/frame` antes de começar o stream.
4. Repita com `-q 30` e `-q 90`: o tempo de decode cresce com o tamanho.

### 5.2 FPS, KB/frame, vazão do Wi-Fi e latência por qualidade

Com uma captura de tela do seu jogo ou desktop:

```sh
python3 server/pspstream.py --source static --image minha_tela.png --bench 30,50,70,90
```

Abra o PSPStream no PSP e espere ~50 s. A tabela sai no console e em
`bench_AAAAMMDD_HHMMSS.md`. Rode duas vezes, com o decoder hw e com o sw
(SELECT+START+quadrado no PSP troca o decoder).

### 5.3 Latência "vidro a vidro" (a única que inclui tudo)

1. No PC, abra `tools/latency_clock.html` no navegador, em tela cheia (F11).
2. `python3 server/pspstream.py --source portal` e escolha esse monitor.
3. Filme o monitor e o PSP juntos com o celular. Câmera lenta (120/240 fps)
   dá mais precisão.
4. Pause o vídeo em vários quadros. Em cada um, latência = relógio do monitor
   − relógio no PSP. Faça a média de umas 10 leituras. A precisão é de ±1
   quadro do monitor (~17 ms a 60 Hz) por leitura; a média reduz isso.

Esse número inclui tudo: compositor, captura, encode, Wi-Fi, decode, vsync e
o LCD do PSP. A diferença para a "latência" do log do servidor é a parte que
o servidor não enxerga (compositor e LCD).

### 5.4 TCP x UDP

Rode o mesmo `--bench` duas vezes, uma com `transport=tcp` e outra com
`transport=udp` no `server.txt` (ou troque com SELECT+START+L e reinicie o
benchmark). O transporte sai no cabeçalho da tabela. Compare as colunas
"Wi-Fi (KB/s)", "rede (ms)" e "p95". A coluna "pedaços reenviados" mostra
quanto o Wi-Fi está perdendo. Para o conteúdo não variar entre as rodadas,
use `--source static --image captura.png`.

Libere as duas portas no firewall: `sudo firewall-cmd --add-port=5123/tcp --add-port=5123/udp`.

### 5.5 Ajustes para comparar (atalhos SELECT + START + botão)

| atalho | o quê | o que observar |
|---|---|---|
| quadrado | decoder hw <-> sw | decode (ms) no overlay |
| círculo | vsync on/off | rasgo na imagem x ~8 ms de latência média |
| X | prefetch on/off | FPS (sem prefetch: rede + decode em série) |
| L | transporte TCP <-> UDP (reconecta) | rede, FPS, p95 |
| triângulo | overlay | — |

Também vale testar a **"Economia de energia WLAN"** do XMB ligada e
desligada. O PSPStream avisa na tela quando ela está ligada.

### Tabela para preencher

| teste | resultado |
|---|---|
| decode sw, q70 (ms/frame) | 34 |
| decode hw, q70 (ms/frame) | 7,91 |
| vazão Wi-Fi medida, TCP (KB/s, coluna "Wi-Fi" do bench) | 235-356 |
| vazão Wi-Fi medida, UDP (KB/s) | 367-412 (enlace ~470 descontando o overhead) |
| UDP, q50: FPS / latência / p95 | 21.8 / 42 / 63 ms |
| TCP, q50: FPS / latência / p95 | 8.5 / 203 / 805 ms |
| UDP + early_kb=10, q50: FPS / latência / p95 | 16.0 / 64 / 126 ms (pior que sem: 19.7 / 46 / 75 ms) |
| FPS em q50 / q70 (bench) | |
| latência servidor (captura -> exibido), q adaptativo | |
| latência vidro a vidro (câmera) | |
| economia de energia WLAN ligada: latência | |
