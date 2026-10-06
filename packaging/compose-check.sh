#!/bin/sh
# Confere os arquivos de docker/ e o instalador (usado pelo CI):
# - cada compose é válido (sozinho e com o build.yml);
# - o serviço pspstream é o mesmo nos três (fora o depends_on e o padrão da
#   conversão, nvidia no compose.nvidia.yml);
# - o install.sh passa no shellcheck e gera um .env que o compose aceita.
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work" 2> /dev/null || sudo rm -rf "$work"' EXIT
cd "$root/docker"
for f in compose.yml compose.nvidia.yml pspstream.yml; do
    docker compose -f "$f" config -q
    docker compose -f "$f" -f build.yml config -q
    docker compose -f "$f" config --format json > "$work/$f.json"
done
python3 - "$work" <<'PY'
import json
import sys

def service(name):
    svc = json.load(open(f"{sys.argv[1]}/{name}.json"))["services"]["pspstream"]
    svc.pop("depends_on", None)
    svc["environment"].pop("PSPSTREAM_VIDEO_CONVERT")
    return svc

base = service("compose.yml")
for other in ("compose.nvidia.yml", "pspstream.yml"):
    assert service(other) == base, f"the pspstream service in {other} differs from compose.yml"
print("pspstream service is the same in the three files")
PY
if command -v shellcheck > /dev/null; then
    shellcheck "$root/docker/install.sh" "$root/packaging/"*.sh
fi
sudo=""
[ "$(id -u)" = 0 ] || sudo=sudo
$sudo bash "$root/docker/install.sh" --dry-run --gpu intel --yes --dir "$work/dry" > /dev/null
$sudo bash "$root/docker/install.sh" --gpu intel --yes --no-host --no-start --dir "$work/inst" > /dev/null
$sudo sh -c "cd '$work/inst' && docker compose config -q"
$sudo grep -q '^COMPOSE_FILE=compose.yml$' "$work/inst/.env"
$sudo grep -q '^PSPSTREAM_VIDEO_CONVERT=va$' "$work/inst/.env"
echo "ok"
