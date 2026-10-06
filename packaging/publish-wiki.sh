#!/bin/sh
# Publica a pasta wiki/ na wiki do GitHub (usado pelo workflow wiki.yml). A
# pasta é a fonte: a wiki fica igual a ela, e uma página criada só na wiki é
# apagada.
#
#   GITHUB_REPOSITORY=dono/repo WIKI_TOKEN=... packaging/publish-wiki.sh
#   WIKI_URL=/caminho/repo.git packaging/publish-wiki.sh     (teste local)
#
# A wiki precisa existir antes: Settings > Features > Wikis, e uma primeira
# página criada pelo navegador (só então o GitHub cria o repositório dela).
set -eu
root=$(cd "$(dirname "$0")/.." && pwd)
if [ -n "${WIKI_URL:-}" ]; then
    url=$WIKI_URL
else
    url="https://github.com/${GITHUB_REPOSITORY:?defina GITHUB_REPOSITORY (dono/repo) ou WIKI_URL}.wiki.git"
fi

# O token vai num cabeçalho, como no actions/checkout, e não no endereço.
auth=
if [ -n "${WIKI_TOKEN:-}" ]; then
    auth="http.extraheader=AUTHORIZATION: basic $(printf 'x-access-token:%s' "$WIKI_TOKEN" | base64 | tr -d '\n')"
fi
gitw() {
    if [ -n "$auth" ]; then git -c "$auth" "$@"; else git "$@"; fi
}

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
if ! gitw clone -q --depth 1 "$url" "$work/wiki"; then
    echo "could not clone the wiki ($url). Does it exist? In Settings > Features, check Wikis," >&2
    echo "open the Wiki tab, create any page and run again." >&2
    exit 1
fi

find "$work/wiki" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -R "$root/wiki/." "$work/wiki/"
cd "$work/wiki"
git add -A
if git diff --cached --quiet; then
    echo "the wiki already matches the wiki/ folder"
    exit 0
fi
git -c core.quotepath=false diff --cached --stat
rev=${GITHUB_SHA:-$(git -C "$root" rev-parse HEAD)}
git -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "Wiki from the wiki/ folder at $(printf %.7s "$rev")"
gitw push -q origin HEAD
echo "wiki published"
