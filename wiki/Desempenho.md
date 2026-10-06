PSP-3000 e Fedora 44 com Wi-Fi 802.11b. Os detalhes e o histórico de cada
número estão em [Medições](Medições).

| | FPS | latência média | KB/frame |
|---|---|---|---|
| MJPEG (v0.4), imagem fixa q50 / q90 | 20 / 11 | 46 / 90 ms | 13-35 |
| H.264 só quadros completos (v0.8), imagem fixa q50 / q90 | 69 / 61 | 21 / 26 ms | 2,4 / 5,4 |
| H.264, Minecraft pelo portal (fonte ~38 fps) | 35-37 | 30-38 ms | 5-9 |
| H.264 + captura KMS, cenas leves / jogo | 43-56 / 40-42 | 32-38 / 55-66 ms | 4 / 8-9 |
| H.264 com frames P (v0.9), Hollow Knight + KMS (relato) | quase 60, com engasgos de vez em quando | não medida | 1,2-2,5 (70-150 KB/s, contra 400-450 KB/s do H.264 só quadros completos) |
| H.264 com frames P (v1.0), Hollow Knight + KMS (relato), pedido quando o decode começa | **perto de 60, liso** ("excelente") | não medida | |
| o mesmo, pedido depois de exibir (`prefetch=0` de verdade) | ~45 | não medida | |

- Decode no PSP: JPEG 7,9 ms (hardware); H.264 só quadros completos 3,7 ms;
  frame P + 2 cópias 10,6 ms.
- No PC, o pacote P sai 1,8-2,7 ms depois do pedido (openh264 direto).
- Nas cenas de jogo, a rede é o limite: o 802.11b do PSP entrega 380-460
  KB/s na prática. Os frames P existem para isso.
- O decode dos frames P (10,6 ms) cabe nos 16,7 ms de um frame a 60 fps e
  roda em paralelo com a chegada do próximo. Os engasgos do h264p vinham das
  perdas no Wi-Fi (cada P precisa do anterior, então uma perda parava o
  stream até o reenvio); a v1.0 manda o último pedaço em dobro e repete o
  pedido, e a linha do servidor mostra o que sobrou e por quê
  ([Opções do servidor](Opções-do-servidor#a-linha-de-estatística)). Com o
  próximo frame pedido quando o decode começa (`prefetch=auto`), o stream
  fica liso e perto de 60 fps no PSP-3000.
