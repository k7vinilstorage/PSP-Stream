Caderno de medições do desenvolvimento, em ordem: cada seção registra o que
se sabia naquele momento, inclusive hipóteses que os números seguintes
derrubaram. O resumo atual está em [Desempenho](Desempenho).

Cada número aqui tem uma origem, e elas não se misturam:

| marca | origem | vale para o PSP? |
|---|---|---|
| **[PC]** | medido de verdade no PC de desenvolvimento (CPU x86, 4 núcleos) | sim, para a parte do PC (tamanho de frame, custo de captura/encode) |
| **[SIM]** | `tools/fake_client.py` com Wi-Fi e decode simulados | não: é um modelo do pipeline |
| **[EMU]** | PPSSPPHeadless | não: o tempo emulado não é o tempo do hardware |
| **[PSP]** | PSP-3000 real (ARK-4) + Fedora 44, medido pelo usuário | sim |
| **[PSP relato]** | o que o usuário viu jogando no PSP-3000, sem números anotados | sim, mas é qualitativo |

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
| pedido antecipado | parte fixa | fixo em 6-14 KB **piorou no PSP**; v0.8: automático (~2-3 KB), +12-33% de FPS [SIM] | a medir no PSP |
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

#### Teste v3: todo frame IDR + Stop [PSP]

| passo | tempo por frame | atraso |
|---|---|---|
| intra, 1 chamada | 4,16 ms | 1 frame |
| **intra + Stop** | **4,23 ms** (Stop 1,12 ms) | **0** |
| intra CABAC + Stop | 4,30 ms, 11% menos bytes | 0 |
| intra + Stop, sem o frame 10 | 4,24 ms, sem erros | 0 |
| **intra + Stop direto na VRAM** | **3,72 ms** (Stop 0,62 ms) | **0** |

**É o que entrou no stream (v0.5, `--codec h264`).** Comparado com o
`sceJpeg`: metade do tempo de decode, ~40% dos bytes na mesma qualidade,
nenhum frame de atraso, e cada frame independente como no MJPEG.

#### Encoder do servidor [PC]

- **x264enc (GStreamer) segura 1 frame** em todas as configurações testadas
  (`tune=zerolatency`, `threads=1`, `rc-lookahead=0`, `sync-lookahead=0`,
  `pass=quant`): o AU do frame N só sai depois de entrar o N+1. Numa tela
  parada (o portal só manda frame quando algo muda), a última mudança nunca
  sairia. Descartado.
- **openh264enc entrega na hora.** Só faz baseline/CAVLC (11% maior que
  CABAC no teste do PSP). Com `rate-control=off` ele ignora `qp-min/qp-max`;
  com `rate-control=quality`, bitrate alto e `qp-min = qp-max`, o QP vale.
  O QP não muda com o pipeline rodando: como todo frame é IDR, o servidor
  recria o pipeline do encoder quando a qualidade muda.
- Calibração (12 frames, desktop + jogos, mesma SSIM do `jpegenc`), 3,7 ms
  de encode por frame:

| JPEG | KB | QP do openh264 | KB | % do JPEG |
|---|---|---|---|---|
| q30 | 10,2 | 40 | 4,0 | 40% |
| q50 | 13,6 | 36 | 6,1 | 45% |
| q70 | 18,3 | 34 | 7,7 | 42% |
| q90 | 34,5 | 30 | 11,3 | 33% |

  Reta usada: `QP = 44,6 - 0,16·q` (q na escala do JPEG).

#### v0.5 no PSP-3000: H.264 x JPEG no stream de verdade [PSP]

UDP, `--source static`, mesma imagem, sinal do PSP 45-47%. Ping no início
do stream: 6,3 ms (select) / 5,8 ms (consulta).

| q | H.264: KB | JPEG: KB | H.264: FPS | JPEG: FPS | H.264: latência / p95 | JPEG: latência / p95 | H.264: decode | JPEG: decode | H.264: reenviados | JPEG: reenviados |
|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 1,7 | 6,8 | 27,1 | 22,5 | 26 / 103 ms | 40 / 65 ms | 4,1 ms | 7,3 ms | 0,6% | 1,6% |
| 50 | 2,4 | 9,3 | **28,7** | 19,5 | **28 / 77 ms** | 48 / 117 ms | 4,0 ms | 7,4 ms | 0,3% | 2,3% |
| 70 | 3,8 | 12,5 | **28,6** | 12,8 | **27 / 55 ms** | 62 / 116 ms | 4,1 ms | 7,4 ms | 0,8% | 3,4% |
| 90 | 5,4 | 23,5 | **22,1** | 7,0 | **33 / 61 ms** | 124 / 236 ms | 4,2 ms | 7,9 ms | 1,9% | 14,8% |

- Na mesma qualidade (q na escala do JPEG), o H.264 mandou **23-30% dos
  bytes** nesta imagem, menos que os 33-45% da calibração com 12 frames.
  Resultado: **+47% de FPS na q50, 2,2x na q70, 3,2x na q90**, e latência
  41-74% menor. O decode no stream (4,0-4,2 ms) confere com o teste v3.
- Com frames de 2-4 KB, **o custo fixo por frame virou o gargalo**: o FPS
  fica em ~28,7 de q30 a q70. A rede leva ~38-40 ms por frame, dos quais o
  "1º pedaço" é 22-35 ms e a rajada só 4-8 ms. O servidor reage em 0,1-0,2
  ms ("espera por frame novo"), e o ping no início é de 6 ms. Sobram ~16-29
  ms por frame entre o pedido sair do PSP e o 1º pedaço chegar, sem
  explicação ainda.
- Com o rádio parado ~80% do tempo, pedir o próximo frame antes de o atual
  chegar (`early_kb`) deveria esconder esse custo fixo. Com JPEG grande, isso
  piorou porque o rádio estava ocupado; com H.264, a situação é outra.
- A "vazão na rajada" de q30 (62 KB/s) não vale: com 2 pedaços por frame, a
  conta tem um pedaço só.

#### v0.5: tela de verdade e pedido antecipado [PSP]

**Portal (tela do Fedora), `--codec h264`, qualidade adaptativa:** em q90 (o
máximo), 2,2-3,1 KB por frame, 16-27 fps (média ~21), latência média 22-41
ms (p95 30-118 ms), fonte a ~38 fps. Com JPEG, o mesmo adaptativo ficava
perto de q55 com ~46 ms. Agora a qualidade máxima sai com latência menor
que a média de antes.

**`early_kb=8` (estático, H.264):** q30 32,3 fps, q50 31,8, q70 25,8, q90
31,8. Sem ele: 27,1 / 28,7 / 28,6 / 22,1. Ganho de 10-44% (fora a q70,
dentro da variação de ~20% entre rodadas) e latência 0-8 ms maior. Se o
custo fixo por frame fosse só espera, o pedido antecipado dobraria o FPS.
Não dobrou.

**O custo fixo, por eliminação:** o servidor reage em 0,1-0,2 ms. O ping
com a rede parada leva 6 ms. Mas pedido -> 1º pedaço leva 15-35 ms durante
o stream (e 30-54 ms com o portal, que inclui ~12 ms de espera por frame
novo). O que muda entre os dois: o ping vai em rajada (16 seguidos), e o
stream é pedido/resposta com pausas de 20-30 ms. É o padrão em que o power
save de uma placa Wi-Fi atrapalha: ela cochila nas pausas e o roteador
segura o pedido seguinte até ela acordar. O servidor detecta o power save
do PC ligado. Streams contínuos (Moonlight, por exemplo) não têm pausas e
não sentem isso. **Teste que decide:** `sudo iw dev wlp0s20f3 set
power_save off` (volta no reboot) e o mesmo bench.

#### H.264, bench das 12:37 [PSP]

Sem anotação de power save nem `early_kb` (a confirmar), sinal 55%:

