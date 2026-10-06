Primeiro, `python3 server/pspstream.py --check` (ou `pspstream --check`):
ele lista o que falta e o comando para a sua distribuição. No Ubuntu, veja
também o [Guia do Ubuntu](Guia-do-Ubuntu#10-problemas-comuns-no-ubuntu); no
Wolf, a tabela da página [Wolf](Wolf#10-problemas).

| sintoma | o que fazer |
|---|---|
| "Sem resposta do PC" / "Procurar" não acha | o servidor está rodando? Libere 5123/udp e 5123/tcp no firewall (`--check` diz o comando). PC e PSP na mesma rede, sem isolamento de clientes no roteador |
| algo falta ou não abre | `--check`: lista o que falta e o comando para a sua distribuição |
| latência alta, FPS oscilando | desligue a Economia de energia WLAN do PSP; deixe o PC no 5 GHz ou no cabo (o servidor avisa se ele divide o canal de 2,4 GHz com o PSP); roteador em modo misto b/g/n |
| engasgos de vez em quando (h264p) | confira o prefetch (4ª linha do overlay; o padrão `auto` diz "ate 2 frames a frente quando o decode comeca"). Depois, veja os engasgos e a causa na [linha do servidor](Opções-do-servidor#a-linha-de-estatística). `perda`/`pedido atrasado`: Wi-Fi (distância, canal de 2,4 GHz cheio, micro-ondas, Bluetooth); `captura`: o PC; `IDR` frequente: perdas seguidas. `--codec h264` aguenta perdas melhor (cada quadro é independente), com 2-3x mais banda |
| h264p liso, mas com FPS bem abaixo de 60 | com `prefetch=0`, cada frame espera o anterior ser exibido (~45 fps): use `auto`. Veja a `fonte` na linha do servidor: abaixo de 60, é a captura (`--source kms`). Até a v1.1, `auto` ficava em 52-55 com o Wi-Fi oscilando (o pedido chegava depois da captura seguinte) e o limite de `--fps` cortava frames de uma fonte com horários tremidos: atualize servidor e EBOOT |
| `--fps 40` não dá 40 | até a v1.1, o limite de `--fps` cortava frames com os horários da captura tremendo (~38) e o PSP pulava capturas (~35): atualize. 40 de uma tela de 60 Hz alterna 17 e 33 ms; para um movimento uniforme, `--fps 30` |
| som some depois de mexer na configuração ("som: erro no canal de audio") | EBOOT 1.1 antes da correção: o canal de som não era solto com som na fila. Atualize o EBOOT |
| captura em ~38-40 fps no GNOME 50 | use `--source kms` ([Instalação](Instalação#captura-kms-recomendada-no-gnome-50)) |
| KMS: "sem permissão para ler a tela" | a mensagem diz qual auxiliar e o comando. Pacote: `sudo setcap cap_sys_admin+ep /usr/libexec/pspstream/pspstream-kms`; repositório: `make -C tools/kms cap` (de novo depois de cada `make`). Confira com `getcap` no mesmo arquivo: deve mostrar `cap_sys_admin=ep`. Com a permissão lá e o erro continuando: a partição está montada com `nosuid`, ou o servidor roda num terminal de Flatpak (como o do VS Code), num container ou num toolbox, onde ela não vale (a mensagem diz qual) |
| controles não chegam | o log diz "controles desativados": configure o `/dev/uinput` ([Instalação](Instalação#controles-uinput)) |
| sem som | o log do servidor diz `som: ...` ao iniciar: sem `pulsesrc`/`adpcmenc`, instale `gstreamer1-plugins-good` e `gstreamer1-plugins-bad-free`. No PSP, a 5ª linha do overlay: "desligado" = SELECT + START + cima; "esperando o PC" = o servidor não está mandando. Só pelo UDP |
| som picotando | `vazio` subindo no overlay: Wi-Fi oscilando (o buffer aumenta sozinho até 120 ms). `--audio-rate 32000`, `22050` ou `--audio-mono` aliviam a rede |
| zumbido no som (EBOOT 1.1 antes da correção) | era o buffer de saída reaproveitado enquanto tocava; atualize o EBOOT |
| o PC continua tocando o som | o servidor grava o que sai nas caixas. Para o som ir só para o PSP: `pactl load-module module-null-sink sink_name=psp`, escolha "Null Output" como saída nas configurações de som e rode o servidor com `--audio-device psp.monitor` |
| o servidor avisa "EBOOT antigo" ou "não aceita frames P" | atualize o EBOOT (v1.0 ou mais novo) |
| imagem torta ou com cores erradas (JPEG) | `decoder=sw` no `server.txt` |
| algo estranho no H.264 do PC | `--h264-encoder gstreamer` usa o caminho antigo; mande o log |
| a interface web diz "endereço não permitido" | use o IP ou `localhost`, ou libere o nome com `--web-allow-host` ([Interface web](Interface-web#proteções)) |
