"""Idioma: inglês por padrão, português completo como opção.

Confere no código-fonte que cada texto dentro de tr() ou N_() (servidor) e
de t(), N_() e data-i18n (interface web) tem tradução em lang_pt.PT, com os mesmos
campos, e que não sobrou texto em português fora delas.

  python3 -m unittest tests.test_i18n
"""
import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server"
sys.path.insert(0, str(SERVER))

import i18n  # noqa: E402
from lang_pt import PT  # noqa: E402

FIELDS = re.compile(r"\{[^{}]*\}|%[-#0 +]*\d*(?:\.\d+)?[sdifrx%]")
ACCENTS = re.compile(r"[áéíóúãõçâêôàÁÉÍÓÚÃÕÇÂÊÔ]")
JS_CALL = re.compile(r"""\b(?:t|N_)\(\s*(["'`])((?:\\.|(?!\1).)*)\1""")
HTML_ATTR = re.compile(r'data-i18n(?:-[a-z-]+)?="([^"]*)"')


def server_files():
    return sorted(p for p in SERVER.glob("*.py") if p.name != "lang_pt.py")


def docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


def scan(path):
    """(mensagens de tr()/N_(), problemas, constantes em português fora delas)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    msgs, problems, inside = [], [], set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "N_"):
            arg = node.args[0] if node.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                msgs.append((node.lineno, arg.value))
                inside.add(id(arg))
            elif arg is None or isinstance(arg, (ast.JoinedStr, ast.BinOp)):
                # f-string ou texto montado: nunca bate com a chave do catálogo
                problems.append(f"{path.name}:{node.lineno}: {node.func.id}() precisa de um texto fixo")
    docs = docstrings(tree)
    loose = [f"{path.name}:{n.lineno}: {n.value!r}" for n in ast.walk(tree)
             if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in inside
             and id(n) not in docs and ACCENTS.search(n.value)]
    return msgs, problems, loose


def web_messages():
    out = []
    for path in (SERVER / "web" / "app.js", SERVER / "web" / "index.html"):
        text = path.read_text(encoding="utf-8")
        out += [(path.name, m.group(2)) for m in JS_CALL.finditer(text)]
        out += [(path.name, m.group(1)) for m in HTML_ATTR.finditer(text)]
    return out


def fields(text):
    return sorted(FIELDS.findall(text))


class CatalogTest(unittest.TestCase):
    def test_every_message_is_translated(self):
        missing, wrong = [], []
        for path in server_files():
            msgs, problems, _ = scan(path)
            self.assertEqual(problems, [])
            for line, msg in msgs:
                where = f"{path.name}:{line}"
                if msg not in PT:
                    missing.append(f"{where}: {msg!r}")
                elif fields(msg) != fields(PT[msg]):
                    wrong.append(f"{where}: {fields(msg)} x {fields(PT[msg])} em {msg!r}")
        for name, msg in web_messages():
            if msg not in PT:
                missing.append(f"{name}: {msg!r}")
            elif fields(msg) != fields(PT[msg]):
                wrong.append(f"{name}: {msg!r}")
        self.assertEqual(missing, [], "sem tradução em lang_pt.py")
        self.assertEqual(wrong, [], "campos diferentes na tradução")

    def test_no_stale_translations(self):
        used = {m for p in server_files() for _, m in scan(p)[0]} | {m for _, m in web_messages()}
        self.assertEqual(sorted(set(PT) - used), [], "traduções que nenhum código usa")
        self.assertEqual([k for k, v in PT.items() if not v.strip()], [])

    def test_no_portuguese_left_in_the_server(self):
        loose = [line for p in server_files() for line in scan(p)[2]]
        self.assertEqual(loose, [], "texto em português fora de tr()/N_()")


class LanguageTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_normalize(self):
        for code in ("pt", "pt_BR", "pt-BR", "PT_br.UTF-8"):
            self.assertEqual(i18n.normalize(code), "pt")
        for code in ("en", "en_US", "en-GB.utf8"):
            self.assertEqual(i18n.normalize(code), "en")
        with self.assertRaises(ValueError):
            i18n.normalize("fr")

    def test_default_is_english(self):
        self.assertEqual(i18n.DEFAULT, "en")
        self.assertEqual(i18n.from_argv([], environ={}), "en")
        self.assertEqual(i18n.from_argv(["--lang", "pt"], environ={}), "pt")
        self.assertEqual(i18n.from_argv(["--lang=pt_BR"], environ={}), "pt")
        self.assertEqual(i18n.from_argv([], environ={"PSPSTREAM_LANG": "pt"}), "pt")
        self.assertEqual(i18n.from_argv(["--lang", "en"], environ={"PSPSTREAM_LANG": "pt"}), "en")
        self.assertEqual(i18n.from_argv(["--lang", "xx"], environ={}), "en")

    def test_tr(self):
        msg = next(iter(PT))
        self.assertEqual(i18n.tr(msg), msg)
        self.assertEqual(i18n.catalog(), {})
        i18n.set_language("pt")
        self.assertEqual(i18n.tr(msg), PT[msg])
        self.assertIs(i18n.catalog(), PT)
        self.assertEqual(i18n.tr("not in the catalog"), "not in the catalog")



class PortugueseTest(unittest.TestCase):
    """Com --lang pt (ou PSPSTREAM_LANG=pt), as mensagens saem em português."""

    def setUp(self):
        i18n.set_language("pt")

    def tearDown(self):
        i18n.set_language("en")

    def test_help_and_check(self):
        from unittest import mock
        import distro
        import doctor
        import pspstream
        self.assertIn("porta TCP e UDP", pspstream.build_parser().format_help())
        with mock.patch.object(distro, "current", return_value=("debian", "Ubuntu 24.04")):
            text = doctor.report([doctor.Item("System", "ok", "x"), doctor.Item("Audio", "warn", "y")])
        self.assertIn("Sistema", text)
        self.assertIn("aviso", text)
        self.assertIn("Som", text)

    def test_stats_line(self):
        import stats
        s = {"fps": 50.0, "source_fps": 60.0, "kb_per_frame": 2.0, "wifi_kbps": 100.0, "latency_ms": 30.0,
             "latency_p95_ms": 40.0, "capture_ms": 1.0, "age_ms": 2.0, "transfer_ms": 10.0, "local_ms": 12.0,
             "first_ms": 3.0, "burst_ms": 7.0, "burst_kbps": 400.0, "decode_ms": 10.0, "wait_ms": 1.0,
             "idle_ms": None, "ping_ms": 0, "quality": None, "keepalive": 0, "resent_pct": 0, "lost": 0,
             "hitches": 2, "hitch_max_ms": 70.0, "hitch_causes": {"loss": 1, "late request": 1}}
        line = stats.format_summary(s)
        self.assertIn("latência", line)
        self.assertIn("engasgos 2 (pior 70 ms: 1 perda, 1 pedido atrasado)", line)


class CommandLineTest(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("en")

    def test_lang_option(self):
        import pspstream
        parser = pspstream.build_parser()
        self.assertEqual(parser.parse_args([]).lang, "en")
        self.assertEqual(parser.parse_args(["--lang", "pt_BR"]).lang, "pt")
        with self.assertRaises(SystemExit), open("/dev/null", "w") as null:
            import contextlib
            with contextlib.redirect_stderr(null):
                parser.parse_args(["--lang", "fr"])

    def test_check_in_portuguese(self):
        from unittest import mock
        import doctor
        import pspstream
        seen = []
        with mock.patch.object(doctor, "main", side_effect=lambda *a: seen.append(i18n.language()) or 0):
            self.assertEqual(pspstream.main(["--check", "--lang", "pt"]), 0)
        self.assertEqual(seen, ["pt"])

if __name__ == "__main__":
    unittest.main()