| q | KB | FPS | latência / p95 | rede | 1º pedaço | rajada | reenviados |
|---|---|---|---|---|---|---|---|
| 30 | 1,7 | **44,8** | **20 / 34 ms** | 26,0 ms | 23,7 ms | 2,4 ms | 0,4% |
| 50 | 2,4 | **40,8** | **22 / 36 ms** | 25,6 ms | 21,3 ms | 4,4 ms | 1,1% |
| 70 | 3,8 | 21,1 | 55 / 154 ms | 51,2 ms | 32,3 ms | 19,1 ms | 6,3% |
| 90 | 5,4 | 6,9 | 558 / 957 ms | 148,7 ms | 55,6 ms | 93,2 ms | 15,2% |

- q30/q50: o melhor resultado até aqui, 41-45 fps e ~21 ms de latência
  média (antes 27-32 fps).
- q70/q90: o enlace piorou no meio da rodada (vazão na rajada de 205 e 56
  KB/s, 6-15% reenviados, o Wi-Fi caiu para 46 KB/s). Com 3,8-5,4 KB por
  frame, as rodadas anteriores fizeram 22-32 fps; não é efeito da qualidade.
- A v0.6 manda um ping por segundo durante o stream (coluna "ping no
  stream" no bench), para separar rádio de resposta com frame.

#### H.264, power save do PC desligado, `early_kb=0`, v0.6 [PSP]

Sinal 72%. Ping no início não registrado.

| q | FPS | latência / p95 | rede | 1º pedaço | ping no stream (mín) | rajada | reenviados |
|---|---|---|---|---|---|---|---|
| 30 | 32,1 | 23 / 50 ms | 33,2 | 32,2 | 11,2 (3,5) | 7,6 | 1,6% |
| 50 | **42,8** | **19 / 34 ms** | 21,8 | 18,4 | 8,4 (3,5) | 3,5 | 1,1% |
| 70 | 23,9 | 31 / 68 ms | 39,4 | 30,7 | 13,3 (3,8) | 8,8 | 2,0% |
| 90 | 28,6 | 29 / 45 ms | 34,8 | 23,3 | 9,7 (3,5) | 11,6 | 1,2% |

- Com o power save ligado (12:22): 27,1 / 28,7 / 28,6 / 22,1 fps. Melhorou
  em q50 e q90, empatou em q30 e q70: efeito menor do que eu previa, dentro
  da variação entre rodadas fora a q50.
- **O ping durante o stream mede 8-13 ms (mínimo 3,5 ms), e o 1º pedaço
  18-32 ms.** O caminho de rede é o mesmo (PSP -> roteador -> PC ->
  servidor -> volta). Sobram ~10-20 ms por frame que só aparecem quando a
  resposta é um frame: não é a ida e volta do rádio em geral.
- Suspeitos: (1) o PSP começa o decode e o flip logo depois de pedir o
  próximo frame (prefetch), e isso atrasaria a recepção; (2) o pacote de
  1452 bytes contra 8 do ping, se o roteador mandar ao PSP numa taxa baixa.
  Teste do (1): `prefetch=0`. O bench agora mostra o mínimo e a mediana do
  1º pedaço.

#### prefetch=1 x prefetch=0: o custo fixo era quase todo travada de 200 ms [PSP]

H.264, power save do PC desligado, `early_kb=0`, v0.6:

| q | FPS (pre1 / pre0) | 1º pedaço pre1: média (mín, mediana) | 1º pedaço pre0: média (mín, mediana) | ping no stream (pre1 / pre0) |
|---|---|---|---|---|
| 30 | 29,0 / 24,2 | 41,6 (5,2, **8,7**) | 37,5 (5,3, **8,1**) | 7,4 / 8,1 |
| 50 | 42,3 / 24,5 | 23,0 (5,1, **7,5**) | 33,0 (5,2, **9,1**) | 10,8 / 9,4 |
| 70 | 37,2 / 25,6 | 20,9 (5,0, **7,2**) | 28,0 (5,0, **9,4**) | 9,2 / 11,4 |
| 90 | 29,6 / 25,8 | 25,5 (5,0, **7,7**) | 23,5 (5,3, **8,9**) | 7,0 / 13,3 |

- **O frame típico chega em ~8 ms** (mediana), igual ao ping. A média de
  20-40 ms vem de poucos frames muito atrasados. O prefetch não muda a
  mediana: o decode não atrapalha a recepção (suspeito 1 descartado). O
  prefetch vale 1,2-1,7x de FPS.
- Média - mediana = 13-33 ms. Com o PSP esperando **200 ms fixos** para
  repetir um pedido sem resposta, isso é ~1 frame em 6-15 que perdeu o
  pedido ou a resposta inteira. A q30 é a pior: frames de 2 pedaços somem
  inteiros numa rajada de interferência, e perdas parciais o NACK resolve
  rápido.
- **v0.7:** o pedido é repetido depois de uma ida e volta medida (média + 4
  desvios do 1º pedaço, 30-200 ms), e o overlay conta as repetições
  ("repet"). Simulação (`fake_client --loss 0.02 --loss-up 0.05 --rtt-ms 8`,
  H.264 q30) [SIM]: **63-70 fps** contra 40-47 fps com os 200 ms.

#### v0.7 no PSP-3000: o FPS dobrou [PSP]

H.264, `--source static`, prefetch=1, `early_kb=0`, power save do PC
desligado, sinal 72%:

| q | KB | FPS | latência / p95 | rede | 1º pedaço: média (mín, mediana) | ping no stream | rajada | reenviados |
|---|---|---|---|---|---|---|---|---|
| 30 | 1,7 | **64,2** | 19 / 37 ms | 14,3 | 12,7 (5,1, 6,7) | 11,4 | 7,0 | 0,5% |
| 50 | 2,4 | **60,6** | 20 / 39 ms | 15,7 | 12,0 (1,8, 7,2) | 9,7 | 3,8 | 1,5% |
| 70 | 3,8 | **53,4** | 23 / 40 ms | 18,7 | 11,6 (1,8, 6,9) | 10,4 | 7,2 | 1,4% |
| 90 | 5,4 | **43,3** | 28 / 55 ms | 22,7 | 12,9 (1,8, 6,9) | 24,1 | 9,9 | 1,1% |

- A média do 1º pedaço caiu de 21-42 ms para 12-13 ms (mediana ~7 ms): as
  travadas de 200 ms sumiram.
- q30-q50 bate no limite da tela do PSP (60 Hz). Na q90, quem limita é a
  rede: 5,4 KB por frame.

**Evolução na mesma imagem (`--source static`):**

| versão | q50: FPS / latência | q90: FPS / latência |
|---|---|---|
| v0.3 (JPEG, TCP pull) | 8,5 / 203 ms | 3,0 / 265 ms |
| v0.3 (JPEG, UDP) | 19,7 / 46 ms | 10,9 / 90 ms |
| v0.5 (H.264 intra + Stop) | 28,7 / 28 ms | 22,1 / 33 ms |
| v0.6 (+ power save do PC desligado) | 42,8 / 19 ms | 28,6 / 29 ms |
| v0.7 (repetição de pedido adaptativa) | 60,6 / 20 ms | 43,3 / 28 ms |
| **v0.8 (pedido antecipado automático)** | **69,5 / 21 ms** | **61,3 / 26 ms** |

Acima de 60 fps, o PSP recebe mais frames do que a tela de 60 Hz mostra, e
o excedente é descartado antes do decode. Mesmo assim, sempre há um frame
novo pronto na hora da troca de tela.

#### v0.7 com a tela de verdade: desktop e Minecraft [PSP]

`python3 server/pspstream.py --codec h264` (portal, adaptativo com alvo de 20
fps, que ficou em q90), `early_kb=0`, sinal do PSP oscilando entre 47% e 92%.
Linhas do log a cada 2 s:

| trecho | KB/frame | FPS (fonte) | latência / p95 | 1º pedaço | rajada (vazão) | espera por frame novo | reenviados |
|---|---|---|---|---|---|---|---|
| desktop | 2,0-2,6 | 24-30 (36-40) | 27-34 / 48-81 ms | 24-35 ms | 4-7 ms | 8-10 ms | 0-3% |
| Minecraft | 6-10,7 | 18-36, típico ~28 (37-42) | 38-72 / 57-170 ms | 9-33, típico ~13 ms | 13-45 ms (243-485 KB/s) | 0,3-4,6 ms | 0-16,5% |
| queda de sinal (50%) | 2,6 | 9 | 102 / 200 ms | 54 ms | 56 ms (29 KB/s) | 1,3 ms | 16,7% |

- **Minecraft:** a rede é o gargalo. Cada frame passa ~12 ms esperando o 1º
  pedaço e 15-30 ms transferindo. O tempo morto entre um frame e o pedido do
  próximo é ~1/3 do ciclo.
- **Desktop:** frames pequenos. O servidor espera ~10 ms por um frame novo da
  captura, e o FPS (24-30) fica abaixo do da captura (36-40).
- Em aberto: no desktop, "1º pedaço" menos "espera" dá ~20 ms. No jogo dá
  ~10 ms e no bench estático ~7 ms (mediana). Ainda não sei de onde vêm os ~10
  ms a mais.

#### v0.8: pedido antecipado automático [SIM]

O pedido antecipado volta, agora calculado: o PSP pede o próximo frame quando
o que falta do atual leva uma ida e volta para chegar. Em bytes, isso é ping
do início / intervalo médio entre pedaços x 1400 bytes. No PSP-3000, ~5 ms /
~3 ms x 1400 ≈ 2-3 KB, com teto de 8 KB. Os testes antigos (6-14 KB fixos,
JPEG) pediam 2-5x cedo demais: o frame seguinte ia inteiro para a fila do
roteador, e a latência subia sem ganho de FPS. Duas regras evitam desperdício:

- **Frame com buraco não antecipa.** O próximo entraria na fila na frente do
  reenvio, e o frame com perda seria descartado.
- **Frame com buraco e um mais novo já chegando:** descarta o frame em vez de
  pedir reenvio. O reenvio chegaria depois do mais novo e seria jogado fora.
  A 1ª simulação, sem essas regras, perdia 12% dos frames e reenviava ~80
  pedaços à toa a cada 12 s.

Simulação: `tools/fake_client.py` com 400 KB/s, ida e volta de 5 ms, 2% de
perda e decode de 4 ms. Servidor H.264 local, captura a 38 fps como o portal.
O FPS é o do PSP falso, e a latência e o tempo morto vêm do log do servidor.

| cenário | `early_kb=0`: FPS / latência / tempo morto | `auto`: FPS / latência / tempo morto | antecipa |
|---|---|---|---|
| desktop (fonte de teste, 2,3 KB) | 37,5-37,8 / 27-30 ms / 26-28 ms | 37,0-37,7 / 26,4-26,8 ms / 29 ms | 2,5 KB |
| jogo (pinwheel q70, 10 KB) | 26,7-28,1 / 63-68 ms / 9,7-10 ms | **31,0-31,6** / 60-64 ms / 3,9-4,5 ms | 2,3 KB |
| estático q30 (4,7 KB) | 49,7 / 24,2 ms / 9,8 ms | **66,2** / 24,2 ms / 3,8 ms | 2,6 KB |
| estático q90 (8,4 KB) | 31,9 / 35,6 ms / 9,8 ms | **36,3** / 35,3 ms / 4,8 ms | 2,5 KB |
| estático q90, 5% de perda, 10 ms, 350 KB/s | 22,4 / 50 (p95 74) ms | **26,6** / 47 (p95 58) ms | 4,0 KB |

- O "tempo morto" mede do último pedaço de um frame ao 1º do seguinte. O
  piso é o intervalo de um pedaço (~3,5 ms a 400 KB/s), então os ~4 ms do
  `auto` são o máximo possível.
- A latência não subiu, então não se formou fila. No desktop, os dois modos
  já acompanham a captura na simulação. Lá a ida e volta simulada é limpa (5
  ms), e no PSP real o "1º pedaço" do desktop foi de 24-35 ms.
- **Isto é simulação.** A primeira versão do pedido antecipado também ganhou
  na simulação (+62%) e não ganhou nada no PSP. O bench e o teste com o jogo
  no PSP decidem. `early_kb=0` no `server.txt` volta ao comportamento da v0.7.

#### v0.8 no PSP-3000: bench e Minecraft [PSP]

**Bench** (H.264, `--source static`, `early_kb=auto`, sinal 100%):

| q | KB | v0.7: FPS / latência / p95 | v0.8: FPS / latência / p95 | tempo morto entre frames | antecipa |
|---|---|---|---|---|---|
| 30 | 1,7 | 64,2 / 19 / 37 ms | **71,2** / 21 / 36 ms | +6,7 ms | 3,2 KB |
| 50 | 2,4 | 60,6 / 20 / 39 ms | **69,5** / 21 / 38 ms | +5,2 ms | 2,5 KB |
| 70 | 3,8 | 53,4 / 23 / 40 ms | 55,7 / 31 / 70 ms | +4,5 ms | 2,5 KB |
| 90 | 5,4 | 43,3 / 28 / 55 ms | **61,3** / 26 / 36 ms | +4,0 ms | 2,5 KB |

- O limite calculado ficou em 2,5-3,2 KB, como previsto. O tempo morto
  ficou em 4-7 ms, perto do piso (o intervalo de um pedaço).
- Em q90, +42% de FPS, com latência e p95 menores. A fase q70 teve ping no
  stream mais alto (12 ms contra 7-10 ms) e latência pior. Entre rodadas, o
  Wi-Fi varia ~20%.

**Tela de verdade** (`--source portal`, adaptativo em q90, sinal 70-100%):

| trecho | KB/frame | FPS (fonte) | latência / p95 | tempo morto | espera por frame novo |
|---|---|---|---|---|---|
| desktop | 2,1-4,2 | 29-37,5 (36-40) | 24-37 / 31-77 ms | +15-24 ms | 10-17 ms |
| Minecraft | 5-9 | 25-39,5, típico 35-37 (37-40) | 28-46 / 37-85 ms, típico 30-38 | +6-14 ms | 3-11 ms |
| interferência | 11 | 9,5-25,5 | 58-115 / 84-239 ms | | 0,1-0,8 ms |

- **Minecraft:** de ~28 fps e 50-60 ms (v0.7) para 35-37 fps e 30-38 ms.
  O FPS encostou no da fonte, então quem limitava passou a ser a captura.
- **Desktop:** de 24-30 para 29-37,5 fps, também perto da fonte. O tempo
  morto aqui é a espera por um frame novo.
- A "fonte" de ~38 fps vem do próprio portal (abaixo), não do servidor.

#### Captura do portal: o videorate cortava 60 fps para ~38 [PC]

O pipeline tinha `videorate drop-only=true max-rate=60` para respeitar o
`--fps`. O portal entrega taxa variável (`framerate=0/1`), e o horário dos
frames treme ±1 ms. Nessa situação, o videorate (GStreamer 1.24) descarta
~1/3 dos frames:

| entrada (60 Hz, `framerate=0/1`) | videorate `max-rate=60` | videorate `max-rate=75` | limitador novo |
|---|---|---|---|
| sem tremor | 60,0 | - | 60,0 |
| tremor ±1 ms | **38,2** | 38,0 | 60,0 |
| tremor ±2 ms | 39,5 | 37,7 | 60,0 |
| ao vivo, pipeline completo, ±1-2 ms | **37,3** | - | **60,0** |

O videorate saiu. O `--fps` agora é aplicado por uma sonda na saída da fila
(`RateLimiter`): agenda de 1/fps com 25% de tolerância. Uma fonte de 60 Hz
passa inteira, e uma de 144 Hz fica em ~60-70 fps. O servidor agora loga
quanto a fonte entrega, quanto passa pelo limite e quanto é codificado.

**No portal de verdade, isso não era o limite [PSP].** Com o log novo (Fedora
44, GNOME, tela 2240x1400 a 59,998 Hz, Minecraft aberto), o próprio PipeWire
entrega 37,7-38,7 fps, e tudo passa pelo limite:

```
formato da captura: video/x-raw, format=BGRA, width=2240, height=1400, framerate=0/1,
                    max-framerate=7864015/131072 (59,998), ...
captura: a fonte entrega 37.7 fps (intervalo mediano 32.2 ms, p10 16.8, p90 33.8);
         passam pelo limite de 60 fps: 37.7; codificados: 37.9
```

Os intervalos são de 1 ou 2 quadros da tela (16,7 ou 33,3 ms), com ~40% de
1 quadro. Duas explicações cabem, e um teste separa as duas:

1. **O jogo roda a ~38-40 fps.** Uma GPU integrada com o Minecraft em
   2240x1400 dá isso fácil. O GNOME só manda frame quando a tela muda, então
   a captura não passa do FPS do jogo.
2. **O GNOME (mutter) limita a captura.** O limitador dele usa um intervalo
   mínimo igual ao período da tela (16 667 µs). Um frame que chega alguns µs
   adiantado é pulado, e o seguinte vem 2 quadros depois. A outra hipótese é
   a cópia da tela inteira (2240x1400, 12,5 MB) para a memória comum, feita
   pelo próprio GNOME.

Teste: ver a "fonte" com algo que muda a 60 fps de verdade (o mouse girando
sem parar no desktop, ou um vídeo de 60 fps) e o FPS do Minecraft na tela F3.

**Resultado:** o Minecraft roda a 60 fps no notebook, então a explicação 1
cai e quem limita é a captura do GNOME. O formato negociado não tem
`memory:DMABuf`, então a cada frame o GNOME copia a tela inteira (12,5 MB) da
GPU para a memória comum. O `--dmabuf` (experimental) pede a tela como
DMA-BUF e reduz no OpenGL: só 480x272 (~0,5 MB) chega à CPU. Se o limite for
essa cópia, a fonte sobe para ~60 fps. Se for o limitador de intervalo do
GNOME, nada muda. Testado aqui só sem GPU (EGL sem tela, llvmpipe): a
redução gera 480x272 com as bordas certas, e um pipeline sem DMA-BUF volta
sozinho para o modo normal.

**`--dmabuf` no PC do usuário [PSP]:** funcionou, com a tela em DMA-BUF
(`drm-format=XR24:0x0100000000000002`, tiled da Intel). Mas a fonte continuou
em 38,3-38,7 fps (intervalo mediano 32,4-33,1 ms, p10 16,0-16,6 ms), e a
"captura" subiu de ~4 para 5-9 ms (a GPU sincroniza para devolver a imagem).
Sem ganho: a cópia não era o limite. Fica como opção, mas não é recomendado.

**A causa, no código do mutter 50** (`meta-screen-cast-stream-src.c`, conferido
nas tags 46.0, 48.0, 50.0 e 51.0):

```c
min_interval_us = (G_USEC_PER_SEC * max_framerate.denom) / max_framerate.num;
if (time_since_last_frame_us < min_interval_us) {
    /* "Skipped recording frame on stream %u, too early" */
    meta_screen_cast_stream_src_queue_follow_up (src, flags);
    return;
}
```

- O `max-framerate` negociado é a taxa da tela (7864015/131072 = 59,998 Hz),
  então o intervalo mínimo vira 16 667 µs (divisão inteira). O horário de
  cada frame é o tempo de apresentação esperado do quadro, que treme alguns
  µs em torno de 16 667,3. Todo quadro que cai abaixo de 16 667 é pulado.
- Na captura de monitor, a reposição de um frame pulado pede um redesenho
  (`clutter_actor_queue_redraw_with_clip` de 1x1 pixel), que só sai no
  próximo quadro da tela: 33,3 ms depois do último frame gravado.
- Se uma fração p dos quadros passa, o intervalo médio é 16,7p + 33,3(1-p).
  Com 38,3 fps, p = 0,43: ~43% de intervalos de 16,7 ms e o resto de 33 ms.
  Bate com o p10 de 16,6 ms e a mediana de 33 ms.
- Na captura de **janela**, a reposição é um timer de 1/60 s que grava sem
  esperar redesenho. É por isso que o OBS relata que só a captura de tela
  inteira fica travada ([mutter #4214](https://gitlab.gnome.org/GNOME/mutter/-/work_items/4214)).
- No mutter 50, a faixa anunciada para monitores é de 1/1 até a taxa da
  tela, e o cliente não consegue negociar outro valor. No mutter 51, o
  mínimo voltou a 0/1, e `max-framerate=0/1` desliga o limitador
  (`max_framerate.num > 0`).

**`--window` (janela do Minecraft em tela cheia) [PSP]:** `max-framerate=60/1`, e
a fonte entregou 36,5, 38,8 e 41,3 fps nos três relatórios (intervalo mediano
21-26 ms, p10 16,8, p90 32-43), com janelas de 2 s chegando a 45-47 fps. Quase
igual aos 38 da captura de tela: a reposição por timer ajuda pouco, porque o
limitador continua pulando ~metade dos quadros. **No GNOME 50, o teto da
captura é ~40 fps**, por qualquer caminho do portal.

O PipeWire repassa o `max-framerate` pedido nas caps do GStreamer
(`src/gst/gstpipewireformat.c`). Então, no GNOME 51, pedir `max-framerate=0/1`
desliga o limitador. No GNOME 50, a faixa anunciada é de 1/1 até a taxa da
tela, e não há valor que ajude.

Na mesma rodada: cenas leves (1,4-3,7 KB) deram 25-38 fps com 29-46 ms; cenas
pesadas (9-12 KB), 25-37 fps com 53-75 ms (rede 25-36 ms por frame).

**Sunshine em Flatpak** usa a mesma captura pelo portal (o Flatpak não permite
a KMS). No código dele (`src/platform/linux/pipewire.cpp`): ele pede
`max-framerate` de 0/1 a 1000/1 com preferência pela taxa pedida (60). Como o
GNOME 50 oferece no máximo 59,998, ele desliga o próprio ritmo e manda cada
frame quando chega, ou seja, ~40 fps no mesmo PC. Só repete a imagem depois
de 1 s sem nada novo.

**Captura KMS (`--source kms`):** lê o plano principal da placa de vídeo, sem
o compositor, como a captura KMS do Sunshine. O auxiliar
`tools/kms/pspstream-kms` (com `CAP_SYS_ADMIN`) exporta o framebuffer como
DMA-BUF a cada troca de buffer, e o servidor reduz no OpenGL.

**Primeira rodada KMS [PSP]:** funcionou. A fonte entregou **58,3 fps**
(intervalo mediano 16,5 ms, p10 16,2, p90 17,5), e as janelas de 2 s ficaram
em 58-60 fps, contra ~38 pelo portal. O buffer da tela usa a compressão da
Intel Gen12 com cor de limpeza (modificador `0x0100000000000008`, 3 planos), e
a importação no OpenGL aceitou. Quando o jogo entra ou sai da tela cheia, o
formato troca entre XR30 (10 bits) e XR24, e o pipeline renegocia sem travar.
A "idade" do frame enviado caiu para 7-10 ms, e a espera por frame novo para
~0,1 ms.

O PSP ficou em 9-28 fps e 60-130 ms, porque nessa rodada a rede limitou:
- frames de 10-19 KB em q90;
- sinal oscilando entre 47% e 85%, com até 20% de pedaços reenviados;
- ping no stream de 17 a 346 ms.

A captura foi do monitor 0 de 2 (1280x720 a 59,855 Hz). O log agora lista
os outros monitores e o `--kms-monitor` de cada um.

**Segunda rodada KMS [PSP]** (tela do notebook em 1280x720, fonte 57,9 fps):

| trecho | KB/frame | FPS | latência / p95 | antes, pelo portal (~38 fps) |
|---|---|---|---|---|
| cenas leves | 3,7-4,4 | **43-56** | **32-38 / 54-62 ms** | 25-38 fps, 29-46 ms |
| jogo, Wi-Fi bom | 8-8,6 | **40-42** | 55-66 / 71-97 ms | 29-39 fps, 35-65 ms (6-11 KB) |
| jogo, sinal 57-67% | 8-11 | 14-23 | 74-94 / 148-212 ms | |

- Com a captura a 60 fps, o limite agora é o Wi-Fi: 380-460 KB/s na rajada.
  Nas quedas de sinal, 10-15% dos pedaços são reenviados.
- Nesta rodada o ping do início deu 9,9 ms (e 28,7 ms com select), então o
  pedido antecipado ficou em 4-5 KB, contra 2-3 KB nas outras. O tempo morto
  continuou em 3-5 ms, sem fila.
- O PC também está no Wi-Fi. O servidor agora lê a banda dele (`iw dev ...
  link`): no 2,4 GHz, o PC e o PSP dividem o mesmo canal, e a dica é usar o 5
  GHz do roteador ou cabo.

**Minecraft nesta rodada** (q90, sinal 50-100%): frames de 6-11 KB dão 29-39
fps e 35-65 ms. Nas cenas de 13-15,6 KB, 24-28 fps e 70-84 ms (p95 88-133
ms): aí a rede limita, e o decode sobe para 6-7 ms.

#### Frames P (v0.9, `--codec h264p`) [PC] [SIM] [EMU]

Com a captura a 60 fps, o que sobrou foi a rede: nas cenas de jogo, frames
de 12-18 KB a ~450 KB/s, mesmo com o PC no 5 GHz. Todo frame IDR manda a
imagem inteira a cada vez. Com frames P, vai só o que mudou.

**Tamanho [PC]** (openh264, QP 30, frames de jogo com a câmera deslizando):

| deslocamento por frame | P / IDR |
|---|---|
| 2 px | 5% |
| 8 px | 13% |
| 24 px | 51% |
| tela parada | pacote inteiro ~100 bytes |

Encode no PC: ~1,2 ms o P e ~0,5 ms as 2 cópias (pacote em ~1,5-1,8 ms).

**Como funciona** (detalhes em [Protocolo](Protocolo)):

- O decoder do PSP só solta o frame N depois do N+2 (teste v2 acima), e o
  `Stop` zera as referências. Então cada pacote leva o frame e 2 cópias (P
  sem mudança), e o PSP faz 3 chamadas: **10,6 ms de decode** no PSP-3000
  com o openh264 (teste v4 abaixo), contra 3,7 ms do IDR + Stop.
- O servidor codifica só o frame que vai mandar. O PSP decodifica todos, em
  ordem, e pula os P sem referência até chegar um IDR (pedido com a flag
  `IDR`).
- **Fila [EMU/SIM]:** a primeira versão pedia o próximo frame assim que um
  chegava, como no JPEG. Sem o descarte do frame velho, frames P pequenos
  chegam mais rápido que os 12 ms de decode e a fila cresceria (latência) até
  estourar os slots (IDR). Agora o próximo é pedido quando o decode pega o
  último da fila: ele chega enquanto o atual decodifica.
- **Frame perdido inteiro [SIM]:** um P pequeno cabe num pacote. Se ele some,
  nada chega, e o próximo frame viria sem a referência. O pedido repetido
  agora leva um NACK do frame esperado, e o servidor reenvia o mesmo. Na
  simulação com 2-5% de perda, nenhum IDR foi necessário.
- **QP [PC]:** o openh264enc ignora `qp-min/qp-max` com o pipeline rodando
  (medido: o tamanho não muda). Trocar a qualidade é refazer o encoder, que
  começa com IDR. A qualidade adaptativa entra junto de um IDR que o PSP
  pediu, ou no máximo a cada 3 s.

**Simulação [SIM]** (`tools/fake_client.py`: 450 KB/s, ida e volta de 6 ms,
decode de 5 ms no intra e 12 ms no P; servidor com um clipe de jogo
deslizando 8 px por frame, q90):

| | KB/frame | FPS | rede | recebido -> exibido |
|---|---|---|---|---|
| H.264 intra | 7,0 | 54,6 | 18,2 ms | 5,3 ms |
| **frames P** | **0,4** | **59,1** | 16,6 ms | 12,8 ms |
| intra, 2% de perda | 7,0 | 47,9 (15 frames perdidos) | 20,2 ms | 5,3 ms |
| **P, 2% de perda** | 0,5 | **50,8** (0 perdidos, 0 IDR) | 19,0 ms | 13,0 ms |

O deslize puro é o melhor caso do P (a compensação de movimento acerta tudo).
Jogo de verdade tem animação e troca de cena: os P serão maiores, e a troca
de cena custa quase um IDR. Com a imagem toda mudando, o P chega perto do
tamanho do IDR e sobra só o decode mais caro.

**Emulador [EMU]:** 400 frames P seguidos do mesmo clipe, por UDP e por TCP,
saíram sem nenhum defeito na imagem. Os tempos não valem: com frames P o PSP
fica ocioso esperando a rede, e o relógio emulado corre ~30x o real (400
frames em ~1,5 s de verdade aparecem como dezenas de segundos no overlay).

**Primeiro teste no PSP-3000 [PSP]: o PSP desligou.** Sem mais detalhes
ainda. O caminho de decode é o da v2 (3 chamadas sem Stop, cada AU com o
AUD, de um buffer alinhado), mas o stream é outro: o openh264 escreve
`level_idc` 41 (o x264 da v2: 30), POC tipo 0 com 16 bits e `frame_num` de
15 bits (x264: POC tipo 2, 4 bits). No modo intra isso nunca pesou, porque
cada frame é IDR + Stop. A sonda v4 (`psp/probe`) separa cada diferença num
passo e grava antes de cada um, então um desligamento aponta o passo.

#### Teste v4 no PSP-3000: o formato não é o problema [PSP]

60 frames por passo (testsrc2 com o número do frame; openh264 em q90). Nada
desligou:

| passo | chamadas ok | por frame mostrado | cada cópia | atraso |
|---|---|---|---|---|
| x264 + 2 cópias, nível 3.0 (v2) | 180/180 | 12,11 ms | 4,02 ms | 0 |
| x264 + 2 cópias, nível 4.1 | 180/180 | 12,12 ms | 4,02 ms | 0 |
| openh264 IDR + Stop na VRAM (`--codec h264`) | 60/60 | 3,73 ms (Stop 0,62) | | 0 |
| openh264 P, 1 chamada, nível 3.0 | 60/60 | 3,55 ms | | 2 |
| openh264 + 2 cópias, nível 3.0 | 180/180 | **10,63 ms** | 3,53 ms | 0 |
| openh264 + 2 cópias, nível 4.1 (o stream) | 180/180 | **10,63 ms** | 3,53 ms | 0 |

- O nível declarado não muda nada, e o stream do openh264 com 2 cópias
  decodifica certo e sem atraso. As cópias do openh264 (~20-80 bytes) custam
  3,5 ms, contra 4,0 ms das do x264.
- Então a causa está no que a sonda não tinha: tempo (60 frames contra
  minutos), conteúdo e perdas de verdade, ou a rede rodando junto.
- **Suspeito (descartado na v4.1):** sem IDR periódico, `frame_num` (15 bits) e o POC
  (16 bits, +2 por AU) do openh264 dão a volta no AU 32768. Com 3 AUs por
  frame mostrado, a ~60 fps, isso acontece em ~3 min. Vídeo de PSP nunca
  chega lá: cada IDR zera os dois. O servidor passou a mandar um IDR a cada
  1800 frames (30 s a 60 fps; tirado na v1.0), e a v4.1 testa a volta
  (passo 7, 12000 frames) e a volta de uma perda pelo IDR sem Stop (passo 8).

#### Teste v4.1 no PSP-3000: IDR no meio dos P desliga o PSP [PSP]

O stream desligou o PSP de verdade (ao ligar, começou do zero), em 10-20 s,
na bateria.

| passo | resultado |
|---|---|
| 7: openh264 + 2 cópias, 12000 frames sem IDR (contadores dão a volta no AU 32768) | 36000/36000 chamadas ok, 10,60 ms por frame, atraso 0 |
| 8: o mesmo com 60 frames, pula os frames 20-29 e entrega o IDR do frame 30 sem Stop | **o PSP desligou** |

- A volta de `frame_num`/POC não é o problema.
- O passo 8 imita o stream depois de uma perda: os P sem referência são
  pulados e o IDR pedido entra **sem Stop**, com o decoder ainda segurando 2
  frames. Nenhum teste anterior fazia isso: no modo intra todo IDR vem depois
  de um Stop, e nos clipes P o único IDR era o primeiro. No stream, um IDR no
  meio aparece em segundos: perda, troca de qualidade (o encoder é refeito)
  ou o IDR periódico. Bate com os 10-20 s.
- **Correção (v0.9):** `sceMpegAvcDecodeStop` antes de todo IDR que chega
  com frames P dentro do decoder. Stop + IDR é o modo intra (passo 3), e IDR
  + P com cópias é o começo de todo stream (passos 5-7): as duas metades já
  foram medidas. A sonda v4.2 testa a combinação: IDR a cada 10 frames com
  Stop antes (passo 9) e perda + Stop + IDR (passo 10).

Outras causas possíveis que a sonda não cobre:
- **Modo de espera automático:** o app não chamava `scePowerTick`, então o
  PSP podia suspender no meio do stream sem botão apertado (parece
  desligado; ao ligar de novo, volta para onde estava). Agora chama a cada
  2 s.
- **Bateria fraca:** 3 decodes por frame gastam mais.

#### Teste v4.2 e gameplay: frames P funcionando [PSP]

A sonda v4.2 testou a correção (Stop antes de todo IDR que chega com frames
P no decoder): IDR a cada 10 frames com Stop antes, e perda de 5 frames +
Stop + IDR, o mesmo cenário do passo 8 que desligava. Os dois passaram, e o
stream com `--codec h264p` (EBOOT v0.9 com a correção) rodou em gameplay sem
problemas. Ficam para medir no PSP: FPS e latência contra o `--codec h264`
na mesma cena, o `dec` no overlay (esperado ~10,6 ms) e os contadores `idr`
e `repet`.

#### openh264 direto, sem o GStreamer (v1.0) [PC]

Pelo GStreamer (appsrc -> openh264enc -> appsink), cada AU passa por duas
filas e duas threads. Com a libopenh264 chamada direto (ctypes), o mesmo
encode, imagem parada com o servidor de verdade e um pedido a cada ~5 ms:

| | pedido -> 1º pacote, mediana | p95 |
|---|---|---|
| JPEG ou H.264 já pronto (referência: ida e volta no localhost) | 0,43-0,48 ms | 0,66-0,72 ms |
| frames P pelo GStreamer | 2,83 ms | 4,17 ms |
| **frames P, openh264 direto** | **1,78 ms** | **2,67 ms** |

Por AU, com a imagem andando: frame 0,85 ms e cópia 0,30 ms com o encoder
"quente"; com 16 ms parado entre pacotes (60 fps), 1,7 e 0,5 ms (a CPU sai
do ritmo). Desligar a detecção de fundo ou de troca de cena, ou baixar a
complexidade, mudou menos que o ruído da medida, então os parâmetros ficaram
iguais aos do openh264enc: o fluxo sai idêntico, byte a byte (teste
`test_direct_matches_gstreamer`).

O ganho maior é outro: com o controle de taxa desligado, o QP de cada frame
vem do `iDLayerQp`, que o `SetOption(ENCODER_OPTION_SVC_ENCODE_PARAM_EXT)`
atualiza sem reset (`WelsEncoderParamAdjust`, ramo sem reset, conferido no
código da 2.6). A qualidade adaptativa muda sem IDR; pelo GStreamer, cada
troca refazia o encoder (IDR) e esperava até 3 s.

#### Engasgos com frames P: perda, não decode (v1.0) [PSP relato + SIM]

**Relato do PSP-3000** (Minecraft e Hollow Knight, captura KMS): bons
resultados nos dois. No Hollow Knight, o `h264p` fica em quase 60 fps com
70-150 KB/s, mas de vez em quando engasga bastante e o FPS cai; o `h264`
(só quadros completos) fica mais estável, com FPS um pouco menor e 400-450
KB/s.

**O decode não é o limite.** As 3 chamadas do pacote P custam 10,6 ms no
PSP-3000 (teste v4), e o custo é fixo por chamada: uma cópia sem nada para
decodificar custa 3,5 ms, o mesmo que um frame com conteúdo (1 chamada:
3,55 ms). Isso cabe nos 16,7 ms de um frame a 60 fps, e o próximo frame é
pedido quando o decode pega o atual, então rede e decode correm em paralelo:
com a captura a 60 fps, o frame seguinte chega ~16,7 ms depois, e o decode
já terminou. O único corte possível seria mandar menos cópias, e cada uma a
menos é um frame (16,7 ms) a mais de latência. O `dec` do overlay mostra o
tempo real; só acima de ~14 ms ele começaria a pesar.

**O que muda entre os dois modos é a perda.** No `h264`, cada quadro é
independente: um pedaço perdido estraga só aquele frame, e o seguinte já
vem a caminho (pedido antecipado). No `h264p`, cada P precisa do anterior,
então uma perda para o stream até o reenvio:

| o que se perde | v0.9 | espera |
|---|---|---|
| pedaço do meio | o último chega com buraco: NACK na hora | ~1 ida e volta |
| último pedaço (ou o frame inteiro, que costuma ser 1 pedaço) | só notado pelo silêncio | 20-50 ms + 1 ida e volta |
| o pedido, na subida | nada chega | RTO (>= 30 ms) + a resposta |

Com 1-2% de perda e 60 frames por segundo, isso é uma travada de 2-4 frames
a cada um ou dois segundos.

**Simulação** (`tools/fake_client.py`, 450 KB/s, ida e volta de 6 ms,
decode de 10,6 ms no P e 3,7 ms no intra; clipe de jogo deslizando com
ruído, P de ~1,5 KB e troca de cena a cada 2,4 s; 16 s por rodada, 2
rodadas). Engasgo = 50 ms ou mais entre dois frames exibidos.

| cenário | engasgos v0.9 | engasgos v1.0 | p99 do intervalo v0.9 / v1.0 | FPS v0.9 / v1.0 | `h264`: engasgos, FPS, KB/s |
|---|---|---|---|---|---|
| sem perda | 0-1 | 0 | 38-39 / 36-37 ms | 59,6 / 59,5 | 0, 48,6, 389 |
| 1% na descida e na subida | 9-12 | 0-1 | 49-51 / 38-39 ms | 57,1 / 59,0 | 10, 44,7, 359 |
| 2% na descida, 0,5% na subida | 4-11 | 2-3 | 43-53 / 41-42 ms | 57,0 / 58,1 | 4, 43,8, 351 |
| rajadas de 6 ms que levam tudo (0,5%) | 10-12 | 5-8 | 51-60 / 40-46 ms | 57,9 / 57,9 | 5, 45,9, 368 |

O mesmo padrão do relato: sem perda o `h264p` fica em 60 fps; com perda,
engasga mais que o `h264`, que tem menos FPS e 2-3x a banda.

**O que entrou na v1.0:**

- **Último pedaço em dobro:** o servidor manda o último pedaço de cada pacote
  P de novo 6 ms depois (`--p-redundancy-ms`). Custo medido no clipe: ~0,7
  KB por frame, ~40 KB/s a 60 fps (+27-53% sobre os P).
- **Pedido em dobro:** o PSP manda o pedido de frame novo com o número do
  frame (FRAME + NACK) e repete depois de 6 ms se nenhum pedaço chegou. O
  servidor reconhece a cópia (frame ainda não enviado: junta com o pedido
  pendente; enviado há menos de 15 ms: ignora). Um pedido perdido custa 6 ms
  em vez de >= 30.
- Atrasos de 3-4 ms caíam dentro das rajadas de 6 ms (sem ganho nelas); 9 ms
  pioravam a perda isolada. 6 ms ficou nos dois.
- O PSP não repete mais um pedido que ainda nem fez (com o próximo adiado
  para a thread de decode, o pedido "sem resposta" era falso), e enquanto
  espera a thread de decode pedir, a thread de rede olha a cada 1 ms (antes
  podia dormir até 100 ms sem saber do pedido).
- **Sem IDR periódico:** a volta de `frame_num`/POC passou no PSP (teste
  v4.1, passo 7), e o IDR a cada 30 s custava ~10 KB e uma travadinha.
- A linha do servidor mostra os engasgos com a causa provável (perda, IDR,
  pedido atrasado, captura), para o próximo teste no PSP dizer o que sobrou.

Um erro meu na simulação: o `fake_client` contava o pedido feito pelo
"decode" depois de ler o pacote que respondia a ele, ficava com um pedido
fantasma e esperava o RTO. Isso fazia o `h264p` engasgar até sem perda; o
PSP não tem esse erro (a thread de rede conta o pedido antes de ler o
pacote), e o simulador foi corrigido antes das medidas acima.

**Ainda não medido no PSP:** tudo acima é simulação, e o emulador só confere
a lógica (601 frames P por UDP e 301 por TCP sem erro; o relógio dele pula o
tempo ocioso). O que sobra no PSP real (rajadas longas, IDR depois de perdas
seguidas) aparece na linha de engasgos do servidor.

#### Frames P sem prefetch (v1.0) [PSP relato + SIM]

**Relato do PSP-3000** (Hollow Knight, KMS, EBOOT e servidor com as mudanças
acima): ficou excelente, e para isso o **prefetch precisa estar desligado**.
Com `prefetch=0`, o FPS fica perto de 60, às vezes 60 cravado. Latência não
anotada.

Com prefetch, o próximo frame é pedido quando o decode pega o atual e chega
enquanto ele decodifica: a rede e o decode andam juntos, e o FPS fica perto
de 60. Mas cada frame depende da ida e volta daquele instante, então uma
oscilação do Wi-Fi vira um frame que fica 2 ou 3 vblanks na tela entre
outros que ficam 1. Sem prefetch, o próximo só é pedido depois de exibir o
atual: um frame por vez, sem nada chegando durante o decode e sem fila, e o
ritmo depende só do ciclo pedido -> frame -> decode, que no PSP-3000 coube
em ~16,7 ms.

(**v1.1:** esse "sem prefetch" de ~60 fps era, por acaso, o pedido feito
quando o decode começa, por um sinal velho num semáforo; sem ele, o
`prefetch=0` dá ~45 fps. Ver a seção "45 ou 60 fps conforme a história".)

**A simulação errou aqui** [SIM, não bateu com o PSP]: ela previa ~30 fps
sem prefetch. No modelo, o ciclo (6 ms fixos de ida e volta + espera pela
captura do clipe + encode + transferência a 450 KB/s + 10,6 ms de decode)
passava dos 16,7 ms, e o stream ficava em um frame a cada 2 da captura. No
PSP real (PC no 5 GHz) ele coube num frame. A tabela fica como registro;
para esta decisão vale o PSP.

Simulação (mesmo cenário da tabela acima, 16 s, `fake_client --prefetch`):

| | FPS | p99 do intervalo entre frames | do frame pronto no PC à tela (sem a captura) |
|---|---|---|---|
| prefetch, sem perda | 58-60 | 37-38 ms | ~29 ms |
| sem prefetch, sem perda | ~30, constante | 47 ms (quase todos 33 ms) | ~41 ms |
| prefetch, 1% de perda | 58,7 | 38,5 ms | |
| sem prefetch, 1% de perda | 29,7 | 47,9 ms | |

No emulador, os dois modos decodificam certo (601 frames P por UDP, 301 por
TCP; o relógio dele pula o tempo ocioso, então os tempos não valem).

**v1.0: `prefetch=auto` é o padrão.** Sem prefetch com frames P, com prefetch
no JPEG e no H.264 só com quadros completos, onde ele rende 1,2-1,7x de FPS
(medido acima) e cada frame é independente. `prefetch=1` religa com frames P
(útil se o Wi-Fi for lento a ponto de o ciclo não caber num frame); SELECT +
START + X inverte durante o stream, e o overlay diz quando o próximo é
pedido.

#### Prefetch com frames P: 45 ou 60 fps conforme a história (v1.1) [PSP relato + SIM]

**Relato:** no Hollow Knight, com `prefetch=0`, perto de 45 fps; ligando e
desligando o prefetch (SELECT + START + X), vai para 60 fps e fica.

**Dois erros, achados com isso:**

- **`prefetch=0` dependia de um sinal velho.** Sem prefetch, a thread de
  rede esperava o "decode terminou" num semáforo binário. Um sinal sobrava
  quando dois frames eram publicados de uma vez (um frame completo que
  esperava o reenvio de um mais velho), ou ao ligar o prefetch (que sinaliza
  para destravar a espera). Com o sinal sobrando, cada pedido saía um frame
  antes: quando o decode pegava o frame atual, não depois de exibir. Era
  esse o modo de ~60 fps lisos; sem o sinal, depois de exibir, ~45 fps. Os
  relatos "liso e perto de 60 com o prefetch desligado" (v1.0) eram esse
  modo, por acaso.
- **Com prefetch, um pedido fantasma.** A thread de decode soltava o
  "pedido adiado" (`ask_deferred = 0`) antes de contar o pedido que fazia
  (`dec_asks++`). A thread de rede, que desde a v1.0 olha a cada 1 ms nessa
  espera, podia ver "ninguém pediu" no meio e pedir o mesmo frame de novo.
  O servidor juntava os dois num frame só, o PSP ficava contando um pedido a
  mais, o frame seguinte não era pedido, e o stream esperava o RTO (>= 30
  ms). Antes da v1.0, o pedido repetido falso fazia o mesmo. Era o engasgo
  do prefetch com frames P.

Na simulação, depois das correções (16 s, mesmo cenário das tabelas acima):

| | sem perda: FPS / engasgos / repetidos | 1% de perda: FPS / engasgos / repetidos |
|---|---|---|
| antes: `auto` com o pedido fantasma | 50-51 / 3-4 / 15-39 | 53,8 / 2 / 17 |
| `auto`: pede quando o decode começa | 55,6 / 0 / 0 | 53,7 / 1 / 0 |
| `1`: e também antes do fim do frame | 59,5 / 1 / 0 | 58,9 / 0 / 2 |
| `0`: depois de exibir | 30,8 / 0 / 0 | 30,4 / 1 / 0 |

O simulador é pessimista com a rede (ida e volta fixa de 6 ms e 450 KB/s):
nele o `0` dá 30 fps, e no PSP deu 45. **No PSP, o modo de ~60 fps lisos foi
o "pede quando o decode começa"**, e ele vira o `auto` (padrão) de
propósito: sem semáforo, sem pedido antecipado no meio de um frame. O `1`,
agora sem o pedido fantasma, foi melhor na simulação; fica para medir no
PSP (SELECT + START + X alterna auto, sim e não).

#### Som (v1.1) [PC + EMU]

**Formato (primeira versão):** IMA ADPCM do WAV (o `adpcmenc` do
GStreamer), 32 kHz estéreo, blocos de 641 amostras por canal (20,03 ms) = 648
bytes + 20 de cabeçalho + 28 de UDP/IP: **~34 KB/s e 50 pacotes/s** no ar.
Em mono ou a 22,05 kHz, metade disso ou menos. (Depois: 44,1 kHz, abaixo.)

**Fidelidade [PC]:** num sinal de teste (senos de 440 + 3000 Hz num canal,
220 Hz no outro), SNR de 34 dB contra o original. O decoder do PSP
(`psp/src/ima.c`) é o contrário exato do encoder: refazendo o encode em
Python com o estado do decoder, 0 de 30720 nibbles diferem. O `adpcmdec` do
próprio GStreamer usa a fórmula com multiplicação e difere em até 36 (de
32767) da reconstrução do encoder; a mesma SNR. O `ima.c` compilado no PC dá
as mesmas amostras da referência em Python (teste
`test_psp_decoder_matches_reference`, estéreo e mono).

**CPU no PC [PC]:** captura de teste + `audioresample` + `adpcmenc` + pacote
+ `sendto`: ~2% de um núcleo (10 s, 49,8 pacotes/s, 32,5 KB/s de dados).

**Emulador [EMU]:** o canal `sceAudioSRC` abre a 32 kHz, os pacotes chegam
inteiros (0 perdidos), o vídeo segue (401 frames P sem erro), e o atalho
desliga e liga o som (o servidor para e volta a mandar;
`tools/emu_audio_test.py`). O buffer esvazia no emulador ("vazio" 13, alvo
em 120 ms) porque o relógio emulado corre mais rápido que o real quando o
PSP está ocioso: não vale como medida.

**Atraso esperado (não medido no PSP):** leitura do PipeWire (pedaços de 10
ms) + bloco de 20 ms + rede (~5-10 ms) + buffer do PSP (começa em 40 ms,
30-120 ms) + saída (pedaços de 8 ms): ~80-100 ms no começo, menos quando o
buffer desce para 30 ms. O vídeo sai em ~30-45 ms, então o som deve chegar
um pouco depois da imagem. Para medir no PSP: o alvo e o "vazio" do
overlay depois de alguns minutos de jogo, e se o FPS do vídeo muda com o som
ligado.

#### Som no PSP-3000: zumbido "de abelha" (v1.1) [PSP relato + PC]

**Relato:** o som funcionou, mas com um zumbido que parece som de abelha.

**Causa (no código):** a thread de som tinha um buffer de saída só. No PSP,
o `sceAudioSRCOutputBlocking` volta quando o pedaço entra na fila, e o
hardware lê o buffer (DMA) enquanto toca; a thread já escrevia o pedaço
seguinte por cima. O fim de cada pedaço de 256 amostras (8 ms a 32 kHz)
saía estragado: um defeito periódico de ~125 Hz, a frequência de um
zumbido. No PPSSPP não aparece: ele copia as amostras na hora da chamada.
O `pspaudiolib` do pspsdk usa dois buffers pelo mesmo motivo. **Correção:**
dois buffers alternados, e cada pedaço sai do cache para a RAM
(`sceKernelDcacheWritebackRange`) antes de ir para o DMA. A confirmar no
PSP.

**Taxa e encoder [PC]:** 10 s de duas músicas de jogo (`triTenkemusikk.wav`
e `Monstertruck_intro`, do opentri), SNR contra o original na mesma taxa,
decodificado pelo `ima.c` do PSP compilado no PC (blocos de 1024 bytes para
comparar com o ffmpeg, que só aceita potências de 2):

| | adpcmenc (o do servidor) | ffmpeg, trellis 8 | ffmpeg, trellis 16 |
|---|---|---|---|
| tri, 32 kHz | 32,0 dB | 34,1 | 34,8 |
| tri, 44,1 kHz | 32,3 | 33,8 | 35,6 |
| Monstertruck, 32 kHz | 27,8 | 30,1 | 30,7 |
| Monstertruck, 44,1 kHz | 29,6 | 32,2 | 33,0 |

- **44,1 kHz passa a ser o padrão:** é a taxa do hardware do PSP, então o
  `sceAudioSRC` não reamostra (a conversão fica no `audioresample` do PC),
  os agudos vão até 22 kHz, e a fidelidade sobe 0,3-1,8 dB. Custa ~12 KB/s
  (~46 KB/s no total).
- Um encoder com trellis (busca a melhor sequência de nibbles em vez da
  mais próxima a cada amostra) ganharia mais 2-3 dB, com o mesmo decoder no
  PSP. Fica para depois: o `adpcmenc` não tem trellis, e o do ffmpeg exige
  blocos de potência de 2 e um processo à parte.

#### Som que não voltava depois de mexer na configuração (v1.1) [PSP relato + EMU]

**Relato:** depois de mudar qualquer opção numa sessão já aberta e
aplicar, o som dava erro e só voltava reiniciando o app.

**Causa:** o canal `sceAudioSRC` só é solto (`sceAudioSRCChRelease`) com a
fila de saída vazia; com som na fila, a chamada falha. O retorno era
ignorado, o canal ficava reservado, e o stream seguinte (depois da tela de
configuração, ou desligando e ligando o som) não conseguia reservá-lo: o
`sceAudioSRCChReserve` falhava (0x80268002). O PPSSPP tem a mesma regra, e
o `tools/emu_audio_test.py`, que antes só olhava o log do servidor, agora
confere o log do PSP e reproduziu a falha. **Correção:** antes de soltar,
espera a fila esvaziar (`sceAudioOutput2GetRestSample`, < 30 ms) e tenta de
novo por até ~100 ms; ao reservar, se falhar, solta e tenta mais uma vez.
No emulador: desligar e ligar (SELECT + START + cima) e voltar da tela de
configuração reabrem o canal, sem nenhuma falha. A confirmar no PSP.

#### Frames P: janela de 2 frames e `--fps` exato (v1.1) [PSP relato + SIM]

**Relato** (frames P, `prefetch=auto`, Wi-Fi abaixo de 150 KB/s, ou seja, a
banda não é o limite): com `--fps 40`, o PSP raramente fica em 40, fica
por volta de 35 com quedas; sem limite, 52-55 em vez de 60, também com
quedas de vez em quando.

**Causa 1: o pedido corria contra a captura.** No `auto`, o PSP pedia o
N+1 quando o decode pegava o N, e o servidor só manda um frame pedido. O
pedido tinha de ir, e o frame ser codificado, antes da captura seguinte
(16,7 ms a 60 fps): ida e volta + encode + transferência davam ~12 ms, e
qualquer oscilação do Wi-Fi atrasava o frame ou fazia o servidor pular uma
captura. O simulador com ida e volta fixa não mostrava isso; com a ida e
volta oscilando (4 ms + exponencial de média 3 ms, `--rtt-jitter-ms`), deu
53-55 fps, como no PSP. **Correção: a janela.** O pedido feito no decode
autoriza até o N+2 (FRAME + NACK "até o frame F"; o servidor guarda o
crédito, no máximo 2 à frente) e o frame sai na hora da captura. Um frame P
que se perde inteiro é notado quando o seguinte chega (buraco na numeração),
e o PSP pede o reenvio na hora, em vez de um IDR.

**Causa 2: o limite de `--fps` cortava frames.** O `RateLimiter` contava a
vez do frame seguinte a partir do frame que chegou, com 25% de tolerância.
Um frame atrasado empurrava a vez dos seguintes, e o seguinte, no horário,
era cortado. Fonte de 60 Hz com o horário de cada frame tremendo (normal de
desvio σ), 100 s:

| | limite antigo | grade fixa (v1.1) |
|---|---|---|
| `--fps 60`, σ 2 ms / 3 ms | 59,0 / 55,5 | 60,0 / 59,9 |
| `--fps 40`, σ 2 ms / 3 ms | 39,0 / 38,3 | 40,0 / 40,0 |
| `--fps 50`, σ 0-3 ms | 45,7-48,0 | 50,0 |
| fonte de 75 Hz, `--fps 60` | 54,5-56,2 | 59,9-60,0 |
| fonte de 144 Hz, `--fps 60`, σ 3 ms | 58,7 | 60,0 |
| `--fps 30` | 30,0 | 30,0 |

A grade conta cada vez a partir da anterior; um frame passa se chega até
meio período antes da vez dele (meio período da fonte, se ela for mais
rápida), e a grade só recomeça quando fica para trás (fonte parada ou mais
lenta). O pior intervalo entre frames também caiu (fonte de 60 Hz,
`--fps 60`, σ 3 ms: 43 → 38 ms).

**Simulação** (`fake_client`, clipe de jogo com movimento, 450 KB/s, decode de
10,6 ms, ida e volta de 4 ms + oscilação de 3 ms, 12 s):

| `--fps` | sem a janela | janela de 2 |
|---|---|---|
| 60 | 53-55 fps | 59,4-60 fps |
| 60, 2% de perda (3 rodadas) | 52,5-53,7 fps, 5 engasgos (287 ms) | 59,0-59,7 fps, 4 engasgos (328 ms) |
| 40 | 39,9 | 39,8-39,9 |
| 30 | 29,9 | 29,9 |

A latência não muda (rede ~17-25 ms nos dois). Sem a janela, o PSP também
repetia cada pedido depois de 6 ms (~60 pacotes/s a mais na subida); com
ela, não precisa (o pedido seguinte cobre um perdido).

**40 de 60 Hz não é uniforme.** A tela só tem frames a cada 16,7 ms, então
`--fps 40` é 2 de cada 3: intervalos de 17 e 33 ms, sempre. Somado ao tempo
de transferência, que varia com o tamanho de cada frame, alguns intervalos
na tela passam de 50 ms (na simulação, p99 de ~52 ms com e sem a janela).
Para um movimento uniforme: `--fps 30` (um sim, um não) ou 60.

**No PSP, a medir:** FPS com `--fps 60` e `--fps 40` (o overlay e a linha
`fonte` do servidor, que mostra a taxa da captura depois do limite).

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
