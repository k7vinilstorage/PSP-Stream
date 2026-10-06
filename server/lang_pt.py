"""Mensagens em português. A chave é o texto em inglês do código (tr() e
N_(); veja i18n.py). Ao criar ou mudar uma mensagem, acrescente a tradução
aqui: tests/test_i18n.py confere que nenhuma falta."""

PT = {
    # server/adaptive.py
    'quality %d -> %d (target %.1f KB, average %.1f KB, throughput %.0f KB/s, decode %.1f ms)':
        'qualidade %d -> %d (alvo %.1f KB, média %.1f KB, vazão %.0f KB/s, decode %.1f ms)',
    # server/audio.py
    'a {size}-byte block does not fit in a packet: lower --audio-ms':
        'bloco de {size} bytes não cabe num pacote: diminua --audio-ms',
    'end of stream (EOS)':
        'fim do stream (EOS)',
    'audio stopped: %s (video goes on)':
        'som parou: %s (o vídeo continua)',
    "could not capture the audio from '{device}'":
        "não foi possível capturar o som de '{device}'",
    # server/control.py
    'expected an object':
        'esperava um objeto',
    'unknown setting':
        'configuração desconhecida',
    'web interface: %s':
        'interface web: %s',
    'could not save {path}: {error}':
        'não foi possível gravar {path}: {error}',
    'could not start the capture: {error}':
        'não foi possível iniciar a captura: {error}',
    'the new capture failed: {error}':
        'a captura nova falhou: {error}',
    'capture: %s, %s, up to %d fps':
        'captura: %s, %s, até %d fps',
    'off':
        'desligados',
    'disabled: {error}':
        'desativados: {error}',
    'controls disabled: %s':
        'controles desativados: %s',
    'the capture did not open: {error}':
        'a captura não abriu: {error}',
    'audio disabled: %s':
        'som desativado: %s',
    'turned off in the web interface':
        'desligado na interface web',
    'audio off':
        'som desligado',
    'controls off':
        'controles desligados',
    # server/distro.py
    'comes from the fedora-cisco-openh264 repository, already enabled on Fedora Workstation':
        'vem do repositório fedora-cisco-openh264, já ativo no Fedora Workstation',
    'comes from the codecs.opensuse.org repository (enabled on Tumbleweed)':
        'vem do repositório codecs.opensuse.org (ativo no Tumbleweed)',
    'install it with the package manager (see https://github.com/k7vinilstorage/PSP-Stream/wiki/Installation)':
        'instale pelo gerenciador de pacotes (veja https://github.com/k7vinilstorage/PSP-Stream/wiki/Installation)',
    # server/gamepad.py
    'unknown target in the keymap (gamepad): {names}; use {valid}':
        'destino desconhecido no keymap (gamepad): {names}; use {valid}',
    'unknown PSP button in {where}: {name}':
        'botão do PSP desconhecido em {where}: {name}',
    'python-evdev is not installed ({hint})':
        'python-evdev não instalado ({hint})',
    'no access to /dev/uinput ({error}); see "Controls (uinput)" on the Installation page of the PSPStream wiki':
        'sem acesso a /dev/uinput ({error}); veja "Controls (uinput)" na página Installation da wiki do PSPStream',
    'unknown shift button: {name}':
        'botão de shift desconhecido: {name}',
    'analog.stick must be left, right, dpad or none (got {value})':
        'analog.stick deve ser left, right, dpad ou none (veio {value})',
    'gamepad: nothing from the PSP for %.0f ms, back to neutral':
        'controle: PSP sem mandar nada há %.0f ms, voltando ao neutro',
    'gamepad %s = %d':
        'controle %s = %d',
    # server/gst_source.py
    'capture format: %s':
        'formato da captura: %s',
    'median interval {median:.1f} ms, p10 {p10:.1f}, p90 {p90:.1f}':
        'intervalo mediano {median:.1f} ms, p10 {p10:.1f}, p90 {p90:.1f}',
    'no intervals':
        'sem intervalos',
    'capture: the source delivers %.1f fps (%s); through the %d fps limit: %.1f; encoded: %.1f':
        'captura: a fonte entrega %.1f fps (%s); passam pelo limite de %d fps: %.1f; codificados: %.1f',
    'the H.264 encoder did not return the frame':
        'o encoder H.264 não devolveu o frame',
    'could not start the GStreamer pipeline':
        'não foi possível iniciar o pipeline GStreamer',
    'could not convert {path}':
        'não consegui converter {path}',
    # server/h264.py
    'could not start {encoder}':
        'não foi possível iniciar o {encoder}',
    '{encoder} did not return the frame':
        'o {encoder} não devolveu o frame',
    "H.264: %s; using GStreamer's openh264enc":
        'H.264: %s; usando o openh264enc do GStreamer',
    "H.264: %s; switching to GStreamer's openh264enc":
        'H.264: %s; passando para o openh264enc do GStreamer',
    'SPS with a zero before level_idc':
        'SPS com zero antes do level_idc',
    'H.264: libopenh264 %s called directly':
        'H.264: libopenh264 %s chamada direto',
    # server/inject.py
    "profile '{profile}' does not exist in {path} (available: {names})":
        "perfil '{profile}' não existe em {path} (disponíveis: {names})",
    'key %s %s':
        'tecla %s %s',
    'pressed':
        'pressionada',
    'released':
        'solta',
    'unknown button in the keymap: {name}':
        'botão desconhecido no keymap: {name}',
    'unknown codes in the keymap: {codes}':
        'códigos desconhecidos no keymap: {codes}',
    'controls: nothing from the PSP for %.0f ms, releasing everything':
        'controles: PSP sem mandar nada há %.0f ms, soltando tudo',
    # server/jpeginfo.py
    '{size} is larger than {max}':
        '{size} é maior que {max}',
    'progressive JPEG (must be baseline)':
        'JPEG progressivo (precisa ser baseline)',
    '{sampling} sampling (sceJpeg needs 4:2:0)':
        'amostragem {sampling} (sceJpeg exige 4:2:0)',
    'not a JPEG (no SOI marker)':
        'não é um JPEG (falta o marcador SOI)',
    'invalid marker at byte {offset}':
        'marcador inválido no byte {offset}',
    'SOF header not found':
        'cabeçalho SOF não encontrado',
    # server/kms.py
    'invalid reply from the helper: {magic}':
        'resposta inválida do auxiliar: {magic}',
    'the helper {path} is missing: build it with make -C tools/kms and then make -C tools/kms cap (asks for the sudo password), or install the PSPStream package':
        'falta o auxiliar {path}: compile com make -C tools/kms e depois make -C tools/kms cap (pede a senha do sudo), ou instale o pacote do PSPStream',
    'the helper did not introduce itself (status {status})':
        'o auxiliar não se apresentou (status {status})',
    'the helper did not answer':
        'o auxiliar não respondeu',
    'the helper exited (code {code})':
        'o auxiliar saiu (código {code})',
    '{size}-byte reply from the helper (expected {expected})':
        'resposta de {size} bytes do auxiliar (esperado {expected})',
    'no permission to read the screen: {problem}':
        'sem permissão para ler a tela: {problem}',
    'KMS: the framebuffer has no modifier; treating it as linear':
        'KMS: o framebuffer não informa o modificador; tratando como linear',
    'KMS: the screen changed to %dx%d; restart the server for the right aspect ratio':
        'KMS: a tela mudou para %dx%d; reinicie o servidor para a proporção certa',
    'KMS: format %s, %dx%d, %d plane(s)':
        'KMS: formato %s, %dx%d, %d plano(s)',
    'KMS capture: {problem}':
        'captura KMS: {problem}',
    # server/netcheck.py
    "the PC is on Wi-Fi ({iface}): if you can, use a cable, or the router's 5 GHz network. On the same channel, every packet crosses the air twice and the PSP's bandwidth drops":
        'o PC está no Wi-Fi ({iface}): se der, use cabo, ou a rede de 5 GHz do roteador. No mesmo canal, cada pacote cruza o ar duas vezes e a banda do PSP cai',
    "PC on {ghz:.1f} GHz Wi-Fi ({iface}): good, it does not compete for the PSP's 2.4 GHz channel":
        'PC no Wi-Fi de {ghz:.1f} GHz ({iface}): bom, ele não disputa o canal de 2,4 GHz do PSP',
    "the PC is on 2.4 GHz Wi-Fi ({iface}, channel {channel}), the same as the PSP: every packet crosses the same channel twice and the PSP's bandwidth is halved. Use a cable, or connect the PC to the router's 5 GHz network (the PSP stays on 2.4). With one network name for both bands: nmcli connection modify <network> 802-11-wireless.band a":
        'o PC está no Wi-Fi de 2,4 GHz ({iface}, canal {channel}), o mesmo do PSP: cada pacote cruza o mesmo canal duas vezes e a banda do PSP cai pela metade. Use cabo, ou conecte o PC na rede de 5 GHz do roteador (o PSP continua no 2,4). Com um nome de rede só para as duas bandas: nmcli connection modify <rede> 802-11-wireless.band a',
    "the PC's Wi-Fi power save is ON: the router holds the PSP's requests until the card wakes up. Turn it off: sudo iw dev %s set power_save off (until reboot) or nmcli connection modify <network> 802-11-wireless.powersave 2 (permanent)":
        'power save do Wi-Fi do PC LIGADO: o roteador segura os pedidos do PSP até a placa acordar. Desligue: sudo iw dev %s set power_save off (até reiniciar) ou nmcli connection modify <rede> 802-11-wireless.powersave 2 (permanente)',
    "check the PC's Wi-Fi power save: iw dev %s get power_save":
        'confira o power save do Wi-Fi do PC: iw dev %s get power_save',
    # server/openh264.py
    'libopenh264 not found ({errors})':
        'libopenh264 não encontrada ({errors})',
    'openh264 {version} is too old (needs 2.x)':
        'openh264 {version} antiga demais (precisa da 2.x)',
    'WelsCreateSVCEncoder failed':
        'WelsCreateSVCEncoder falhou',
    'GetDefaultParams failed':
        'GetDefaultParams falhou',
    'unexpected SEncParamExt layout in openh264 {version}: {fields}':
        'layout de SEncParamExt inesperado na openh264 {version}: {fields}',
    'InitializeExt refused the parameters':
        'InitializeExt recusou os parâmetros',
    'SetOption(SVC_ENCODE_PARAM_EXT) refused the new QP':
        'SetOption(SVC_ENCODE_PARAM_EXT) recusou o QP novo',
    'I420 of {size} bytes, expected {expected}':
        'I420 de {size} bytes, esperava {expected}',
    'EncodeFrame failed':
        'EncodeFrame falhou',
    'the encoder skipped the frame (type {type})':
        'o encoder pulou o frame (tipo {type})',
    'inconsistent output from openh264 {version} ({size} bytes, the library says {expected})':
        'saída inconsistente na openh264 {version} ({size} bytes, a biblioteca diz {expected})',
    # server/paths.py
    '{path} is on a partition mounted with nosuid, which ignores the permission: use the package or a clone of the repository on another partition':
        '{path} está numa partição montada com nosuid, que ignora a permissão: use o pacote ou um clone do repositório em outra partição',
    "the server runs with no_new_privs (in a Flatpak terminal, like VS Code's, or in a container), and the kernel ignores the helper's permission: run the server in a regular terminal":
        'o servidor roda com no_new_privs (num terminal de Flatpak, como o do VS Code, ou num container), e o kernel ignora a permissão do auxiliar: rode o servidor num terminal comum',
    'the helper {path} lacks the permission: run {fix} (again after every make)':
        'o auxiliar {path} está sem a permissão: rode {fix} (de novo depois de cada make)',
    'the helper {path} lacks the permission: run {fix}':
        'o auxiliar {path} está sem a permissão: rode {fix}',
    'the helper {path} has the permission, but the kernel did not return the image (in a container or toolbox, it does not apply outside of it)':
        'o auxiliar {path} tem a permissão, mas o kernel não devolveu a imagem (num container ou toolbox, ela não vale fora dele)',
    # server/portal.py
    'no D-Bus session bus: {error}':
        'sem barramento D-Bus de sessão: {error}',
    'ScreenCast portal unavailable: {error}':
        'portal ScreenCast indisponível: {error}',
    '{method} failed: {error}':
        '{method} falhou: {error}',
    '{method}: no answer in {seconds} s':
        '{method}: sem resposta em {seconds} s',
    'capture cancelled in the dialog':
        'captura cancelada no diálogo',
    '{method} refused by the portal (code {code})':
        '{method} recusado pelo portal (código {code})',
    'choose the monitor/window in the system dialog (if it shows up)...':
        'escolha o monitor/janela no diálogo do sistema (se aparecer)...',
    'the portal returned no stream':
        'o portal não devolveu nenhum stream',
    'PipeWire: node %d, %s':
        'PipeWire: nó %d, %s',
    'size ?':
        'tamanho ?',
    # server/protocol.py
    "the PSP's EBOOT speaks an old protocol version (v{old}, this server speaks v{new}): build and copy this version's EBOOT.PBP":
        'o EBOOT do PSP é de uma versão antiga do protocolo (v{old}, este servidor fala v{new}): compile e copie o EBOOT.PBP desta versão',
    'short request: {size} bytes (expected {expected})':
        'pedido curto: {size} bytes (esperado {expected})',
    'invalid magic in the request: {magic}':
        'magic inválido no pedido: {magic}',
    'invalid magic in the frame: {magic}':
        'magic inválido no frame: {magic}',
    'invalid magic in the chunk: {magic}':
        'magic inválido no pedaço: {magic}',
    'invalid magic in the audio: {magic}':
        'magic inválido no som: {magic}',
    # server/sources.py
    'static image: raw I420, encoded on every send (P frames)':
        'imagem estática: I420 cru, codificada a cada envio (frames P)',
    'static image: %s':
        'imagem estática: %s',
    'static image: %dx%d, %.1f KB':
        'imagem estática: %dx%d, %.1f KB',
    'static image: H.264, %.1f KB':
        'imagem estática: H.264, %.1f KB',
    # server/stats.py
    'loss':
        'perda',
    'late request':
        'pedido atrasado',
    'capture':
        'captura',
    ' (source {fps:4.1f})':
        ' (fonte {fps:4.1f})',
    '{fps:5.1f} fps{source} | {kb:5.1f} KB/frame | Wi-Fi {wifi:5.0f} KB/s | latency {latency:5.1f} ms (p95 {p95:5.1f}) ~ capture {capture:4.1f} + age {age:4.1f} + network {network:5.1f} + psp {psp:5.1f} | network = 1st chunk {first:4.1f} + burst {burst:4.1f} ms ({burst_kbps:4.0f} KB/s) | decode {decode:4.1f} ms | wait for a new frame {wait:4.1f} ms':
        '{fps:5.1f} fps{source} | {kb:5.1f} KB/frame | Wi-Fi {wifi:5.0f} KB/s | latência {latency:5.1f} ms (p95 {p95:5.1f}) ~ captura {capture:4.1f} + idade {age:4.1f} + rede {network:5.1f} + psp {psp:5.1f} | rede = 1º pedaço {first:4.1f} + rajada {burst:4.1f} ms ({burst_kbps:4.0f} KB/s) | decode {decode:4.1f} ms | espera por frame novo {wait:4.1f} ms',
    ' | dead time {ms:+5.1f} ms':
        ' | tempo morto {ms:+5.1f} ms',
    ' (early by {kb:.1f} KB)':
        ' (antecipa {kb:.1f} KB)',
    ' | in-stream ping {ms:4.1f} ms (min {min:4.1f})':
        ' | ping no stream {ms:4.1f} ms (mín {min:4.1f})',
    ' | {n} resends (still screen)':
        ' | {n} reenvios (tela parada)',
    ' | {pct:.1f}% UDP chunks resent':
        ' | {pct:.1f}% pedaços UDP reenviados',
    ' | {n} frames lost':
        ' | {n} frames perdidos',
    ' | audio {kbps:.0f} KB/s':
        ' | som {kbps:.0f} KB/s',
    ' | hitches {n} (worst {ms:.0f} ms: {causes})':
        ' | engasgos {n} (pior {ms:.0f} ms: {causes})',
    # server/transports.py
    'the PSP closed the connection':
        'PSP fechou a conexão',
    'DSCP not applied: %s':
        'DSCP não aplicado: %s',
    'the PSP did not answer for %.0f s':
        'PSP ficou %.0f s sem responder',
    'reading ended: %s':
        'leitura terminou: %s',
    # server/web.py
    'invalid port':
        'porta inválida',
    "address not allowed (use http://localhost, the PC's IP or a name allowed with --web-allow-host)":
        'endereço não permitido (use http://localhost, o IP do PC ou um nome liberado com --web-allow-host)',
    'web: wrong password from %s':
        'web: senha errada vinda de %s',
    'password':
        'senha',
    'missing file {name}':
        'faltou o arquivo {name}',
    'not found':
        'não existe',
    'origin not allowed':
        'origem não permitida',
    'empty or too large request':
        'pedido vazio ou grande demais',
    'invalid JSON':
        'JSON inválido',
    'expected {"values": {...}}':
        'esperava {"values": {...}}',
    # server/wolf_api.py
    'the Wolf API socket does not exist: {path} (in the Wolf service: WOLF_SOCKET_PATH and the /var/run/wolf volume; here: --wolf-socket)':
        'o socket da API do Wolf não existe: {path} (no serviço do Wolf: WOLF_SOCKET_PATH e o volume /var/run/wolf; aqui: --wolf-socket)',
    'no permission to open {path} (Wolf creates the socket as root; see the Wolf page of the PSPStream wiki)':
        'sem permissão para abrir {path} (o Wolf cria o socket como root; veja a página Wolf da wiki do PSPStream)',
    'nobody answers at {path}: is Wolf running?':
        'ninguém atende em {path}: o Wolf está rodando?',
    '{where}: Wolf did not answer in {seconds:g} s':
        '{where}: o Wolf não respondeu em {seconds:g} s',
    '{where}: reply that is not JSON (HTTP {status}): {text}':
        '{where}: resposta que não é JSON (HTTP {status}): {text}',
    'failed':
        'falhou',
    'POST /api/v1/sessions/add: the reply has no session_id':
        'POST /api/v1/sessions/add: a resposta não trouxe o session_id',
    # server/wolf_input.py
    'controls: virtual Xbox controller plugged into Wolf session %s':
        'controles: controle de Xbox virtual ligado na sessão %s do Wolf',
    'Wolf did not take the controller: {error}':
        'o Wolf não recebeu o controle: {error}',
    'controls: unplugging the controller in Wolf: %s':
        'controles: desligando o controle no Wolf: %s',
    'controls: %s':
        'controles: %s',
    # server/capture.py
    'codec: JPEG (%s)':
        'codec: JPEG (%s)',
    'no openh264: {hint}':
        'sem o openh264: {hint}',
    '--size other than 480x272':
        '--size diferente de 480x272',
    '--codec {codec} needs openh264: {hint}':
        '--codec {codec} precisa do openh264: {hint}',
    '--codec {codec} only works at 480x272 (the PSP decoder writes the whole screen)':
        '--codec {codec} só funciona em 480x272 (o decoder do PSP escreve a tela inteira)',
    "codec: H.264 (every frame IDR, the PSP's hardware decoder)":
        'codec: H.264 (todo frame IDR, decoder de hardware do PSP)',
    'codec: H.264 with P frames (encoded when sending; EBOOT v0.9+, otherwise IDR only)':
        'codec: H.264 com frames P (codificado na hora de enviar; EBOOT v0.9+, senão só IDR)',
    'virtual Xbox 360 controller':
        'controle de Xbox 360 virtual',
    'keyboard and mouse':
        'teclado e mouse',
    "controls: profile '%s' (%s)%s":
        "controles: perfil '%s' (%s)%s",
    "controls: profile '%s' is keyboard and mouse; through Wolf the controls go as an Xbox controller: using the xbox profile (or --profile xbox-camera, xbox-shoulders)":
        "controles: o perfil '%s' é de teclado e mouse; pelo Wolf os controles vão como um controle de Xbox: usando o perfil xbox (ou --profile xbox-camera, xbox-shoulders)",
    'virtual Xbox controller in Wolf':
        'controle de Xbox virtual no Wolf',
    ', with the {name} profile':
        ', com o perfil {name}',
    "controls: profile '%s' (%s; the PSP session joins the lobby)%s":
        "controles: perfil '%s' (%s; a sessão do PSP entra no lobby)%s",
    '--dmabuf: the portal did not report the screen size; the image may come out stretched':
        '--dmabuf: o portal não disse o tamanho da tela; a imagem pode sair esticada',
    '--source gst needs --gst-src "<GStreamer elements>"':
        '--source gst precisa de --gst-src "<elementos GStreamer>"',
    '--audio-device wolf only works with --source wolf':
        '--audio-device wolf só vale com --source wolf',
    "GStreamer's pulsesrc and adpcmenc are missing ({hint})":
        'faltam o pulsesrc e o adpcmenc do GStreamer ({hint})',
    'audio: %s, %d Hz %s, IMA ADPCM in %.0f ms packets (~%.0f KB/s when the PSP asks)':
        'som: %s, %d Hz %s, IMA ADPCM em pacotes de %.0f ms (~%.0f KB/s quando o PSP pede)',
    'stereo':
        'estéreo',
    "GStreamer's adpcmenc is missing ({hint})":
        'falta o adpcmenc do GStreamer ({hint})',
    'the Wolf capture is not running':
        'a captura do Wolf não está rodando',
    "audio: from Wolf (the target's audio, through the PSPStream session), %d Hz %s, IMA ADPCM in %.0f ms packets (~%.0f KB/s when the PSP asks)":
        'som: do Wolf (o som do alvo, pela sessão do PSPStream), %d Hz %s, IMA ADPCM em pacotes de %.0f ms (~%.0f KB/s quando o PSP pede)',
    'no frame in {seconds} s':
        'nenhum frame em {seconds} s',
    'capture: DMA-BUF + scaling on the GPU (OpenGL), --dmabuf':
        'captura: DMA-BUF + redução na GPU (OpenGL), --dmabuf',
    '--dmabuf did not work (%s); falling back to capturing through regular memory':
        '--dmabuf não funcionou (%s); voltando para a captura pela memória comum',
    # server/settings.py
    'applies right away':
        'vale na hora',
    'restarts the capture (the PSP stays connected)':
        'refaz a captura (o PSP continua conectado)',
    'restarts the audio capture':
        'refaz a captura do som',
    'restarts the controls':
        'refaz os controles',
    "applies on the PSP's next connection":
        'vale na próxima conexão do PSP',
    'applies when the server restarts':
        'vale ao reiniciar o servidor',
    'General':
        'Geral',
    'Language':
        'Idioma',
    'Language of this page and of the server messages (log, --check). English is the default.':
        'Idioma desta página e das mensagens do servidor (log, --check). O padrão é inglês.',
    'Capture':
        'Captura',
    'Source':
        'Fonte',
    'portal: the screen through the Wayland portal (asks for permission the first time). kms: straight from the graphics card, 60 fps on GNOME 50 (the helper needs permission to read the screen: --setup gives it). x11: X11 session. test: animated pattern. static: still image. wolf: what runs in Wolf (Games on Whales); shows up when the Wolf API socket exists.':
        'portal: a tela pelo portal do Wayland (pede permissão na primeira vez). kms: direto da placa de vídeo, 60 fps no GNOME 50 (o auxiliar precisa da permissão de ler a tela: o --setup a dá). x11: sessão X11. test: padrão animado. static: imagem fixa. wolf: o que roda no Wolf (Games on Whales); aparece quando o socket da API do Wolf existe.',
    'Monitor (KMS)':
        'Monitor (KMS)',
    '0 = the first connected monitor; the log says how many there are.':
        '0 = o primeiro monitor ligado; o log diz quantos há.',
    'Capture a window':
        'Capturar uma janela',
    'The portal asks for a window instead of a monitor.':
        'O portal pergunta qual janela, em vez de um monitor.',
    'Hide the cursor':
        'Esconder o cursor',
    'Target in Wolf':
        'Alvo no Wolf',
    'Lobby id or name, or session id. Empty = the only open lobby (with several, the log lists the options).':
        'Id ou nome do lobby, ou id da sessão. Vazio = o único lobby aberto (com vários, o log lista as opções).',
    'Conversion in Wolf':
        'Conversão no Wolf',
    'How Wolf brings the image down from the GPU: nvidia (CUDA), va (Intel/AMD) or cpu (Wolf without zero-copy). auto tries them in that order; the log says which one worked.':
        'Como o Wolf desce a imagem da GPU: nvidia (CUDA), va (Intel/AMD) ou cpu (Wolf sem zero-copy). auto tenta nessa ordem; o log diz qual funcionou.',
    'FPS limit':
        'Limite de FPS',
    'On a 60 Hz screen, 30 is every other frame (even); 40 alternates 17 and 33 ms intervals.':
        'Numa tela de 60 Hz, 30 é um frame sim, um não (uniforme); 40 alterna intervalos de 17 e 33 ms.',
    'Scaling filter':
        'Filtro de redução',
    'bilinear2 (default) does not alias and makes smaller frames; lanczos makes text a little sharper, ~2 ms more.':
        'bilinear2 (padrão) não serrilha e gera frames menores; lanczos deixa o texto um pouco mais nítido, com ~2 ms a mais.',
    'Stretch to the PSP screen':
        'Esticar para a tela do PSP',
    'No black bars; the image loses its aspect ratio.':
        'Sem as bordas pretas; a imagem perde a proporção.',
    'Video':
        'Vídeo',
    'Codec':
        'Codec',
    'auto = h264p with openh264. h264p: H.264 with P frames, ~10x fewer bytes. h264: every frame complete, copes better with losses at 2-3x the bandwidth. jpeg: without openh264.':
        'auto = h264p com o openh264. h264p: H.264 com frames P, ~10x menos bytes. h264: todo frame completo, aguenta perdas melhor com 2-3x mais banda. jpeg: sem openh264.',
    'Adaptive quality':
        'Qualidade adaptativa',
    'Adjusts the quality to the measured Wi-Fi bandwidth.':
        'Ajusta a qualidade à banda medida do Wi-Fi.',
    'Quality':
        'Qualidade',
    '1-100. With adaptive quality, it is the starting one.':
        '1-100. Com a adaptativa, é a inicial.',
    'FPS the bandwidth must sustain':
        'FPS que a banda tem de sustentar',
    'Adaptive: lower = more quality and more time per frame.':
        'Adaptativa: menor = mais qualidade e mais tempo por frame.',
    'Minimum quality':
        'Qualidade mínima',
    'Maximum quality':
        'Qualidade máxima',
    'Audio':
        'Som',
    'PC audio':
        'Som do PC',
    'The PSP also turns it on and off (SELECT + START + up). UDP only.':
        'O PSP também liga e desliga (SELECT + START + cima). Só pelo UDP.',
    'Audio source':
        'Fonte do som',
    "monitor = what plays on the speakers (with the wolf source, Wolf's audio); test = 440 Hz tone; or a PipeWire source.":
        'monitor = o que sai nas caixas (com a fonte wolf, o som do Wolf); test = tom de 440 Hz; ou uma fonte do PipeWire.',
    'Rate (Hz)':
        'Taxa (Hz)',
    "44100 is the PSP's (no resampling). ~46 KB/s in stereo.":
        '44100 é a do PSP (sem reamostrar). ~46 KB/s em estéreo.',
    'Mono':
        'Mono',
    'Half the bytes.':
        'Metade dos bytes.',
    'Controls':
        'Controles',
    'PSP controls on the PC':
        'Controles do PSP no PC',
    'Needs access to /dev/uinput (PSPStream wiki, Installation).':
        'Precisa de acesso ao /dev/uinput (wiki do PSPStream, Installation).',
    'Profile':
        'Perfil',
    'game, desktop, arrows: keyboard and mouse. xbox*: virtual Xbox 360 controller.':
        'game, desktop, arrows: teclado e mouse. xbox*: controle de Xbox 360 virtual.',
    'Mouse speed':
        'Velocidade do mouse',
    "Multiplies the profile's (keyboard and mouse profiles).":
        'Multiplica a do perfil (perfis de teclado e mouse).',
    'Network':
        'Rede',
    'Wi-Fi priority (DSCP)':
        'Prioridade no Wi-Fi (DSCP)',
    'ef = WMM voice queue (default); cs5/af41 = video; 0 = none.':
        'ef = fila de voz do WMM (padrão); cs5/af41 = vídeo; 0 = nenhuma.',
    'Copy of the last chunk (ms)':
        'Cópia do último pedaço (ms)',
    'P frames: the last chunk of each frame is sent again after this; 0 turns it off.':
        'Frames P: o último pedaço de cada frame vai de novo depois disso; 0 desliga.',
    'Port':
        'Porta',
    'TCP and UDP. On the PSP, the same in server.txt.':
        'TCP e UDP. No PSP, a mesma no server.txt.',
    'use true or false':
        'use verdadeiro ou falso',
    'invalid number':
        'número inválido',
    'use a whole number':
        'use um número inteiro',
    'the minimum is {min:g}':
        'o mínimo é {min:g}',
    'the maximum is {max:g}':
        'o máximo é {max:g}',
    'invalid option ({options})':
        'opção inválida ({options})',
    'invalid text':
        'texto inválido',
    'invalid source name (letters, digits and . : @ + - _)':
        'nome de fonte inválido (letras, números e . : @ + - _)',
    'up to 100 characters, no control characters':
        'até 100 caracteres, sem caracteres de controle',
    'unknown type: {kind}':
        'tipo desconhecido: {kind}',
    'the minimum is above the maximum':
        'a mínima passa da máxima',
    'settings %s ignored: %s':
        'configuração %s ignorada: %s',
    'settings %s ignored: not a JSON object':
        'configuração %s ignorada: não é um objeto JSON',
    "settings %s: '%s' does not exist, ignored":
        "configuração %s: '%s' não existe, ignorada",
    'invalid option':
        'opção inválida',
    'settings %s: %s = %r ignored (%s)':
        'configuração %s: %s = %r ignorada (%s)',
    'settings %s: %s ignored (%s)':
        'configuração %s: %s ignorada (%s)',
    # server/pspstream.py
    'PSP connected over %s: %s:%d':
        'PSP conectado via %s: %s:%d',
    'sending failed: %s':
        'envio falhou: %s',
    'PSP disconnected (%d frames, %.1f MB sent%s)':
        'PSP desconectado (%d frames, %.1f MB enviados%s)',
    ', {kb:.0f} KB of JPEG headers saved':
        ', {kb:.0f} KB de cabeçalho JPEG economizados',
    'the PSP left':
        'PSP saiu',
    'the PSP started the stream':
        'PSP iniciou o stream',
    'repeated HELLO (the PSP thought the stream stopped)':
        'HELLO repetido (PSP achou que o stream parou)',
    'the PSP does not decode H.264 (EBOOT older than v0.5, or h264=0 in server.txt): update the EBOOT or run the server with --codec jpeg':
        'o PSP não decodifica H.264 (EBOOT anterior à v0.5, ou h264=0 no server.txt): atualize o EBOOT ou rode o servidor com --codec jpeg',
    'pure PSP <-> PC round trip (small packet, idle network): %s':
        'ida e volta pura PSP <-> PC (pacote pequeno, rede parada): %s',
    'audio: turned off on the PSP':
        'som: desligado no PSP',
    'audio: the PSP asked, but the server has no audio (--no-audio, or the capture did not open)':
        'som: o PSP pediu, mas o servidor está sem som (--no-audio, ou a captura não abriu)',
    'audio: turned on on the PSP (%d Hz, %s, ~%.0f KB/s)':
        'som: ligado no PSP (%d Hz, %s, ~%.0f KB/s)',
    "the PSP's WLAN power save holds the packets at the router and raises the latency a lot: turn it off in Settings > Power Save Settings":
        'a economia de energia WLAN do PSP segura os pacotes no roteador e aumenta muito a latência: desligue em Ajustes > Ajustes de economia de energia',
    'a %d KB frame exceeds the %d KB limit; dropped':
        'frame de %d KB excede o limite de %d KB; descartado',
    'H.264: P frames (IDR only when the PSP asks; quality changes without IDR)':
        'H.264: frames P (IDR só quando o PSP pede; a qualidade muda sem IDR)',
    'H.264: P frames (IDR only when the PSP asks; new quality at most every %.0f s)':
        'H.264: frames P (IDR só quando o PSP pede; qualidade nova no máximo a cada %.0f s)',
    "the PSP's EBOOT does not take P frames (older than v0.9, or h264p=0 in server.txt): sending every frame as IDR":
        'o EBOOT do PSP não aceita frames P (anterior à v0.9, ou h264p=0 no server.txt): mandando todo frame IDR',
    'IDR requested by the PSP':
        'IDR pedido pelo PSP',
    'benchmark: quality %d (%.0f s)':
        'benchmark: qualidade %d (%.0f s)',
    '| q | KB/frame | FPS | source (fps) | Wi-Fi (KB/s) | average latency (ms) | p95 (ms) | network (ms) | 1st chunk (ms) | in-stream ping (ms) | burst (ms) | burst throughput (KB/s) | wait for a new frame (ms) | dead time between frames (ms) | early request (KB) | decode (ms) | PSP received->shown (ms) | resends 1 s | chunks resent | frames lost |':
        '| q | KB/frame | FPS | fonte (fps) | Wi-Fi (KB/s) | latência média (ms) | p95 (ms) | rede (ms) | 1º pedaço (ms) | ping no stream (ms) | rajada (ms) | vazão na rajada (KB/s) | espera por frame novo (ms) | tempo morto entre frames (ms) | pedido antecipado (KB) | decode (ms) | PSP recebido->exibido (ms) | reenvios 1 s | pedaços reenviados | frames perdidos |',
    '{first:.1f} (min {min:.1f}, median {median:.1f}) | ':
        '{first:.1f} (mín {min:.1f}, mediana {median:.1f}) | ',
    '{ping:.1f} (min {min:.1f}) | ':
        '{ping:.1f} (mín {min:.1f}) | ',
    'at the end':
        'no fim',
    'Pure round trip (ping at the start of the stream): {ping}':
        'Ida e volta pura (ping no início do stream): {ping}',
    'Source: {source} {width}x{height}, codec: {codec}, transport: {transport}':
        'Fonte: {source} {width}x{height}, codec: {codec}, transporte: {transport}',
    'benchmark done, table saved to %s:\n%s':
        'benchmark concluído, tabela salva em %s:\n%s',
    '{ms:.1f} ms waiting with select()':
        '{ms:.1f} ms esperando com select()',
    '{ms:.1f} ms polling the socket':
        '{ms:.1f} ms consultando o socket',
    '; PSP using {mode}':
        '; PSP usando {mode}',
    'polling':
        'consulta',
    'PSP Wi-Fi: signal %d%%, WLAN power save ON':
        'Wi-Fi do PSP: sinal %d%%, economia de energia WLAN LIGADA',
    'PSP Wi-Fi: signal %d%%, WLAN power save off':
        'Wi-Fi do PSP: sinal %d%%, economia de energia WLAN desligada',
    'PSP Wi-Fi: signal {signal}%, WLAN power save ON':
        'Wi-Fi do PSP: sinal {signal}%, economia de energia WLAN LIGADA',
    'PSP Wi-Fi: signal {signal}%, WLAN power save off':
        'Wi-Fi do PSP: sinal {signal}%, economia de energia WLAN desligada',
    'JPEG header cache: on; DSCP: {dscp}':
        'Cache do cabeçalho JPEG: ligado; DSCP: {dscp}',
    'JPEG header cache: off; DSCP: {dscp}':
        'Cache do cabeçalho JPEG: desligado; DSCP: {dscp}',
    'use WIDTHxHEIGHT, e.g. 480x272':
        'use LARGURAxALTURA, ex.: 480x272',
    'the PSP shows at most 480x272':
        'o PSP exibe no máximo 480x272',
    'the PIN is digits only':
        'o PIN são só dígitos',
    'PSPStream: streams the PC screen to the PSP (H.264 or MJPEG).':
        'PSPStream: transmite a tela do PC para o PSP (H.264 ou MJPEG).',
    'language of the messages and of the web interface: en (default) or pt. Default also in PSPSTREAM_LANG':
        'idioma das mensagens e da interface web: en (padrão) ou pt. Padrão também em PSPSTREAM_LANG',
    'TCP and UDP port (default %(default)s)':
        'porta TCP e UDP (padrão %(default)s)',
    'h264p: H.264 with P frames, ~10x fewer bytes per frame (EBOOT v0.9+; an old EBOOT gets every frame as IDR). h264: every frame IDR (EBOOT v0.5+). auto (default) = h264p if openh264 is installed, otherwise jpeg':
        'h264p: H.264 com frames P, ~10x menos bytes por frame (EBOOT v0.9+; um EBOOT antigo recebe todo frame IDR). h264: todo frame IDR (EBOOT v0.5+). auto (padrão) = h264p se o openh264 estiver instalado, senão jpeg',
    "P frames and still image: auto (default) = libopenh264 directly, with GStreamer's openh264enc as a fallback; openh264 or gstreamer force one of them":
        'frames P e imagem estática: auto (padrão) = libopenh264 direto, com o openh264enc do GStreamer de reserva; openh264 ou gstreamer forçam um dos dois',
    'UDP: limit the rate the chunks are sent at (0 = no limit, default)':
        'UDP: limitar a taxa de envio dos pedaços (0 = sem limite, padrão)',
    'P frames over UDP: the last chunk of each frame is sent again after MS ms, and losing it does not stall the stream waiting for the NACK (default %(default).0f; 0 = off)':
        'frames P por UDP: o último pedaço de cada frame vai de novo depois de MS ms, e a perda dele não para o stream esperando o NACK (padrão %(default).0f; 0 = desliga)',
    'UDP: send the JPEG header with every frame (to compare; the default sends it only when it changes)':
        'UDP: mandar o cabeçalho JPEG em todo frame (para comparar; o padrão manda só quando muda)',
    "marking of the server's packets for the Wi-Fi priority queue (WMM): ef = voice (default), cs5/af41 = video, 0 = none":
        'marcação dos pacotes do servidor para a fila de prioridade do Wi-Fi (WMM): ef = voz (padrão), cs5/af41 = vídeo, 0 = nenhuma',
    'local address (default %(default)s)':
        'endereço local (padrão %(default)s)',
    "portal = screen on Wayland (default); kms = straight from the graphics card, without GNOME 50's ~40 fps limit (the helper needs permission to read the screen: --setup gives it); test = animated pattern with a clock; x11 = X11 session; gst = your own pipeline (--gst-src); static = an image; wolf = what runs in Wolf (Games on Whales), through its API (PSPStream wiki, Wolf page)":
        'portal = tela no Wayland (padrão); kms = direto da placa de vídeo, sem o limite de ~40 fps do GNOME 50 (o auxiliar precisa da permissão de ler a tela: o --setup a dá); test = padrão animado com relógio; x11 = sessão X11; gst = pipeline próprio (--gst-src); static = uma imagem; wolf = o que roda no Wolf (Games on Whales), pela API dele (wiki do PSPStream, página Wolf)',
    'kms: which connected monitor (0 = the first; the log shows how many there are)':
        'kms: qual monitor ligado (0 = o primeiro; o log mostra quantos há)',
    'kms: graphics card (default: searches all of them)':
        'kms: placa de vídeo (padrão: procura em todas)',
    'image for the static mode (default: assets/testcard.jpg)':
        'imagem do modo static (padrão: assets/testcard.jpg)',
    'GStreamer elements of the source for --source gst':
        'elementos GStreamer da fonte para --source gst',
    'PATH':
        'CAMINHO',
    'wolf: the Wolf API socket (default: WOLF_SOCKET_PATH or %(default)s). It gives full control of Wolf: mount it only in the PSPStream container and never expose it over TCP':
        'wolf: socket da API do Wolf (padrão: WOLF_SOCKET_PATH ou %(default)s). Ele dá controle total do Wolf: monte-o só no container do PSPStream e nunca o exponha por TCP',
    'wolf: what to mirror: lobby id or name, or session id (default: the only open lobby; with several, the log lists the options). Default also in PSPSTREAM_WOLF_TARGET':
        'wolf: o que espelhar: id ou nome do lobby, ou id da sessão (padrão: o único lobby aberto; com vários, o log lista as opções). Padrão também em PSPSTREAM_WOLF_TARGET',
    'auto|nvidia|va|cpu|ELEMENTS':
        'auto|nvidia|va|cpu|ELEMENTOS',
    "wolf: how Wolf brings the image down to regular memory at 480x272. nvidia = CUDA (Wolf's default with NVIDIA); va = Intel/AMD; cpu = Wolf with WOLF_USE_ZERO_COPY=FALSE; auto (default) tries them in that order. Or GStreamer elements that deliver I420 at the sent resolution. Default also in PSPSTREAM_VIDEO_CONVERT":
        'wolf: como o Wolf desce a imagem para a memória comum em 480x272. nvidia = CUDA (o padrão do Wolf com NVIDIA); va = Intel/AMD; cpu = Wolf com WOLF_USE_ZERO_COPY=FALSE; auto (padrão) tenta nessa ordem. Ou elementos GStreamer que entreguem I420 na resolução enviada. Padrão também em PSPSTREAM_VIDEO_CONVERT',
    'DIGITS':
        'DÍGITOS',
    "wolf: the lobby's PIN, if it asks for one (for the controls to join the lobby). Default: PSPSTREAM_WOLF_PIN":
        'wolf: PIN do lobby, se ele pede (para os controles entrarem no lobby). Padrão: PSPSTREAM_WOLF_PIN',
    'PORT':
        'PORTA',
    "wolf: Wolf's UDP port for the video ping (default: WOLF_VIDEO_PING_PORT or %(default)s)":
        'wolf: porta UDP do ping de vídeo do Wolf (padrão: WOLF_VIDEO_PING_PORT ou %(default)s)',
    "wolf: Wolf's UDP port for the audio ping (default: WOLF_AUDIO_PING_PORT or %(default)s)":
        'wolf: porta UDP do ping de som do Wolf (padrão: WOLF_AUDIO_PING_PORT ou %(default)s)',
    'sent resolution (default 480x272)':
        'resolução enviada (padrão 480x272)',
    'maximum capture rate (default %(default)s). Capturing above what the PSP shows makes the sent frame younger':
        'taxa máxima de captura (padrão %(default)s). Capturar acima do que o PSP exibe reduz a idade do frame enviado',
    'JPEG quality 1-100: starting (adaptive) or fixed (--fixed-quality). Default %(default)s':
        'qualidade JPEG 1-100: inicial (adaptativo) ou fixa (--fixed-quality). Padrão %(default)s',
    'do not adapt the quality to the measured bandwidth':
        'não adaptar a qualidade à banda medida',
    'adaptive: FPS the bandwidth must sustain (default %(default)s, measured on the PSP-3000: ~q55 at ~48 ms; 30 is out of reach on 802.11b and drops the quality to the minimum). Lower = more quality and more latency per frame':
        'adaptativo: FPS que a banda precisa sustentar (padrão %(default)s, medido no PSP-3000: ~q55 com ~48 ms; 30 é inalcançável no 802.11b e derruba a qualidade para o mínimo). Menor = mais qualidade e mais latência por frame',
    'adaptive: minimum quality (default %(default)s)':
        'adaptativo: qualidade mínima (padrão %(default)s)',
    'adaptive: maximum quality (default %(default)s)':
        'adaptativo: qualidade máxima (padrão %(default)s)',
    'scaling filter. bilinear2 (default) does not alias and makes frames ~27%% smaller than bilinear; lanczos = slightly sharper text, ~2 ms more':
        'filtro de redução. bilinear2 (padrão) não serrilha e gera frames ~27%% menores que bilinear; lanczos = texto um pouco mais nítido, ~2 ms a mais',
    'stretch instead of keeping the aspect ratio':
        'esticar em vez de manter a proporção',
    'portal: choose a window instead of a monitor':
        'portal: escolher uma janela em vez de um monitor',
    'portal, experimental: receive the screen in GPU memory (DMA-BUF) and scale it to 480x272 in OpenGL; only the small image comes to the CPU. If it does not work, it falls back to the normal mode on its own':
        'portal, experimental: receber a tela na memória da GPU (DMA-BUF) e reduzir para 480x272 no OpenGL; só a imagem pequena vem para a CPU. Se não funcionar, volta sozinho para o modo normal',
    'portal: do not draw the cursor':
        'portal: não desenhar o cursor',
    'portal: do not reuse/save the screen choice':
        'portal: não reutilizar/guardar a escolha de tela',
    'do not capture or send the audio':
        'não capturar nem mandar o som',
    'NAME':
        'NOME',
    "audio: PipeWire/PulseAudio source (pactl list short sources); monitor (default) = what plays on the speakers (with --source wolf, the target's audio in Wolf); wolf = Wolf's audio; test = 440 Hz tone":
        'som: fonte do PipeWire/PulseAudio (pactl list short sources); monitor (padrão) = o que sai nas caixas (com --source wolf, o som do alvo no Wolf); wolf = o som do Wolf; test = tom de 440 Hz',
    "audio: rate (default %(default)s Hz, the PSP's; stereo IMA ADPCM ~ rate/1000 KB/s)":
        'som: taxa (padrão %(default)s Hz, a do PSP; IMA ADPCM estéreo ~ taxa/1000 KB/s)',
    'audio: mono (half the bytes)':
        'som: mono (metade dos bytes)',
    'do not inject the PSP controls on the PC':
        'não injetar os controles do PSP no PC',
    'only show in the log the keys/movements that would be injected':
        'só mostrar no log as teclas/movimentos que seriam injetados',
    'mapping file (default keymap.json)':
        'arquivo de mapeamento (padrão keymap.json)',
    'keymap profile: game, desktop, arrows (keyboard and mouse); xbox, xbox-camera, xbox-shoulders (virtual Xbox 360 controller; with --source wolf, only these). The old names jogo, setas and xbox-ombros still work. Default %(default)s':
        'perfil do keymap: game, desktop, arrows (teclado e mouse); xbox, xbox-camera, xbox-shoulders (controle de Xbox 360 virtual; com --source wolf, só estes). Os nomes antigos jogo, setas e xbox-ombros continuam valendo. Padrão %(default)s',
    "multiplies the profile's mouse speed":
        'multiplica a velocidade do mouse do perfil',
    'releases every key if the PSP sends nothing for S seconds while something is held (default %(default)s; the PSP restates the state every ~100 ms)':
        'solta todas as teclas se o PSP ficar S segundos sem mandar nada enquanto algo está segurado (padrão %(default)s; o PSP reafirma o estado a cada ~100 ms)',
    'seconds between statistics lines':
        'segundos entre linhas de estatística',
    'benchmark: when the PSP connects, runs each quality for --bench-seconds and saves a table to bench_*.md (default 30,50,70,90)':
        'benchmark: quando o PSP conectar, roda cada qualidade por --bench-seconds e salva uma tabela em bench_*.md (padrão 30,50,70,90)',
    'HOST:PORT':
        'HOST:PORTA',
    'settings web interface (default %(default)s, this PC only; 0.0.0.0:5124 opens it to the local network). The password comes from the PSPSTREAM_WEB_PASSWORD variable (without it, anyone who reaches the port changes the settings). Default also in PSPSTREAM_WEB':
        'interface web das configurações (padrão %(default)s, só neste PC; 0.0.0.0:5124 abre para a rede local). A senha vem da variável PSPSTREAM_WEB_PASSWORD (sem ela, quem alcança a porta muda as configurações). Padrão também em PSPSTREAM_WEB',
    "name accepted in the web interface address, besides localhost, the PC's name and IPs (e.g. one from the router's DNS); can repeat. Default: PSPSTREAM_WEB_HOSTS, comma separated":
        'nome aceito no endereço da interface web, além de localhost, do nome do PC e de IPs (ex.: um do DNS do roteador); pode repetir. Padrão: PSPSTREAM_WEB_HOSTS, separados por vírgula',
    'no web interface':
        'sem a interface web',
    'FILE':
        'ARQUIVO',
    'settings saved by the web interface (default %(default)s). Command-line options win over the file':
        'configurações gravadas pela interface web (padrão %(default)s). As opções da linha de comando valem mais que o arquivo',
    "check this machine's dependencies and show the command to install what is missing (apt, dnf, pacman or zypper), without starting the server":
        'conferir as dependências desta máquina e mostrar o comando para instalar o que falta (apt, dnf, pacman ou zypper), sem iniciar o servidor',
    'prepare this machine: installs what --check points out (packages, uinput, firewall, KMS capture), showing each command and asking before':
        'preparar esta máquina: instala o que o --check aponta (pacotes, uinput, firewall, captura KMS), mostrando cada comando e pedindo confirmação antes',
    'settings: %s (%s)':
        'configuração: %s (%s)',
    'settings: the command line wins over the file for %s':
        'configuração: a linha de comando vale mais que o arquivo para %s',
    '%s; the codec from the settings file was ignored (change it in the web interface)':
        '%s; o codec do arquivo de configuração foi ignorado (mude na interface web)',
    '--dmabuf only works with --source portal; ignored':
        '--dmabuf só vale para --source portal; ignorado',
    'could not start the capture: %s':
        'não foi possível iniciar a captura: %s',
    'the capture from the settings file did not start (%s); using the default one':
        'a captura do arquivo de configuração não subiu (%s); usando a padrão',
    'off (--no-input)':
        'desligados (--no-input)',
    'off (--no-audio)':
        'desligado (--no-audio)',
    'the capture did not open (see the log)':
        'a captura não abriu (veja o log)',
    "PSPStream %s: waiting for the PSP at %s:%d, TCP and UDP (on the PSP: 'Find the PC on the network', or this IP in server.txt)":
        "PSPStream %s: aguardando o PSP em %s:%d, TCP e UDP (no PSP: 'Procurar o PC na rede', ou este IP no server.txt)",
    'with a password':
        'com senha',
    'without a password':
        'sem senha',
    'settings: %s%s':
        'configurações: %s%s',
    ' (open to the local network, {access})':
        ' (aberto para a rede local, {access})',
    ' ({access}; allowed names: {names})':
        ' ({access}; nomes liberados: {names})',
    'web interface disabled (%s): %s':
        'interface web desativada (%s): %s',
    'capture stopped: %s':
        'captura parou: %s',
    'shutting down':
        'encerrando',
    'error handling a UDP request':
        'erro tratando pedido UDP',
    # server/doctor.py
    'System':
        'Sistema',
    'warn':
        'aviso',
    'missing':
        'falta',
    " (unknown distribution: the commands below are Ubuntu's)":
        ' (distribuição desconhecida: os comandos abaixo são do Ubuntu)',
    'Python {version}: the server needs 3.10 or newer':
        'Python {version}: o servidor precisa do 3.10 ou mais novo',
    'X11 session ({desktop}): use --source x11 (or --source kms)':
        'sessão X11 ({desktop}): use --source x11 (ou --source kms)',
    'Wayland session ({desktop}): capture through the portal (the default) or --source kms':
        'sessão Wayland ({desktop}): captura pelo portal (o padrão) ou --source kms',
    'no graphical session in this terminal (SSH?): run the server from inside the session, or use --source kms':
        'sem sessão gráfica neste terminal (SSH?): rode o servidor de dentro da sessão, ou use --source kms',
    'PyGObject with GStreamer (gi): the server does not open without it':
        'PyGObject com o GStreamer (gi): sem ele o servidor não abre',
    'GStreamer {version} and PyGObject':
        'GStreamer {version} e PyGObject',
    ': tested from 1.20 on':
        ': testado do 1.20 em diante',
    'basic elements: {names}':
        'elementos básicos: {names}',
    'basic elements (videoscale, videoconvert, appsink)':
        'elementos básicos (videoscale, videoconvert, appsink)',
    'capture through the portal (the default on Wayland)':
        'captura pelo portal (o padrão no Wayland)',
    'JPEG (old EBOOT or --codec jpeg)':
        'JPEG (EBOOT antigo ou --codec jpeg)',
    'audio (capture)':
        'som (captura)',
    'audio (IMA ADPCM)':
        'som (IMA ADPCM)',
    'KMS capture and --dmabuf (scaling on the GPU)':
        'captura KMS e --dmabuf (redução na GPU)',
    'X11 capture (--source x11)':
        'captura X11 (--source x11)',
    'libopenh264 {version}: H.264 with P frames (the default)':
        'libopenh264 {version}: H.264 com frames P (o padrão)',
    "libopenh264: without it the server sends JPEG (~10x more bytes per frame). Without a package in your distribution: Cisco's library (github.com/cisco/openh264/releases) in ~/.local/lib":
        'libopenh264: sem ela o servidor manda JPEG (~10x mais bytes por frame). Sem o pacote na sua distribuição: a biblioteca do Cisco (github.com/cisco/openh264/releases) em ~/.local/lib',
    'ScreenCast portal v{version}':
        'portal ScreenCast v{version}',
    ' (remembers the chosen screen)':
        ' (lembra a tela escolhida)',
    ' (asks for the screen on every start)':
        ' (pergunta a tela a cada partida)',
    "ScreenCast portal unavailable ({error}): install xdg-desktop-portal and your desktop's backend{backend}":
        'portal ScreenCast indisponível ({error}): instale o xdg-desktop-portal e o backend do seu ambiente{backend}',
    'KMS helper not built (optional: --source kms, 60 fps on GNOME 50+)':
        'auxiliar KMS não compilado (opcional: --source kms, 60 fps no GNOME 50+)',
    'KMS helper ready (--source kms)':
        'auxiliar KMS pronto (--source kms)',
    'KMS helper without permission to read the screen (optional: --source kms)':
        'auxiliar KMS sem a permissão de ler a tela (opcional: --source kms)',
    'again after every make':
        'refaça depois de cada make',
    "KMS capture (optional: --source kms, 60 fps on GNOME 50+): the package's helper needs permission to read the screen":
        'captura KMS (opcional: --source kms, 60 fps no GNOME 50+): o auxiliar do pacote precisa da permissão de ler a tela',
    'uinput and python-evdev: virtual keyboard, mouse and Xbox controller':
        'uinput e python-evdev: teclado, mouse e controle de Xbox virtuais',
    'python-evdev is not installed':
        'python-evdev não instalado',
    '/dev/uinput does not exist (uinput module)':
        '/dev/uinput não existe (módulo uinput)',
    'no write permission on /dev/uinput':
        'sem permissão de escrita no /dev/uinput',
    ' (the server streams without the controls)':
        ' (o servidor transmite sem os controles)',
    'pactl not found: the audio uses the default output, without listing the sources':
        'pactl não encontrado: o som usa a saída padrão, sem listar as fontes',
    'sound server: {name}':
        'servidor de som: {name}',
    "pactl did not find this session's sound server (PipeWire/PulseAudio)":
        'pactl não achou o servidor de som (PipeWire/PulseAudio) desta sessão',
    'port {port}/{proto} in use (another server running?)':
        'porta {port}/{proto} em uso (outro servidor rodando?)',
    'port {port} free (TCP and UDP)':
        'porta {port} livre (TCP e UDP)',
    '{firewall} firewall active: if the PSP does not find the PC, open the port':
        'firewall {firewall} ativo: se o PSP não achar o PC, libere a porta',
    'no known firewall active (ufw, firewalld)':
        'nenhum firewall ativo reconhecido (ufw, firewalld)',
    'To install what is missing:':
        'Para instalar o que falta:',
    'Optional ({what}):':
        'Opcional ({what}):',
    'All set: python3 server/pspstream.py':
        'Tudo pronto: python3 server/pspstream.py',
    'Install the missing packages':
        'Instalar os pacotes que faltam',
    'Open /dev/uinput for the controls':
        'Liberar o /dev/uinput para os controles',
    'KMS capture (optional: 60 fps on GNOME 50+; gives the helper permission to read the screen)':
        'Captura KMS (opcional: 60 fps no GNOME 50+; dá ao auxiliar a permissão de ler a tela)',
    'Open the port in the firewall':
        'Liberar a porta no firewall',
    'PSPStream {version}: preparing this machine ({system})':
        'PSPStream {version}: preparando esta máquina ({system})',
    'Unknown distribution: install the packages as in {url}. The rest (uinput, firewall) follows below.':
        'Distribuição desconhecida: instale os pacotes como em {url}. O resto (uinput, firewall) segue abaixo.',
    'Nothing to do.':
        'Nada a fazer.',
    'Run it? [y/N] ':
        'Executar? [s/N] ',
    'skipped':
        'pulado',
    'failed: {command}':
        'falhou: {command}',
    'done':
        'feito',
    'Checking again:':
        'Conferindo de novo:',
    'PSPStream {version}: checking this machine':
        'PSPStream {version}: conferindo esta máquina',
    'PSPStream: checking this machine':
        'PSPStream: conferindo esta máquina',
    # server/wolf_source.py
    'unexpected id from Wolf: {value}':
        'id inesperado do Wolf: {value}',
    'lobby':
        'lobby',
    'session':
        'sessão',
    '{kind} {name} ({id}, which is in lobby {lobby})':
        '{kind} {name} ({id}, que está no lobby {lobby})',
    'no lobby or session':
        'nenhum lobby nem sessão',
    'no lobby open in Wolf; waiting for one (open a game through Wolf UI)':
        'nenhum lobby aberto no Wolf; esperando um (abra um jogo pelo Wolf UI)',
    'there are several lobbies open in Wolf; pick one with --wolf-target: {options}':
        'há vários lobbies abertos no Wolf; escolha um com --wolf-target: {options}',
    "there are {n} lobbies named '{name}'; use the id: {options}":
        "há {n} lobbies com o nome '{name}'; use o id: {options}",
    "target '{name}' is not open in Wolf; waiting ({options})":
        "alvo '{name}' não está aberto no Wolf; esperando ({options})",
    'Wolf: reception ended (%s)':
        'Wolf: recepção encerrada (%s)',
    'Wolf: the video stopped arriving (%s)':
        'Wolf: o vídeo parou de chegar (%s)',
    'Wolf: audio reception ended (%s)':
        'Wolf: recepção do som encerrada (%s)',
    'Wolf: the audio stopped arriving (%s); the video goes on':
        'Wolf: o som parou de chegar (%s); o vídeo continua',
    'connecting to Wolf':
        'conectando ao Wolf',
    'session {sid} mirroring {target}, conversion {convert}':
        'sessão {sid} espelhando {target}, conversão {convert}',
    '; controls in the lobby':
        '; controles no lobby',
    '; controls: {state}':
        '; controles: {state}',
    'joining the lobby':
        'entrando no lobby',
    'controls: the PSP session left the lobby (controls off)':
        'controles: a sessão do PSP saiu do lobby (controles desligados)',
    'controls: could not leave the lobby: %s':
        'controles: não consegui sair do lobby: %s',
    'the target is a standalone Moonlight session ({id}), not a lobby: the PSP controls do not reach it (view only). Use a lobby in --wolf-target':
        'o alvo é uma sessão Moonlight avulsa ({id}), não um lobby: os controles do PSP não chegam a ela (só visualização). Use um lobby no --wolf-target',
    'controls: the PSP session left the lobby (START + up + RB is the Wolf UI shortcut); joining again':
        'controles: a sessão do PSP saiu do lobby (START + cima + RB é o atalho do Wolf UI); entrando de novo',
    ' (the lobby asks for a PIN: --wolf-pin)':
        ' (o lobby pede PIN: --wolf-pin)',
    ' (single-player lobby, already taken)':
        ' (lobby de um jogador só, já ocupado)',
    'Wolf did not let the PSP session join the lobby: {error}{hint}. View only; trying again every {seconds:g} s':
        'o Wolf não deixou a sessão do PSP entrar no lobby: {error}{hint}. Só visualização; tentando de novo a cada {seconds:g} s',
    'controls: the PSP session joined %s; the virtual controller goes to the game':
        'controles: a sessão do PSP entrou no %s; o controle virtual vai para o jogo',
    'Wolf: unexpected error':
        'Wolf: erro inesperado',
    'Wolf: waiting for the previous capture to end its session':
        'Wolf: esperando a captura anterior encerrar a sessão dela',
    "no frame came from Wolf in {seconds:g} s (conversion: {tried}). See Wolf's log (docker logs) and the --wolf-video-convert option":
        'nenhum frame chegou do Wolf em {seconds:g} s (conversão: {tried}). Veja o log do Wolf (docker logs) e a opção --wolf-video-convert',
    "Wolf: the '%s' conversion worked (--wolf-video-convert %s skips testing the others)":
        "Wolf: a conversão '%s' funcionou (--wolf-video-convert %s pula o teste das outras)",
    'Wolf: ending session %s, left over from a previous PSPStream (one PSPStream per Wolf: they all have the same id)':
        'Wolf: encerrando a sessão %s, que sobrou de um PSPStream anterior (um PSPStream por Wolf: todas têm o mesmo id)',
    'tcpserversrc did not open a port':
        'o tcpserversrc não abriu uma porta',
    'Wolf: another session created through the API without client_id uses the same id (%s); the image may not arrive (only one such program per Wolf)':
        'Wolf: outra sessão criada pela API sem client_id usa o mesmo id (%s); a imagem pode não chegar (só um programa assim por Wolf)',
    'Wolf: video pipeline: %s':
        'Wolf: pipeline de vídeo: %s',
    'Wolf: audio pipeline: %s':
        'Wolf: pipeline de som: %s',
    'Wolf: session %s mirroring %s, conversion %s':
        'Wolf: sessão %s espelhando %s, conversão %s',
    'Wolf: no frame with the %s conversion%s':
        'Wolf: nenhum frame com a conversão %s%s',
    'Wolf: redoing the session for the audio (%d Hz, %s)':
        'Wolf: refazendo a sessão para o som (%d Hz, %s)',
    'Wolf: %s; recreating the session when Wolf answers':
        'Wolf: %s; recriando a sessão quando o Wolf responder',
    'Wolf: session %s vanished (did Wolf restart?); creating another':
        'Wolf: a sessão %s sumiu (o Wolf reiniciou?); criando outra',
    'Wolf: %s closed; waiting':
        'Wolf: %s fechou; esperando',
    'Wolf: %s changed lobby; redoing the session':
        'Wolf: %s mudou de lobby; refazendo a sessão',
    'Wolf: could not end session %s: %s':
        'Wolf: não consegui encerrar a sessão %s: %s',
    # server/web
    'kms (graphics card)':
        'kms (placa de vídeo)',
    'test (animated pattern)':
        'test (padrão animado)',
    'static (still image)':
        'static (imagem fixa)',
    'gst (command line)':
        'gst (linha de comando)',
    'h264p (P frames)':
        'h264p (frames P)',
    'h264 (complete frames)':
        'h264 (quadros completos)',
    'ef (voice)':
        'ef (voz)',
    'cs5 (video)':
        'cs5 (vídeo)',
    'af41 (video)':
        'af41 (vídeo)',
    'none':
        'nenhuma',
    '{name} (Xbox controller)':
        '{name} (controle de Xbox)',
    '{name} (keyboard and mouse)':
        '{name} (teclado e mouse)',
    'command line':
        'linha de comando',
    '{flag} was given on the command line: on restart, it wins over this setting':
        '{flag} foi dado na linha de comando: ao reiniciar, ele vale mais que esta configuração',
    '{value} on restart':
        '{value} ao reiniciar',
    '1 change not applied yet':
        '1 mudança ainda não aplicada',
    '{n} changes not applied yet':
        '{n} mudanças ainda não aplicadas',
    'Applying... (confirm the screen in the portal window, on the PC)':
        'Aplicando... (confirme a tela na janela do portal, no PC)',
    'Applying...':
        'Aplicando...',
    'Applied: {keys}.':
        'Aplicado: {keys}.',
    'Some changes were not applied (see the fields).':
        'Algumas mudanças não foram aplicadas (veja os campos).',
    'Nothing changed.':
        'Nada mudou.',
    'Could not apply: {error}':
        'Não foi possível aplicar: {error}',
    'PSP connected ({transport})':
        'PSP conectado ({transport})',
    'PSP connecting':
        'PSP conectando',
    'waiting for the PSP':
        'aguardando o PSP',
    '{addr} over {transport}, for {seconds} s, {frames} frames ({mb} MB)':
        '{addr} via {transport}, há {seconds} s, {frames} frames ({mb} MB)',
    ', signal {signal}%':
        ', sinal {signal}%',
    'PSP Wi-Fi':
        'Wi-Fi do PSP',
    'WLAN power save on: turn it off in Settings > Power Save Settings':
        'economia de energia WLAN ligada: desligue em Ajustes > Ajustes de economia de energia',
    'in server.txt: {ip}:{port} (or "Find the PC on the network")':
        'no server.txt: {ip}:{port} (ou "Procurar o PC na rede")',
    '{source}, {codec}, up to {fps} fps':
        '{source}, {codec}, até {fps} fps',
    ' — stopped: {error}':
        ' — parou: {error}',
    ' (turned off on the PSP)':
        ' (desligado no PSP)',
    'turned off':
        'desligado',
    'profile {profile} ({note})':
        'perfil {profile} ({note})',
    'Server at {ip}:{port}, running for {minutes} min':
        'Servidor em {ip}:{port}, rodando há {minutes} min',
    ' — settings saved in {path}':
        ' — configurações gravadas em {path}',
    'server offline':
        'servidor fora do ar',
    'Could not read the settings: {error}':
        'Não foi possível ler as configurações: {error}',
    'connecting to the server':
        'conectando ao servidor',
    'Now':
        'Agora',
    'fps on the PSP':
        'fps no PSP',
    'capture fps':
        'fps da captura',
    'latency (ms)':
        'latência (ms)',
    'hitches':
        'engasgos',
    'quality':
        'qualidade',
    'Server log':
        'Log do servidor',
    'Changes not applied yet':
        'Mudanças ainda não aplicadas',
    'Undo':
        'Desfazer',
    'Apply':
        'Aplicar',
    # server/capture.py, gst_pipe.py, imaging.py, win_input.py: servidor de Windows
    'what plays on the speakers':
        'o que sai nas caixas',
    'bundled':
        'junto do PSPStream',
    'GStreamer not found: install the GStreamer runtime (MSVC 64-bit) from gstreamer.freedesktop.org, or use the PSPStream build that bundles it':
        'GStreamer não encontrado: instale o runtime do GStreamer (MSVC 64 bits) de gstreamer.freedesktop.org, ou use a versão do PSPStream que já o traz',
    'no pipeline to try':
        'nenhum pipeline para tentar',
    'on Windows, the audio source is monitor (what plays on the speakers) or test':
        'no Windows, a fonte do som é monitor (o que sai nas caixas) ou test',
    'official installer':
        'instalador oficial',
    'capture: %s':
        'captura: %s',
    'Pillow is missing (pip install pillow)':
        'falta o Pillow (pip install pillow)',
    'gst-launch did not connect':
        'o gst-launch não conectou',
    'libopenh264 is missing (and there is no GStreamer in the process to fall back to)':
        'falta a libopenh264 (e não há GStreamer no processo para usar no lugar)',
    'SendInput only exists on Windows':
        'o SendInput só existe no Windows',
    'controls: Windows refused the input (a window running as administrator, or the lock screen?)':
        'controles: o Windows recusou a entrada (uma janela rodando como administrador, ou a tela de bloqueio?)',
    'keys the Windows server does not know: {codes}':
        'teclas que o servidor de Windows não conhece: {codes}',
    # server/pspstream.py e settings.py: opções do Windows
    'screen = the monitor through Desktop Duplication (default); test = animated pattern; gst = your own GStreamer elements (--gst-src); static = an image':
        'screen = o monitor pelo Desktop Duplication (padrão); test = padrão animado; gst = elementos do GStreamer próprios (--gst-src); static = uma imagem',
    'screen: which monitor (0 = the main one)':
        'screen: qual monitor (0 = o principal)',
    'screen: do not draw the cursor':
        'screen: não desenhar o cursor',
    'audio: monitor (default) = what plays on the speakers (WASAPI loopback); test = 440 Hz tone':
        'som: monitor (padrão) = o que sai nas caixas (loopback do WASAPI); test = tom de 440 Hz',
    'check this machine (GStreamer, openh264, firewall) without starting the server':
        'confere esta máquina (GStreamer, openh264, firewall) sem iniciar o servidor',
    'prepare this machine: firewall rule and the openh264 library, asking before each step':
        'prepara esta máquina: regra do firewall e a biblioteca openh264, perguntando antes de cada passo',
    'screen: the monitor through Desktop Duplication (DXGI), with the scaling on the GPU when it can. test: animated pattern. static: still image.':
        'screen: o monitor pelo Desktop Duplication (DXGI), com a redução na GPU quando dá. test: padrão animado. static: imagem fixa.',
    'Monitor':
        'Monitor',
    '0 = the main monitor, 1 = the second one, and so on.':
        '0 = o monitor principal, 1 = o segundo, e assim por diante.',
    'monitor = what plays on the speakers (WASAPI loopback); test = 440 Hz tone.':
        'monitor = o que sai nas caixas (loopback do WASAPI); test = tom de 440 Hz.',
    # server/win_doctor.py
    'GStreamer {version} ({where}: {path})':
        'GStreamer {version} ({where}: {path})',
    'basic elements':
        'elementos básicos',
    'screen capture (Desktop Duplication)':
        'captura da tela (Desktop Duplication)',
    'scaling on the GPU (without it, on the CPU)':
        'redução na GPU (sem ela, na CPU)',
    'audio (WASAPI loopback, IMA ADPCM)':
        'som (loopback do WASAPI, IMA ADPCM)',
    'test pattern (--source test)':
        'padrão de teste (--source test)',
    'SendInput: keyboard and mouse':
        'SendInput: teclado e mouse',
    'All set: pspstream':
        'Tudo pronto: pspstream',
    'GStreamer not found: the capture does not start without it. Use the PSPStream build that bundles it, or install the GStreamer runtime (MSVC 64-bit) from gstreamer.freedesktop.org':
        'GStreamer não encontrado: a captura não abre sem ele. Use a versão do PSPStream que já o traz, ou instale o runtime do GStreamer (MSVC 64 bits) de gstreamer.freedesktop.org',
    'Pillow: JPEG (old EBOOT or --codec jpeg) and still images':
        'Pillow: JPEG (EBOOT antigo ou --codec jpeg) e imagens fixas',
    'Pillow is missing (pip install pillow): no JPEG':
        'falta o Pillow (pip install pillow): sem JPEG',
    'no firewall rule for the port: Windows blocks the PSP (pspstream --setup creates it, as administrator)':
        'nenhuma regra do firewall para a porta: o Windows bloqueia o PSP (o pspstream --setup cria, como administrador)',
    "the network is Public: Windows blocks the PSP and the 'Find the PC' broadcast. In Settings > Network, make it Private":
        "a rede está como Pública: o Windows bloqueia o PSP e o broadcast do 'Procurar o PC'. Em Configurações > Rede, deixe-a como Privada",
    "Download Cisco's openh264 library (H.264 with P frames)":
        'Baixar a biblioteca openh264 do Cisco (H.264 com frames P)',
    ' (bundled)':
        ' (junto do PSPStream)',
    ': tested from 1.22 on':
        ': testado a partir do 1.22',
    "libopenh264 not found: without it the server sends JPEG (~10x more bytes per frame). pspstream --setup downloads Cisco's":
        'libopenh264 não encontrada: sem ela o servidor manda JPEG (~10x mais bytes por frame). O pspstream --setup baixa a do Cisco',
    'the downloaded file does not match the expected checksum ({digest})':
        'o arquivo baixado não bate com o checksum esperado ({digest})',
    'firewall rule {rule}':
        'regra do firewall {rule}',
    'network profile: {category}':
        'perfil da rede: {category}',
    # Windows: controle de Xbox virtual (ViGEmBus)
    'Keyboard and mouse through SendInput; the xbox profiles need the ViGEmBus driver (pspstream --setup).':
        'Teclado e mouse pelo SendInput; os perfis xbox precisam do driver ViGEmBus (pspstream --setup).',
    'Open it? [y/N] ':
        'Abrir? [s/N] ',
    'ViGEmBus: virtual Xbox 360 controller (the xbox profiles)':
        'ViGEmBus: controle de Xbox 360 virtual (os perfis xbox)',
    'The virtual Xbox controller (the xbox profiles) needs the ViGEmBus driver. Open its download page? Install it as administrator, then run pspstream --check':
        'O controle de Xbox virtual (os perfis xbox) precisa do driver ViGEmBus. Abrir a página de download dele? Instale como administrador e depois rode pspstream --check',
    'ViGEmBus driver not installed: no virtual Xbox controller (the xbox profiles; pspstream --setup opens its download page). Keyboard and mouse work':
        'driver ViGEmBus não instalado: sem o controle de Xbox virtual (os perfis xbox; o pspstream --setup abre a página de download dele). Teclado e mouse funcionam',
    'virtual Xbox controller: {reason}':
        'controle de Xbox virtual: {reason}',
    'ViGEmClient.dll not found (it comes with pspstream.exe, in the vigem folder; PSPSTREAM_VIGEMCLIENT points to another)':
        'ViGEmClient.dll não encontrada (ela vem com o pspstream.exe, na pasta vigem; PSPSTREAM_VIGEMCLIENT aponta para outra)',
    'the ViGEmBus driver is not installed: pspstream --setup opens its download page (install it once, as administrator)':
        'o driver ViGEmBus não está instalado: o pspstream --setup abre a página de download dele (instale uma vez, como administrador)',
    'ViGEmBus has no free controller slot (4 Xbox controllers already connected?)':
        'o ViGEmBus não tem vaga para outro controle (já há 4 controles de Xbox conectados?)',
    'no access to the ViGEmBus driver (another program holding it?)':
        'sem acesso ao driver ViGEmBus (outro programa o está segurando?)',
    'ViGEmBus error 0x{code:08X}':
        'erro 0x{code:08X} do ViGEmBus',
    'ViGEmClient: out of memory':
        'ViGEmClient: sem memória',
    'the ViGEmBus driver is too old: install the latest version ({url})':
        'o driver ViGEmBus é antigo demais: instale a versão mais nova ({url})',
    'gamepad: ViGEmBus refused the update: %s':
        'controle: o ViGEmBus recusou a atualização: %s',
    '{path} did not load: {error}':
        '{path} não carregou: {error}',
}
