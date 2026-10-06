Com o servidor rodando, abra **http://localhost:5124** no PC. A página
mostra o estado (PSP conectado, FPS no PSP e da captura, latência, Wi-Fi,
engasgos, qualidade), as configurações gerais e o log. No Docker, ao lado
do Wolf, veja também [Wolf](Wolf#7-interface-web-no-servidor-ou-na-rede).

## O que dá para mudar

| grupo | o que muda |
|---|---|
| Captura | fonte (portal, kms, x11, test, static, wolf), monitor do KMS, janela e cursor do portal, alvo e conversão do Wolf, limite de FPS, filtro de redução, esticar |
| Vídeo | codec, qualidade adaptativa ou fixa, alvo e limites da adaptativa |
| Som | ligado, fonte (o que sai nas caixas, tom de teste ou uma fonte do PipeWire), taxa, mono |
| Controles | ligados, perfil, velocidade do mouse |
| Rede | prioridade no Wi-Fi (DSCP), cópia do último pedaço, porta |

Cada campo diz quando a mudança vale:

- **na hora**: qualidade, limite de FPS, DSCP;
- **refaz a captura**, com o PSP continuando conectado: fonte, codec,
  filtro. A captura nova sobe antes de a velha parar; se não subir (ex.: KMS
  sem o auxiliar), a velha continua e a página mostra o motivo. Com frames P,
  o primeiro frame da captura nova é um IDR;
- **refaz o som** ou **os controles** (as teclas seguradas são soltas);
- **na próxima conexão do PSP** (cópia do último pedaço) ou **ao reiniciar o
  servidor** (porta: mude também o `server.txt` do PSP).

## Onde fica gravado

As mudanças ficam em `~/.config/pspstream/server.json` (só o que difere do
padrão; `--config` escolhe outro arquivo; no Docker, no volume
`pspstream-config`). Na partida, a ordem é padrão < arquivo < linha de
comando: uma opção dada na linha de comando vale mais que o arquivo, e a
página marca esses campos com "linha de comando". Se a captura gravada no
arquivo não subir, o servidor usa a da linha de comando e avisa no log, e a
página continua acessível para trocar.

## Na rede local, com senha

Por padrão, a página só abre no próprio PC (127.0.0.1). `--web
0.0.0.0:5124` (ou a variável `PSPSTREAM_WEB`) abre para a rede local, do
celular, por exemplo. Com a variável `PSPSTREAM_WEB_PASSWORD`, o navegador
pede uma senha (o usuário pode ser qualquer um), com 1 s de espera a cada
senha errada; sem ela, qualquer um na rede muda as configurações. A senha
vai em HTTP, sem criptografia: serve para a rede de casa. Libere a porta
5124/tcp no firewall. `--no-web` desliga a página.

## Proteções

- A página recusa endereços que não conhece, contra DNS rebinding: aceita
  `localhost`, o nome do PC (e `nome.local`) e qualquer IP. Outros nomes,
  como um do DNS do roteador, vão em `--web-allow-host` ou
  `PSPSTREAM_WEB_HOSTS` (separados por vírgula).
- Pedidos POST só com `Content-Type: application/json` e com Origin, se
  houver, igual ao endereço: outro site aberto no navegador não consegue
  mudar nada.
- Nada nela recebe caminho de arquivo nem pipeline do GStreamer
  (`--source gst` e `--image` só pela linha de comando).

O código está em
[`server/web.py`](https://github.com/k7vinilstorage/PSP-Stream/blob/main/server/web.py).
