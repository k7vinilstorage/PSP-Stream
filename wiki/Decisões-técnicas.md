Por que o PSPStream é feito do jeito que é. Os números por trás de cada
escolha estão em [Medições](Medições), e o formato das mensagens, em
[Protocolo](Protocolo).

- **Modelo "pull"** (a ideia central do RNDS-Stream). O PSP pede um frame e
  o servidor responde com o mais recente, codificado na hora. Nunca se forma
  fila na rede, e a latência fica perto de um frame.
- **Servidor em Python, o trabalho pesado em C.** Captura, redução,
  conversão de cor e codificação são GStreamer, openh264 e OpenGL (C/C++,
  GPU); o auxiliar KMS é C. O Python só costura: lê pedidos, chama o
  encoder e corta o frame em pacotes. Medido a 60 fps: as threads de Python
  usam 3-6% de um núcleo, e do pedido do PSP ao 1º pacote (JPEG/H.264 já
  pronto) são 0,43-0,48 ms no localhost, o mesmo de um ping. Reescrever em C
  ou Rust economizaria uns 20-30 MB de RAM e nada perceptível de latência.
- **openh264 chamado direto.** Pelo GStreamer (appsrc -> openh264enc ->
  appsink), cada um dos 3 AUs do pacote P passava por duas filas e duas
  threads, e o QP só mudava refazendo o encoder (um IDR). Pela libopenh264
  direto (ctypes): pedido -> 1º pacote de 2,8 para 1,8 ms, e a qualidade
  muda sem IDR. O fluxo é o mesmo, byte a byte; se a biblioteca faltar ou o
  layout dela não bater, volta para o GStreamer sozinho.
- **Frames P com 2 cópias.** O decoder do PSP só solta o frame N depois do
  N+2, e o `sceMpegAvcDecodeStop`, que solta na hora, zera as referências.
  Cada pacote leva o frame e 2 cópias (P sem mudança, ~20-80 bytes): o PSP
  faz 3 chamadas e mostra o frame real, sem atraso. O Stop só entra antes de
  um IDR, e é obrigatório ali (sem ele, o PSP desliga).
- **UDP (padrão) e TCP, os dois no modelo pull.** No PSP-3000, o Wi-Fi perde
  1-3% dos pacotes; no TCP, cada perda com um frame em trânsito vira um
  timeout de retransmissão, e o vídeo **e os controles** travavam por
  centenas de ms. No UDP, um pedaço perdido volta pelo NACK, e um frame
  inteiro perdido, pelo pedido repetido.
- **Pedido antecipado na medida.** O PSP pede o próximo frame quando o que
  falta do atual leva uma ida e volta para chegar (ping do início x vazão,
  ~2-3 KB no PSP-3000). Valores fixos maiores, que pareciam bons na
  simulação, criavam fila no roteador no PSP real.
- **Frames P pedidos quando o decode começa (`prefetch=auto`).** O próximo
  chega enquanto o atual decodifica, sem nunca ter dois frames na fila e sem
  pedir no meio de um frame chegando. ~60 fps lisos no PSP-3000. Pedir só
  depois de exibir dava ~45 fps. No UDP, o pedido autoriza até 2 frames à
  frente (v1.1): o servidor guarda o crédito e manda cada frame na hora da
  captura, sem esperar a ida e volta daquele pedido.
- **Perdas nos frames P.** Cada P precisa do anterior, então perder um
  pacote para o stream até o reenvio. O servidor manda o último pedaço de
  cada frame P de novo 6 ms depois (perder o último só era notado pelo
  silêncio). Sem a janela, o PSP repete o pedido de frame novo depois de 6
  ms, com o número do frame para o servidor reconhecer a cópia; com ela, o
  pedido seguinte cobre um perdido, e um frame que some inteiro é notado
  quando o seguinte chega (o PSP pede o reenvio na hora).
- **`--fps` numa grade fixa.** O limite conta a vez de cada frame a partir
  da vez anterior, não do frame que chegou: um frame atrasado pela captura
  não empurra os seguintes, e a taxa sai exata (o limite anterior cortava
  frames com os horários tremendo 2-3 ms).
- **Som em IMA ADPCM, empurrado.** 4 bits por amostra (~46 KB/s a 44,1 kHz
  estéreo, a taxa do PSP: o PSP não reamostra), codificado em C pelo
  `adpcmenc` (~2% de um núcleo no PC), e decodificado no CPU do PSP com
  somas e deslocamentos. MP3 ou ATRAC pesariam menos na rede, mas somariam
  50-100 ms de atraso e disputariam o Media Engine com o H.264. O som não
  depende do pedido de vídeo (uma travada no vídeo não corta o som), e vai
  em pacotes de 20 ms: com 10 ms, seriam 100 pacotes por segundo disputando
  o ar do 802.11b com o vídeo.
- **GStreamer em vez de ffmpeg** para a captura: o portal entrega PipeWire,
  que o GStreamer lê nativamente, e tudo roda dentro do processo.
- **Escrita direta no framebuffer** (stride 512), sem sceGu: os decoders
  escrevem na VRAM, sem cópias.
- **Prioridades de thread no PSP**: o decode fica abaixo da pilha TCP/IP,
  senão a rede para durante o decode.
- **`-lpspnet_inet` e `-lpsputility` fora do `LIBS`**: o psp-gcc já os
  acrescenta; listados duas vezes, os stubs se dividem e o carregador lê
  NIDs errados.
- **Wolf só pela API.** O `--source wolf` não muda nada no Wolf: cria uma
  sessão própria pelo socket da API e recebe a imagem e o som por TCP
  local. O porquê de cada parte está em [Wolf por dentro](Wolf-por-dentro).
