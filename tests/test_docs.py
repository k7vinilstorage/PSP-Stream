"""Links da documentação: o README, o CHANGELOG e as páginas da wiki (pasta
wiki/, publicada na wiki do GitHub pelo workflow wiki.yml).

Confere que cada link aponta para algo que existe: uma página da wiki, um
título dentro dela (âncora) ou um arquivo do repositório. Na wiki, links
para o repositório vão com o endereço completo (.../blob/main/caminho),
porque a wiki é outro repositório.

  python3 -m unittest tests.test_docs
"""
import re
import unittest
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
REPO_URL = "https://github.com/k7vinilstorage/PSP-Stream"
WIKI_URL = REPO_URL + "/wiki"
FILE_URL = re.compile(re.escape(REPO_URL) + r"/(?:blob|tree)/main/([^#]*)(?:#(.*))?$")

LINK = re.compile(r"!?\[(?:[^\[\]]|\[[^\]]*\])*\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def prose_lines(text):
    """As linhas fora dos blocos de código, com o número de cada uma."""
    fence = None
    for n, line in enumerate(text.splitlines(), 1):
        m = FENCE.match(line)
        if m:
            if fence is None:
                fence = m.group(1)
            elif m.group(1) == fence:
                fence = None
            continue
        if fence is None:
            yield n, line


def slug(heading):
    """A âncora que o GitHub dá a um título (como o github-slugger)."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"[^\w\- ]", "", text.strip().lower())
    return text.replace(" ", "-")


def anchors(path):
    seen = {}
    out = set()
    for _, line in prose_lines(path.read_text(encoding="utf-8")):
        m = HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        out.add(base if n == 0 else f"{base}-{n}")
    return out


def links(path):
    for n, line in prose_lines(path.read_text(encoding="utf-8")):
        line = re.sub(r"`[^`]*`", "``", line)
        for m in LINK.finditer(line):
            yield n, m.group(1)


def wiki_pages():
    return {p.stem: p for p in WIKI.glob("*.md")}


class DocLinksTest(unittest.TestCase):
    def check_anchor(self, where, path, anchor):
        if anchor:
            self.assertIn(unquote(anchor), anchors(path), f"{where}: não há o título #{anchor} em {path.name}")

    def check_wiki_target(self, where, target):
        page, _, anchor = target.partition("#")
        page = unquote(page) or "Home"
        pages = wiki_pages()
        self.assertIn(page, pages, f"{where}: a wiki não tem a página {page!r}")
        self.check_anchor(where, pages[page], anchor)

    def check_repo_file(self, where, rel, anchor=None):
        rel = unquote(rel).rstrip("/")
        target = ROOT / rel
        self.assertTrue(target.exists(), f"{where}: {rel!r} não existe no repositório")
        if anchor and target.suffix == ".md":
            self.check_anchor(where, target, anchor)

    def check_external(self, where, url):
        """Endereços do próprio repositório: a wiki e os arquivos da main."""
        if url == WIKI_URL or url.startswith(WIKI_URL + "/") or url.startswith(WIKI_URL + "#"):
            self.check_wiki_target(where, url[len(WIKI_URL):].lstrip("/"))
            return
        m = FILE_URL.match(url)
        if m:
            self.check_repo_file(where, m.group(1), m.group(2))

    def test_repo_docs(self):
        """README e CHANGELOG: arquivos do repositório, âncoras e páginas da wiki."""
        for name in ("README.md", "CHANGELOG.md"):
            path = ROOT / name
            for n, url in links(path):
                where = f"{name}:{n}"
                if url.startswith(("http://", "https://")):
                    self.check_external(where, url)
                elif url.startswith("#"):
                    self.check_anchor(where, path, url[1:])
                elif not url.startswith("mailto:"):
                    rel, _, anchor = url.partition("#")
                    self.check_repo_file(where, rel, anchor)

    def test_wiki(self):
        """Páginas da wiki: links entre páginas pelo nome, o resto com o endereço completo."""
        pages = wiki_pages()
        self.assertIn("Home", pages)
        for page in pages.values():
            for n, url in links(page):
                where = f"wiki/{page.name}:{n}"
                if url.startswith(("http://", "https://")):
                    self.check_external(where, url)
                elif url.startswith("#"):
                    self.check_anchor(where, page, url[1:])
                elif url.startswith("mailto:"):
                    continue
                else:
                    self.assertNotIn("/", url.partition("#")[0],
                                     f"{where}: {url!r} não funciona na wiki; use {REPO_URL}/blob/main/...")
                    self.assertFalse(url.endswith(".md"), f"{where}: na wiki, o link é o nome da página, sem .md")
                    self.check_wiki_target(where, url)

    def test_every_page_is_listed(self):
        """Toda página aparece na Home e no menu lateral."""
        pages = set(wiki_pages()) - {"Home", "_Sidebar", "_Footer"}
        for index in ("Home", "_Sidebar"):
            linked = {unquote(url.partition("#")[0]) for _, url in links(WIKI / f"{index}.md")}
            self.assertEqual(pages - linked, set(), f"páginas fora de wiki/{index}.md")

    def test_slug(self):
        self.assertEqual(slug("5. Já tenho o Wolf"), "5-já-tenho-o-wolf")
        self.assertEqual(slug("8. Configurações (`.env`)"), "8-configurações-env")
        self.assertEqual(slug("Controle de Xbox (`--profile xbox`)"), "controle-de-xbox---profile-xbox")
        self.assertEqual(slug("UDP"), "udp")


if __name__ == "__main__":
    unittest.main()
