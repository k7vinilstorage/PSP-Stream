#!/usr/bin/env bash
# Installs Wolf (Games on Whales) and PSPStream with Docker on a clean machine:
# checks the GPU, prepares the system as the Wolf documentation asks, writes
# the configuration and starts everything. Shows each command and asks before
# changing the system. Running it again updates (keeps the .env).
#
#   curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
#   sudo bash install.sh
#
# Options:
#   --gpu nvidia|intel|amd|cpu   skip GPU detection
#   --only-pspstream             Wolf already runs (another stack): PSPStream only
#   --dir DIR                    where the compose files and .env go (default /opt/wolf-pspstream)
#   --ref REF                    repository branch or tag to download the files from (default main)
#   --lang en|pt                 installer and server language (default en; also PSPSTREAM_LANG)
#   --yes                        answer yes to everything (no questions)
#   --no-host                    do not touch the system (modules, udev, firewall)
#   --no-start                   only prepare the files, do not start the containers
#   --dry-run                    only show what it would do
set -euo pipefail

REPO="k7vinilstorage/PSP-Stream"
WOLF_RULES_URL="https://raw.githubusercontent.com/games-on-whales/wolf/stable/85-wolf.rules"
WOLF_PORTS_TCP="47984 47989 48010"
WOLF_PORTS_UDP="47999 48100 48200"
PSP_PORT=5123
WEB_PORT=5124

gpu="" only_psp=0 dir="/opt/wolf-pspstream" ref="main" yes=0 no_host=0 no_start=0 dry=0
lang="${PSPSTREAM_LANG:-en}"

# Idioma antes de tudo, para o --help também sair nele.
prev=""
for a in "$@"; do
    case "$a" in --lang=*) lang="${a#--lang=}" ;; esac
    [ "$prev" = --lang ] && lang="$a"
    prev="$a"
done
case "$lang" in pt*|PT*) lang=pt ;; *) lang=en ;; esac

t() {  # t "english" "português": o texto no idioma escolhido
    if [ "$lang" = pt ]; then printf '%s' "$2"; else printf '%s' "$1"; fi
}

usage() {
    if [ "$lang" = pt ]; then
        cat <<'USO'
Instala o Wolf (Games on Whales) e o PSPStream com Docker numa máquina limpa:
confere a GPU, prepara o sistema como a documentação do Wolf pede, escreve a
configuração e sobe tudo. Mostra cada comando e pergunta antes de mexer no
sistema. Rodar de novo atualiza (mantém o .env).

  curl -fsSLO https://raw.githubusercontent.com/k7vinilstorage/PSP-Stream/main/docker/install.sh
  sudo bash install.sh --lang pt

Opções:
  --gpu nvidia|intel|amd|cpu   pula a detecção da GPU
  --only-pspstream             o Wolf já roda (outra stack): só o PSPStream
  --dir PASTA                  onde ficam o compose e o .env (padrão /opt/wolf-pspstream)
  --ref REF                    branch ou tag do repositório para baixar os arquivos (padrão main)
  --lang en|pt                 idioma do instalador e do servidor (padrão en; ou PSPSTREAM_LANG)
  --yes                        responde sim a tudo (sem perguntas)
  --no-host                    não mexe no sistema (módulos, udev, firewall)
  --no-start                   só prepara os arquivos, sem subir os containers
  --dry-run                    só mostra o que faria
USO
    else
        sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
    fi
    exit "${1:-0}"
}
while [ $# -gt 0 ]; do
    case "$1" in
        --gpu) gpu="${2:-}"; shift 2 ;;
        --only-pspstream) only_psp=1; shift ;;
        --dir) dir="${2:-}"; shift 2 ;;
        --ref) ref="${2:-}"; shift 2 ;;
        --lang) shift 2 ;;
        --lang=*) shift ;;
        --yes|-y) yes=1; shift ;;
        --no-host) no_host=1; shift ;;
        --no-start) no_start=1; shift ;;
        --dry-run) dry=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "$(t "unknown option" "opção desconhecida"): $1" >&2; usage 1 ;;
    esac
done

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
warn() { printf '\033[33m    %s: %s\033[0m\n' "$(t warning aviso)" "$*"; }
die() { printf '\033[31m%s: %s\033[0m\n' "$(t error erro)" "$*" >&2; exit 1; }
run() {  # mostra e roda (ou só mostra, no --dry-run)
    printf '    $ %s\n' "$*"
    [ "$dry" = 1 ] || "$@"
}
ask() {  # ask "pergunta" -> 0 = sim
    [ "$yes" = 1 ] && return 0
    [ "$dry" = 1 ] && return 0
    local answer
    read -r -p "    $1 $(t "[Y/n]" "[S/n]") " answer < /dev/tty || return 1
    case "$answer" in ""|s|S|sim|Sim|y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
}

