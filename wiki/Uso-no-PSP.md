Primeiro uso, a tela de configuração, os atalhos durante o stream, o overlay
e o `server.txt`. Como instalar: [Instalação](Instalação).

## Primeiro uso

No PC:

```sh
python3 server/pspstream.py --source kms --profile xbox    # ou: pspstream --source kms --profile xbox
```

No PSP, abra o PSPStream. Na primeira vez não há IP configurado, e a tela de
configuração espera: escolha **Procurar o PC na rede** (X) e depois aperte
**START** (salva e conecta). Nas próximas vezes, ela conecta sozinha em 3 s.

Sem `--source kms`, a captura é pelo portal: na primeira vez o GNOME/KDE
pergunta qual monitor ou janela transmitir, e a escolha fica salva
(`--forget` pergunta de novo).

As configurações do PC também mudam pelo navegador, em
**http://localhost:5124** (ver [Interface web](Interface-web)).

## Tela de configuração

Aparece ao abrir (conecta sozinha em 3 s se o IP já existe; qualquer botão
para a contagem), com **SELECT + START + R** durante o stream e com
**START** quando o Wi-Fi ou o PC não respondem.

- **Cima/Baixo** escolhe o item, **Esq/Dir** muda o valor.
- **IP do PC** e **Porta**: X entra na edição dígito a dígito (Esq/Dir
  escolhe o dígito, Cima/Baixo muda, X termina).
- **Perfil de Wi-Fi**: mostra o nome salvo no XMB.
- **Procurar o PC na rede**: liga o Wi-Fi e manda um ping em broadcast; o
  servidor responde e o IP dele entra no lugar.
- Transporte, H.264, frames P, decoder, vsync, overlay, controles, som,
  prefetch (auto/sim/não) e os ajustes de rede: as mesmas opções do
  [`server.txt`](#servertxt).
- **START** grava o `server.txt` e conecta; **O** conecta sem gravar. Ao
  gravar, os comentários do arquivo antigo somem.

## Atalhos durante o stream

Segure **SELECT + START** e aperte:

| botão | faz |
|---|---|
| triângulo | liga/desliga o overlay |
| quadrado | decoder do JPEG hardware/software |
| círculo | vsync |
| cima | liga/desliga o som (o PC para de mandar quando desliga) |
| X | prefetch: auto -> sim -> não (ver [Prefetch](#prefetch)) |
| L | troca o transporte TCP/UDP (reconecta) |
| R | abre a tela de configuração |

Enquanto o atalho estiver segurado, nada é enviado ao PC. O SELECT apertado
sozinho antes do START chega ao PC.

## Overlay

```
 41.3 fps   1.2 KB  52 KB/s
dec 10.6 ms (h264p) rede 9.8 ms udp drop 0
perdidos 0 nack 1 repet 0 idr 0 ping 7.1 ms (min 5.2, ini 6.3 sel)
pede ate 2 frames a frente quando o decode comeca (auto)
```

A 4ª linha diz quando o próximo frame é pedido: quando o decode começa,
autorizando até 2 à frente (frames P com `prefetch=auto`; o frame sai do PC
na hora da captura), quando faltam tantos KB do atual (pedido
antecipado: JPEG e H.264 só com quadros completos, ou `prefetch=1`) ou
depois de exibir (`prefetch=0`). A 5ª é o som:

```
som 44.1 kHz buf 38 ms (alvo 40) perdidos 0 vazio 0 pulos 0
```

`buf` é o som recebido esperando para tocar, e `alvo`, quanto o PSP tenta
manter (começa em 40 ms; sobe 10 ms a cada vez que o buffer esvazia,
`vazio`, e desce 5 ms a cada 10 s sem faltar, entre 30 e 120 ms). `perdidos`
são pacotes que não chegaram (viram 20 ms de silêncio) e `pulos`, som
descartado porque acumulou demais (o atraso não cresce).

`h264p` = H.264 com frames P (`h264`: só quadros completos; `hw`/`sw`:
JPEG). `idr` conta os quadros completos pedidos depois de uma perda, e
`repet`, os pedidos repetidos por falta de resposta (pedido ou frame inteiro
perdido, ou tela parada). `nack` conta os pedidos de pedaços que faltavam.

## `server.txt`

Fica em `ms0:/PSP/GAME/PSPStream/server.txt`. Tudo pode ser mudado na tela
de configuração. À mão:

```
192.168.1.100        # IP do PC (opcional :porta, padrão 5123)
wifi_profile=1       # perfil de rede do XMB
transport=udp        # udp (padrão) | tcp
h264=1               # aceita H.264
h264p=1              # aceita H.264 com frames P
decoder=auto         # JPEG: auto (hardware com reserva em software) | hw | sw
vsync=1              # 1 = sem rasgo na imagem (+0 a 16 ms); 0 = troca imediata
overlay=1            # FPS, KB/frame, tempos
input=1              # controles do PSP -> PC
audio=1              # som do PC (só pelo UDP); 0 = o PC nem manda
prefetch=auto        # auto (padrão) | 1 | 0 (ver abaixo)
early_kb=auto        # UDP: pede o próximo frame quando faltar isso do atual (auto = ida e volta x vazão; 0 = no fim)
rxwait=auto          # UDP: auto | select | poll
rcvbuf=64            # buffer de recepção do socket (KB)
bench=0              # 1 = mede o decode JPEG hw x sw no próprio PSP ao conectar
menu_wait=3          # s com a tela de configuração aberta antes de conectar sozinho (0 = direto)
```

O PSP só aceita o IP em números (sem nomes de DNS).

### Prefetch

Prefetch é pedir o próximo frame antes de terminar o atual, para rede e
decode trabalharem juntos:

| `prefetch=` | JPEG e H.264 só com quadros completos | frames P |
|---|---|---|
| `auto` (padrão) | pede antes do fim do frame que chega (1,2-1,7x de FPS, medido no PSP-3000) | pede quando o decode pega o atual: **~60 fps lisos** no PSP-3000 (Hollow Knight). No UDP, autoriza até 2 frames à frente (v1.1): o frame sai na hora da captura |
| `1` | igual | também pede antes do fim do frame que chega (pedido antecipado) |
| `0` | pede depois de exibir o atual | pede depois de exibir o atual: ~45 fps no mesmo teste |

SELECT + START + X troca entre os três durante o stream, e o overlay mostra
qual está valendo. Até a v1.0, o `0` dava 45 ou 60 fps conforme a história
(um sinal velho fazia ele pedir quando o decode começava), e o `1` com frames
P engasgava por um erro na contagem dos pedidos; os dois foram corrigidos
na v1.1.

A janela de 2 frames (v1.1): com só o seguinte autorizado, o pedido tinha de
ir e o frame ser codificado antes da captura seguinte (16,7 ms a 60 fps);
com o Wi-Fi oscilando, o servidor perdia capturas e o PSP-3000 ficava em
52-55 fps com a fonte a 60. Com 2 à frente, o pedido já está esperando no PC
(simulação: 53-55 → 59,5-60 fps, mesma latência). Na fila do PSP fica no
máximo um frame pronto a mais, e só se a rede entregar dois de uma vez.
