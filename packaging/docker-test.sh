#!/bin/sh
# Teste da imagem Docker contra o Wolf falso (tests/fake_wolf.py), com as
# opções do docker/compose.yml: uid 0 sem nenhuma capability, sistema de
# arquivos só leitura, o diretório do socket montado só para leitura, rede do
# host e as portas de ping do Wolf (48100/48200). Usado pelo CI.
#
#   docker build -t pspstream:test . && sh packaging/docker-test.sh pspstream:test
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
image=${1:-pspstream:test}
run=$(mktemp -d)
cleanup() {
    docker rm -f pspstream-test fakewolf-test > /dev/null 2>&1 || true
    rm -rf "$run" 2> /dev/null || sudo rm -rf "$run" 2> /dev/null || true
}
trap cleanup EXIT

# O Wolf falso como root, como o Wolf: o socket sai srwxr-xr-x root.
docker run -d --name fakewolf-test --network host --user 0:0 -v "$root:/src:ro" -v "$run:/var/run/wolf" \
    --entrypoint python3 "$image" /src/tests/fake_wolf.py --dir /var/run/wolf > /dev/null
sleep 2
docker run -d --name pspstream-test --network host --user 0:0 --cap-drop ALL \
    --security-opt no-new-privileges:true --read-only --tmpfs /tmp -v "$run:/var/run/wolf:ro" \
    "$image" --source wolf --wolf-video-convert cpu --port 5600 --web 127.0.0.1:5601 > /dev/null
sleep 4
docker run --rm --network host -v "$root:/src:ro" --entrypoint python3 "$image" \
    /src/tools/fake_client.py 127.0.0.1 --port 5600 --transport udp --h264p --audio --input-demo --seconds 4 \
    --json > "$run/fake.json"
docker stop -t 10 pspstream-test > /dev/null
docker stop -t 10 fakewolf-test > /dev/null
docker logs pspstream-test 2>&1
docker logs fakewolf-test 2>&1 | tail -1 > "$run/wolf.json"
python3 - "$run/fake.json" "$run/wolf.json" <<'PY'
import json
import sys

psp = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
wolf = json.loads(open(sys.argv[2]).read())
print("PSP de teste:", {k: psp.get(k) for k in ("frames", "broken", "audio_packets", "audio_lost")})
print("Wolf falso:", {"chamadas": sorted(set(wolf["calls"])), "pipelines": wolf["started"],
                      "entradas": wolf["inputs"]})
assert psp["frames"] > 20 and psp["broken"] == 0, psp
assert psp.get("audio_packets", 0) > 50, psp
assert all(ok for _, ok, _ in wolf["started"]) and len(wolf["started"]) == 2, wolf
assert "/lobbies/join" in wolf["calls"] and wolf["inputs"] > 2, wolf
assert wolf["calls"][-1] == "/sessions/stop", wolf  # docker stop encerra a sessão no Wolf
PY
echo "ok"
