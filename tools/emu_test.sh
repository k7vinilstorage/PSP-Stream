#!/bin/sh
# Teste automático no PPSSPPHeadless: sobe o servidor, roda o EBOOT num memory
# stick virtual (server.txt -> 127.0.0.1) e salva um screenshot.
#
#   PPSSPP_HEADLESS=/caminho/PPSSPPHeadless tools/emu_test.sh [saida.png] [args do servidor...]
#
# Variáveis: EXIT_AFTER (frames até sair, padrão 1), TIMEOUT (s, padrão 30), PYTHON,
#            EXTRA_CFG (linhas extras para o server.txt).
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
headless="${PPSSPP_HEADLESS:?defina PPSSPP_HEADLESS}"
shot="${1:-$root/emu_screenshot.png}"
[ $# -gt 0 ] && shift
port="${PORT:-5123}"

work=$(mktemp -d)
trap 'kill $srv 2>/dev/null; rm -rf "$work"' EXIT
game="$work/ms/PSP/GAME/PSPStream"
mkdir -p "$game"
cp "$root/psp/EBOOT.PBP" "$game/"
printf '127.0.0.1:%s\nexit_after=%s\n%b\n' "$port" "${EXIT_AFTER:-1}" "${EXTRA_CFG:-}" > "$game/server.txt"

# --config e --no-web: o teste não lê as configurações do usuário nem disputa a porta da interface web
${PYTHON:-python3} "$root/server/pspstream.py" --port "$port" --stats-interval 1 --config "$work/server.json" --no-web \
    "$@" > "$work/server.log" 2>&1 &
srv=$!
sleep 1

"$headless" --memstick="$work/ms" --graphics=software --timeout="${TIMEOUT:-30}" \
    --screenshot-save="$shot" "$game/EBOOT.PBP" || true

sleep 0.5
echo "---- servidor ----"
cat "$work/server.log"
