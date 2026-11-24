# -*- coding: utf-8 -*-
"""
Тесты английской локали (i18n.js + локализация API в server.py).

Главная гарантия — ПОКРЫТИЕ словаря:
  * каждый ключ t("...") в JS-части index.html имеет перевод в i18n.js;
  * каждый кириллический текстовый узел и атрибут title/placeholder/
    aria-label в статической разметке index.html имеет перевод;
  * каждый пользовательский литерал server.py (обёрнут в _t(...)) имеет
    перевод в L10N_EN;
  * значения переводов не содержат кириллицу (пропуск = строка осталась
    русской);
  * i18n.js синтаксически корректен (node --check, если есть node).

Запуск из корня проекта:
    python -m unittest discover -s tests -v
"""

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

CYR = re.compile(r'[А-Яа-яЁё]')


def norm(s):
    return re.sub(r'\s+', ' ', s).strip()


def load_en_js():
    """Ключи и значения словаря EN из i18n.js (парсинг построчно, ключи —
    JSON-строки с экранированием)."""
    path = os.path.join(ROOT, "i18n.js")
    text = open(path, encoding='utf-8').read()
    m = re.search(r'const EN = \{(.*?)\n\};', text, re.S)
    assert m, "словарь EN не найден в i18n.js"
    body = m.group(1)
    keys, vals = {}, []
    # записи вида:  "ключ": "значение",  (возможно многострочное значение
    # через конкатенацию соседних литералов — собираем первый литерал пары)
    for mm in re.finditer(r'^\s*("(?:[^"\\]|\\.)*")\s*:\s*("(?:[^"\\]|\\.)*")',
                          body, re.M):
        k = json.loads(mm.group(1))
        v = json.loads(mm.group(2))
        keys[k] = v
        vals.append(v)
    return keys, vals, path


def load_en_server():
    """Ключи L10N_EN из server.py — через AST (точно и безопасно)."""
    path = os.path.join(ROOT, "server.py")
    tree = ast.parse(open(path, encoding='utf-8').read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', '') == 'L10N_EN' for t in node.targets):
            keys = {}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                    keys[k.value] = v.value
            return keys
    raise AssertionError("словарь L10N_EN не найден в server.py")


def index_parts():
    text = open(os.path.join(ROOT, "index.html"), encoding='utf-8').read()
    a = text.rindex('<script>') + len('<script>')
    b = text.rindex('</script>')
    return text[:a], text[a:b]     # (разметка с <head>, основной JS)


def js_t_keys(js):
    """Первый аргумент всех вызовов t("...")/t('...')."""
    out = set()
    for m in re.finditer(r'''\bt\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')''', js):
        lit = m.group(1)
        out.add(json.loads(lit) if lit[0] == '"' else
                ast.literal_eval(lit))
    return out


# Намеренно остаются по-русски: названия языков в переключателе локали
# пишутся на своём языке («Русский» / English), как везде.
INTENTIONALLY_RU = {"Русский"}


def html_cyrillic_strings(html):
    """Текстовые узлы и атрибуты title/placeholder/aria-label с кириллицей
    из статической разметки (без <style>/<script>)."""
    cut = html.rindex('<body>')
    head, body = html[:cut], html[cut:]
    body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    out = set()
    for t in re.findall(r'>([^<>]+)<', body):
        t = norm(t)
        if t and CYR.search(t):
            out.add(t)
    for m in re.finditer(r'(?:title|placeholder|aria-label)="([^"]*)"', body):
        if CYR.search(m.group(1)):
            out.add(norm(m.group(1)))
    return out


def server_t_keys():
    """Строковые аргументы _t("...") в server.py — через AST."""
    path = os.path.join(ROOT, "server.py")
    tree = ast.parse(open(path, encoding='utf-8').read())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == '_t':
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                out.add(arg.value)
    return out


class TestDictionary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.en, cls.en_vals, cls.en_path = load_en_js()
        cls.en_norm = {norm(k): v for k, v in cls.en.items()}

    def covered(self, s):
        return s in self.en or norm(s) in self.en_norm

    def test_js_keys_covered(self):
        js = index_parts()[1]
        missing = [k for k in js_t_keys(js) if not self.covered(k)]
        self.assertEqual(missing, [],
                         "нет перевода в i18n.js для t()-ключей")

    def test_html_covered(self):
        html = index_parts()[0]
        missing = [k for k in html_cyrillic_strings(html)
                   if k not in INTENTIONALLY_RU and not self.covered(k)]
        self.assertEqual(missing, [],
                         "нет перевода в i18n.js для текста разметки")

    def test_server_keys_covered(self):
        en_srv = load_en_server()
        missing = [k for k in server_t_keys() if k not in en_srv]
        self.assertEqual(missing, [],
                         "нет перевода в L10N_EN (server.py)")

    def test_translations_are_english(self):
        bad = [(k, v) for k, v in self.en.items() if CYR.search(v)]
        self.assertEqual(bad, [], "в английских переводах осталась кириллица")
        en_srv = load_en_server()
        bad = [(k, v) for k, v in en_srv.items() if CYR.search(v)]
        self.assertEqual(bad, [], "в L10N_EN осталась кириллица")

    def test_no_duplicate_norm_keys(self):
        """Ключи, различающиеся только пробелами/переносами, — ловушка
        нормализации applyI18n, их быть не должно."""
        seen = {}
        dups = []
        for k in self.en:
            n = norm(k)
            if n in seen and seen[n] != k:
                dups.append((seen[n], k))
            seen[n] = k
        self.assertEqual(dups, [])

    def test_i18n_js_valid(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("нет node")
        r = subprocess.run([node, "--check", self.en_path],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_build_includes_i18n(self):
        """i18n.js обязан попадать и в бандл, и в payload-обновление."""
        src = open(os.path.join(ROOT, "build.py"), encoding='utf-8').read()
        for lst in ("RES_FILES", "PAYLOAD_FILES"):
            m = re.search(lst + r'\s*=\s*\[([^\]]*)\]', src)
            self.assertIsNotNone(m)
            self.assertIn('"i18n.js"', m.group(1), lst)


class TestServerLocale(unittest.TestCase):
    """Сквозная проверка: API отвечает на языке cookie uni_lang."""

    def setUp(self):
        import server
        self.server = server
        self.client = server.app.test_client()

    def _with_cookie(self, name, value):
        c = self.server.app.test_client()
        try:
            c.set_cookie(name, value)              # werkzeug >= 2.3
        except TypeError:
            c.set_cookie("localhost", name, value)  # werkzeug < 2.3
        return c

    def test_i18n_js_served(self):
        self.assertEqual(self.client.get("/i18n.js").status_code, 200)

    def test_api_messages_localized(self):
        body = dict(data="{}", content_type="application/json")
        r = json.loads(self.client.post("/api/upload", **body).data)
        self.assertEqual(r["log"], "Не выбран COM-порт.")   # по умолчанию — русский

        c_en = self._with_cookie("uni_lang", "en")
        r = json.loads(c_en.post("/api/upload", **body).data)
        self.assertEqual(r["log"], "No COM port selected.")

        c_ru = self._with_cookie("uni_lang", "ru")
        r = json.loads(c_ru.post("/api/upload", **body).data)
        self.assertEqual(r["log"], "Не выбран COM-порт.")


if __name__ == "__main__":
    unittest.main()
