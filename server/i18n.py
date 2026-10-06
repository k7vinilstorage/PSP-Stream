"""Idioma das mensagens: inglês (padrão) ou português.

O texto em inglês fica no código, dentro de tr(); o português fica em
lang_pt.PT, com o texto em inglês como chave. Textos definidos antes de o
idioma ser escolhido (as configurações da interface web, por exemplo) são
marcados com N_() e traduzidos com tr() na hora de mostrar.

O idioma vem de --lang, da variável PSPSTREAM_LANG ou da interface web
(server.json); o padrão é inglês. tests/test_i18n.py confere que toda
mensagem tem tradução, com os mesmos campos ({nome}, %s).
"""
import os

LANGS = ("en", "pt")
DEFAULT = "en"
_lang = DEFAULT


def normalize(code) -> str:
    """'pt', 'pt_BR', 'pt-BR.UTF-8' -> 'pt'; 'en', 'en_US' -> 'en'."""
    code = str(code or "").strip().lower()
    for lang in LANGS:
        if code == lang or code.startswith((lang + "_", lang + "-", lang + ".")):
            return lang
    raise ValueError(f"unknown language {code!r} (use {' or '.join(LANGS)})")


def set_language(code) -> str:
    global _lang
    _lang = normalize(code)
    return _lang


def language() -> str:
    return _lang


def tr(text: str) -> str:
    """O texto no idioma atual (o inglês, se faltar a tradução)."""
    if _lang == "pt":
        from lang_pt import PT
        return PT.get(text, text)
    return text


def N_(text: str) -> str:
    """Marca um texto para tradução, sem traduzir agora: tr() na hora de usar."""
    return text


def catalog() -> dict:
    """As traduções do idioma atual (para a interface web): vazio em inglês."""
    if _lang == "pt":
        from lang_pt import PT
        return PT
    return {}


def from_argv(argv, environ=None) -> str:
    """O idioma pedido antes de montar a linha de comando (o --help já sai
    traduzido): --lang X, --lang=X ou PSPSTREAM_LANG. Inválido: o padrão (o
    argparse reclama depois)."""
    environ = os.environ if environ is None else environ
    code = environ.get("PSPSTREAM_LANG") or DEFAULT
    argv = list(argv)
    for i, arg in enumerate(argv):
        if arg == "--lang" and i + 1 < len(argv):
            code = argv[i + 1]
        elif arg.startswith("--lang="):
            code = arg.split("=", 1)[1]
    try:
        return normalize(code)
    except ValueError:
        return DEFAULT