[ "$dry" = 1 ] || [ "$(id -u)" = 0 ] || die "$(t "run as root" "rode como root"): sudo bash $0 $*"
case "$dir" in /*) ;; *) dir="$(pwd)/$dir" ;; esac

# ---- 1. Docker ----
say "Docker"
command -v docker > /dev/null || die "$(t "Docker is not installed" "o Docker não está instalado") (https://docs.docker.com/engine/install/)"
docker compose version > /dev/null 2>&1 || die "$(t "the Docker compose plugin is missing (package docker-compose-plugin)" "falta o plugin compose do Docker (pacote docker-compose-plugin)")"
docker info > /dev/null 2>&1 || die "$(t "Docker does not respond" "o Docker não responde") (sudo systemctl enable --now docker)"
info "$(docker --version); $(docker compose version | head -n1)"

# Um segundo Wolf na rede do host brigaria pelas portas: com um Wolf que não é
# desta instalação, só o PSPStream.
other_wolf=$(docker ps -a --format '{{.Names}} {{.Image}} {{.Label "com.docker.compose.project"}}' \
    | awk -v p="$(basename "$dir")" '$2 ~ /games-on-whales\/wolf/ && $3 != p {print $1}' | head -n1)
if [ -n "$other_wolf" ] && [ "$only_psp" = 0 ]; then
    die "$(t "there is already a Wolf on this server ($other_wolf). To put PSPStream next to it, use --only-pspstream (and the two lines in the Wolf service: https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf, \"I already have Wolf\")" \
             "já existe um Wolf neste servidor ($other_wolf). Para pôr o PSPStream ao lado dele, use --only-pspstream (e as duas linhas no serviço do Wolf: https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf, \"I already have Wolf\")")"
fi

# ---- 2. GPU ----
say "GPU"
if [ -z "$gpu" ]; then
    if command -v nvidia-smi > /dev/null && nvidia-smi -L > /dev/null 2>&1; then
        gpu=nvidia
    elif ls /dev/dri/renderD* > /dev/null 2>&1; then
        gpu=intel   # VA-API: Intel e AMD usam o mesmo caminho
    else
        gpu=cpu
    fi
    info "$(t "detected: $gpu (to change: --gpu nvidia|intel|amd|cpu)" "detectada: $gpu (para trocar: --gpu nvidia|intel|amd|cpu)")"
fi
case "$gpu" in
    nvidia) compose_file=compose.nvidia.yml; convert=nvidia ;;
    intel|amd) compose_file=compose.yml; convert=va ;;
    cpu) compose_file=compose.yml; convert=cpu
         warn "$(t "without a GPU, Wolf encodes the Moonlight video on the CPU (heavy)" "sem GPU, o Wolf codifica o vídeo do Moonlight no processador (pesado)")" ;;
    *) die "$(t "--gpu must be nvidia, intel, amd or cpu" "--gpu deve ser nvidia, intel, amd ou cpu")" ;;
esac
[ "$only_psp" = 1 ] && compose_file=pspstream.yml

if [ "$gpu" = nvidia ] && [ "$only_psp" = 0 ]; then
    nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | sed 's/^/    /' || true
    if ! command -v nvidia-ctk > /dev/null && ! command -v nvidia-container-cli > /dev/null; then
        die "$(t "the NVIDIA Container Toolkit (1.16+) is missing" "falta o NVIDIA Container Toolkit (1.16+)"): https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
    fi
    if ! docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia; then
        warn "$(t "Docker does not know the NVIDIA runtime yet:" "o Docker ainda não conhece o runtime da NVIDIA:")"
        info "sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    fi
    if [ "$(cat /sys/module/nvidia_drm/parameters/modeset 2>/dev/null)" != Y ]; then
        warn "$(t "nvidia-drm.modeset is not on: Wolf needs it. Add nvidia-drm.modeset=1 to" \
                  "nvidia-drm.modeset não está ligado: o Wolf precisa dele. Acrescente nvidia-drm.modeset=1 ao")"
        info "$(t "GRUB_CMDLINE_LINUX_DEFAULT in /etc/default/grub, run sudo update-grub and reboot" \
                  "GRUB_CMDLINE_LINUX_DEFAULT em /etc/default/grub, rode sudo update-grub e reinicie")"
    fi
fi

# ---- 3. Sistema (como a documentação do Wolf pede) ----
if [ "$no_host" = 0 ] && [ "$only_psp" = 0 ]; then
    say "$(t "Virtual controllers (uinput, uhid and the Wolf udev rules)" "Controles virtuais (uinput, uhid e as regras udev do Wolf)")"
    if ask "$(t "load the uinput and uhid modules now and on every boot?" "carregar os módulos uinput e uhid agora e em cada boot?")"; then
        run modprobe uinput || warn "$(t "could not load uinput" "não deu para carregar o uinput")"
        run modprobe uhid || warn "$(t "could not load uhid" "não deu para carregar o uhid")"
        [ "$dry" = 1 ] || printf 'uinput\nuhid\n' > /etc/modules-load.d/wolf.conf
        info "/etc/modules-load.d/wolf.conf: uinput, uhid"
    fi
    if ask "$(t "install /etc/udev/rules.d/85-wolf.rules (from the Wolf repository)?" "instalar /etc/udev/rules.d/85-wolf.rules (do repositório do Wolf)?")"; then
        run curl -fsSL "$WOLF_RULES_URL" -o /etc/udev/rules.d/85-wolf.rules
        if command -v udevadm > /dev/null; then
            run udevadm control --reload-rules
            run udevadm trigger
        fi
    fi
fi

if [ "$no_host" = 0 ]; then
    say "Firewall"
    tcp="$PSP_PORT" udp="$PSP_PORT" what="PSPStream: $PSP_PORT (UDP $(t and e) TCP)"
    if [ "$only_psp" = 0 ]; then
        tcp="$tcp $WOLF_PORTS_TCP" udp="$udp $WOLF_PORTS_UDP"
        what="$what; Wolf: $WOLF_PORTS_TCP (TCP), $WOLF_PORTS_UDP (UDP)"
    fi
    if command -v ufw > /dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
        info "$(t "ufw active. Ports" "ufw ativo. Portas"): $what"
        if ask "$(t "open these ports in ufw?" "liberar essas portas no ufw?")"; then
            for p in $tcp; do run ufw allow "$p/tcp"; done
            for p in $udp; do run ufw allow "$p/udp"; done
        fi
    elif command -v firewall-cmd > /dev/null && firewall-cmd --state > /dev/null 2>&1; then
        info "$(t "firewalld active. Ports" "firewalld ativo. Portas"): $what"
        if ask "$(t "open these ports in firewalld?" "liberar essas portas no firewalld?")"; then
            for p in $tcp; do run firewall-cmd --permanent --add-port="$p/tcp"; done
            for p in $udp; do run firewall-cmd --permanent --add-port="$p/udp"; done
            run firewall-cmd --reload
        fi
    else
        info "$(t "no known active firewall (ufw, firewalld): nothing to do" "nenhum firewall ativo reconhecido (ufw, firewalld): nada a fazer")"
    fi
fi

# ---- 4. Arquivos ----
say "$(t "Files in" "Arquivos em") $dir"
run mkdir -p "$dir"
# De um clone do repositório, os arquivos ao lado deste script; senão, os do
# GitHub (--ref). Rodar de novo traz os compose novos e mantém o .env.
here=$(cd "$(dirname "$0")" && pwd)
for f in compose.yml compose.nvidia.yml pspstream.yml build.yml .env.example; do
    if [ -f "$here/../server/pspstream.py" ]; then
        [ "$here" = "$dir" ] || run cp "$here/$f" "$dir/$f"
    else
        run curl -fsSL "https://raw.githubusercontent.com/$REPO/$ref/docker/$f" -o "$dir/$f"
    fi
done

env_file="$dir/.env"
password=""
if [ -f "$env_file" ]; then
    info "$(t ".env already exists: kept (delete it to start from scratch)" ".env já existe: mantido (apague-o para começar do zero)")"
else
    web="127.0.0.1:$WEB_PORT"
    if ask "$(t "open the web interface on the local network (http://server-IP:$WEB_PORT), with a password?" "abrir a interface web na rede local (http://IP-do-servidor:$WEB_PORT), com senha?")"; then
        web="0.0.0.0:$WEB_PORT"
        password="$(t "(generated at install)" "(gerada na instalação)")"
        [ "$dry" = 1 ] || password=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 16)
    fi
    info "$(t writing escrevendo) $env_file (COMPOSE_FILE=$compose_file, $(t conversion conversão) $convert)"
    if [ "$dry" = 0 ]; then
        umask 077
        sed -e "s|^COMPOSE_FILE=.*|COMPOSE_FILE=$compose_file|" \
            -e "s|^PSPSTREAM_VIDEO_CONVERT=.*|PSPSTREAM_VIDEO_CONVERT=$convert|" \
            -e "s|^PSPSTREAM_WEB=.*|PSPSTREAM_WEB=$web|" \
            -e "s|^PSPSTREAM_WEB_PASSWORD=.*|PSPSTREAM_WEB_PASSWORD=$password|" \
            -e "s|^PSPSTREAM_LANG=.*|PSPSTREAM_LANG=$lang|" \
            "$dir/.env.example" > "$env_file"
    fi
    if [ "$web" != "127.0.0.1:$WEB_PORT" ] && [ "$no_host" = 0 ]; then
        if command -v ufw > /dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
            ask "$(t "open port $WEB_PORT/tcp (web interface) in ufw?" "liberar a porta $WEB_PORT/tcp (interface web) no ufw?")" && run ufw allow "$WEB_PORT/tcp"
        elif command -v firewall-cmd > /dev/null && firewall-cmd --state > /dev/null 2>&1; then
            ask "$(t "open port $WEB_PORT/tcp (web interface) in firewalld?" "liberar a porta $WEB_PORT/tcp (interface web) no firewalld?")" \
                && run firewall-cmd --permanent --add-port="$WEB_PORT/tcp" && run firewall-cmd --reload
        fi
    fi
fi

if [ "$no_start" = 1 ]; then
    say "$(t "Done (not started: --no-start). To start" "Pronto (sem subir: --no-start). Para subir"): cd $dir && docker compose up -d"
    exit 0
fi

# ---- 5. Imagens e containers ----
say "$(t Images Imagens)"
run cd "$dir"
if [ "$only_psp" = 0 ]; then
    run docker compose pull wolf || die "$(t "could not pull the Wolf image (network? ghcr.io?)" "não deu para baixar a imagem do Wolf (rede? ghcr.io?)")"
fi
if ! run docker compose pull pspstream; then
    # Sem a imagem pronta (ainda não publicada, ou privada no ghcr.io): compila do repositório.
    warn "$(t "could not pull the PSPStream image; building from GitHub" "não deu para baixar a imagem do PSPStream; compilando a partir do GitHub") ($ref)"
    run docker build -t pspstream:local "https://github.com/$REPO.git#$ref"
    [ "$dry" = 1 ] || sed -i "s|^PSPSTREAM_IMAGE=.*|PSPSTREAM_IMAGE=pspstream:local|" "$env_file"
fi

say "$(t Starting Subindo)"
run docker compose up -d

if [ "$dry" = 0 ]; then
    for _ in $(seq 1 30); do
        [ -S /var/run/wolf/wolf.sock ] && break
        sleep 2
    done
    [ -S /var/run/wolf/wolf.sock ] || warn "$(t "the Wolf socket (/var/run/wolf/wolf.sock) did not show up" "o socket do Wolf (/var/run/wolf/wolf.sock) não apareceu"): docker compose logs wolf"
    docker compose ps
fi

ip=$(hostname -I 2>/dev/null | awk '{print $1}')
say "$(t Done Pronto)"
[ "$only_psp" = 0 ] && info "1. Moonlight: $(t "add the server $ip; the pairing PIN goes in the link shown by" "adicione o servidor $ip; o PIN do pareamento vai no link que aparece em"): docker compose -f $dir/$compose_file logs wolf"
info "2. $(t "In Moonlight, open Wolf UI and a game (Start = one player; Coop = the PSP plays along)" "No Moonlight, abra o Wolf UI e um jogo (Start = um jogador; Coop = o PSP joga junto)")"
info "3. $(t "On the PSP: PSPStream > Find the PC on the network (or $ip, port $PSP_PORT)" "No PSP: PSPStream > Find the PC on the network (ou $ip, porta $PSP_PORT)")"
if [ -n "$password" ]; then
    info "$(t "Web interface" "Interface web"): http://$ip:$WEB_PORT  ($(t "user: any; password: $password, kept in $env_file" "usuário: qualquer; senha: $password, guardada em $env_file"))"
else
    info "$(t "Web interface" "Interface web"): http://127.0.0.1:$WEB_PORT $(t "on the server (set it up in $env_file)" "no servidor (configure em $env_file)")"
fi
info "Log: cd $dir && docker compose logs -f pspstream"
info "$(t "Update: run this script again (or" "Atualizar: rode este script de novo (ou"): cd $dir && docker compose pull && docker compose up -d)"
info "$(t "Help and troubleshooting" "Ajuda e problemas"): https://github.com/k7vinilstorage/PSP-Stream/wiki/Wolf"
