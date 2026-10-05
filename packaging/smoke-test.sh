#!/bin/sh
# Teste do pacote já instalado: --check, um stream com o PSP falso
# (tools/fake_client.py) e a interface web. Usado pelo CI no Ubuntu e no Fedora.
#
#   packaging/smoke-test.sh [codec]      (codec: jpeg, o padrão, ou h264p)
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
codec=${1:-jpeg}
pspstream --version
pspstream --check
work=$(mktemp -d)
pspstream --source static --codec "$codec" --no-input --no-audio --port 5600 --web 127.0.0.1:5601 \
    --config "$work/server.json" > "$work/server.log" 2>&1 &
srv=$!
trap 'kill $srv 2>/dev/null || true; cat "$work/server.log"; rm -rf "$work"' EXIT
sleep 3
python3 "$root/tools/fake_client.py" --port 5600 --transport udp --h264p --seconds 3 --json > "$work/fake.json"
python3 - "$work/fake.json" <<'PY'
import json
import sys
import urllib.request

summary = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
status = json.load(urllib.request.urlopen("http://127.0.0.1:5601/api/status", timeout=5))
print("stream:", {k: summary[k] for k in ("frames", "broken", "kb_per_frame")}, "| captura:", status["capture"])
assert summary["frames"] > 20 and summary["broken"] == 0, summary
PY
echo "ok"
