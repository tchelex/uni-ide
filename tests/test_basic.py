# -*- coding: utf-8 -*-
"""
Тесты базовых функций UNI IDE (серверное API).

Запуск из корня проекта:
    python -m unittest discover -s tests -v

Без внешних зависимостей (unittest + Flask test_client). Не требуют
реального arduino-cli: медленные/системные вызовы подменяются фейком,
который фиксирует параллельность — это регрессионные тесты против
«стада» arduino-cli-процессов, забивавшего ЦП на слабых машинах.
Все файлы создаются во временной папке, проект не трогается.
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


class BaseCase(unittest.TestCase):
    """Общая песочница: временные SKETCHBOOK/BUILD_PATH/RECENT_FILE."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="uni-ide-tests-")
        cls._saved = {
            "SKETCHBOOK": server.SKETCHBOOK,
            "BUILD_PATH": server.BUILD_PATH,
            "RECENT_FILE": server.RECENT_FILE,
            "run_cli": server.run_cli,
            "HAS_WEBVIEW": server.HAS_WEBVIEW,
        }
        server.SKETCHBOOK = os.path.join(cls.tmp, "Uni_Sketches")
        server.BUILD_PATH = os.path.join(cls.tmp, "build-tmp")
        server.RECENT_FILE = os.path.join(cls.tmp, "recent.json")

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._saved.items():
            setattr(server, k, v)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.client = server.app.test_client()
        # чистое состояние между тестами
        shutil.rmtree(server.SKETCHBOOK, ignore_errors=True)
        shutil.rmtree(server.BUILD_PATH, ignore_errors=True)
        try:
            os.remove(server.RECENT_FILE)
        except OSError:
            pass
        # дождаться завершения потока прогрева от предыдущего теста
        for _ in range(100):
            with server._warm_guard:
                if not server._warm_state["thread_alive"]:
                    break
            time.sleep(0.05)
        with server._warm_guard:
            server._warm_state["active"] = None
            server._warm_state["pending"] = None
        server._warmed.clear()
        server._ports_cache["data"] = None
        server._ports_cache["t"] = 0.0
        server.run_cli = self._saved["run_cli"]
        server.HAS_WEBVIEW = self._saved["HAS_WEBVIEW"]


class TestProjects(BaseCase):
    """Создание/сохранение/открытие проектов."""

    def test_new_sketch_numbering_and_template(self):
        r1 = _post(self.client, "/api/sketch/new", {}).get_json()
        r2 = _post(self.client, "/api/sketch/new", {}).get_json()
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r1["name"], "uni_sketch1")
        self.assertEqual(r2["name"], "uni_sketch2")
        self.assertTrue(os.path.exists(r1["path"]))
        # стартовый шаблон — заготовка UniBase
        self.assertIn("#include <UNI.h>", r1["code"])
        self.assertIn("robot.begin", r1["code"])

    def test_save_and_load_roundtrip(self):
        r = _post(self.client, "/api/sketch/new", {}).get_json()
        code = "// проверка\nvoid setup(){}\nvoid loop(){}\n"
        s = _post(self.client, "/api/save", {"path": r["path"], "code": code}).get_json()
        self.assertTrue(s["ok"])
        g = self.client.get("/api/code?path=" + r["path"]).get_json()
        self.assertEqual(g["code"], code)
        self.assertEqual(g["name"], r["name"])

    def test_default_project_created_on_first_load(self):
        g = self.client.get("/api/code").get_json()
        self.assertTrue(g["ok"])
        self.assertEqual(g["name"], "uni_sketch1")
        self.assertIn("#include <UNI.h>", g["code"])

    def test_recent_list_order_and_exists(self):
        r1 = _post(self.client, "/api/sketch/new", {}).get_json()
        r2 = _post(self.client, "/api/sketch/new", {}).get_json()
        rec = self.client.get("/api/recent").get_json()["recent"]
        self.assertEqual(rec[0]["name"], r2["name"])       # новые — первыми
        self.assertEqual(rec[1]["name"], r1["name"])
        self.assertTrue(all(x["exists"] for x in rec[:2]))
        # удалённый проект помечается exists:false
        shutil.rmtree(os.path.dirname(r1["path"]))
        rec = self.client.get("/api/recent").get_json()["recent"]
        gone = [x for x in rec if x["name"] == r1["name"]][0]
        self.assertFalse(gone["exists"])

    def test_example_opens_as_copy(self):
        # готовим «пример из библиотеки» во временной папке
        exdir = os.path.join(self.tmp, "lib", "examples", "Blink")
        os.makedirs(exdir, exist_ok=True)
        with open(os.path.join(exdir, "Blink.ino"), "w", encoding="utf-8") as f:
            f.write("void setup(){}\nvoid loop(){}\n")
        with open(os.path.join(exdir, "helper.h"), "w", encoding="utf-8") as f:
            f.write("#define X 1\n")

        r = _post(self.client, "/api/example/open", {"path": exdir}).get_json()
        self.assertTrue(r["ok"])
        self.assertEqual(r["name"], "Blink")
        # копия в SKETCHBOOK, вместе с сопутствующими файлами
        self.assertTrue(r["path"].startswith(server.SKETCHBOOK))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(r["path"]), "helper.h")))
        # оригинал не тронут
        self.assertTrue(os.path.exists(os.path.join(exdir, "Blink.ino")))
        # повторное открытие — уникальное имя
        r2 = _post(self.client, "/api/example/open", {"path": exdir}).get_json()
        self.assertTrue(r2["ok"])
        self.assertNotEqual(r2["path"], r["path"])

    def test_window_new_fallback_url(self):
        r = _post(self.client, "/api/sketch/new", {}).get_json()
        opened = []
        server.HAS_WEBVIEW = False
        orig_open = server.webbrowser.open
        server.webbrowser.open = lambda u: opened.append(u) or True
        try:
            w = _post(self.client, "/api/window/new", {"path": r["path"]}).get_json()
        finally:
            server.webbrowser.open = orig_open
        self.assertTrue(w["ok"])
        self.assertEqual(len(opened), 1)
        self.assertIn("path=", opened[0])

    def test_lib_search_empty_query_is_safe(self):
        r = _post(self.client, "/api/lib/search", {}).get_json()
        self.assertFalse(r["ok"])
        self.assertEqual(r["results"], [])


class FakeCli:
    """Подмена run_cli: фиксирует вызовы и максимальную параллельность."""

    def __init__(self, delay=0.2):
        self.delay = delay
        self.calls = []           # список args
        self.active = 0
        self.max_active = 0
        self.events = []          # (метка, время)
        self._lk = threading.Lock()

    def __call__(self, args, timeout=900, nice=False):
        with self._lk:
            self.calls.append(list(args))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        tag = "compile" + ("-nice" if nice else "") if args[0] == "compile" else args[0]
        self.events.append(("start:" + tag, time.time()))
        time.sleep(self.delay)
        if args[0] == "compile" and "--build-path" in args:
            bp = args[args.index("--build-path") + 1]   # «собираем ядро»
            os.makedirs(os.path.join(bp, "core"), exist_ok=True)
            with open(os.path.join(bp, "core", "core.a"), "wb") as f:
                f.write(b"fake")
        self.events.append(("end:" + tag, time.time()))
        with self._lk:
            self.active -= 1
        return True, "", ""


class TestWarmupStampede(BaseCase):
    """Регрессия: прогрев не должен запускать параллельные полные сборки
    (лавина arduino-cli, забивавшая ЦП на школьных машинах)."""

    def test_warmup_is_serialized_and_latest_wins(self):
        fake = FakeCli(delay=0.3)
        server.run_cli = fake
        paths = [_post(self.client, "/api/sketch/new", {}).get_json()["path"] for _ in range(3)]
        # «студент быстро кликает»: три запроса прогрева подряд
        for p in paths:
            r = _post(self.client, "/api/warmup", {"path": p}).get_json()
            self.assertTrue(r["ok"])
        # дождаться тишины
        for _ in range(100):
            with server._warm_guard:
                if not server._warm_state["thread_alive"]:
                    break
            time.sleep(0.05)
        compiles = [c for c in fake.calls if c[0] == "compile"]
        self.assertEqual(fake.max_active, 1, "прогревы шли параллельно!")
        self.assertLessEqual(len(compiles), 2, "очередь должна хранить только последний запрос")
        # последний запрошенный проект прогрет
        st = self.client.get("/api/warmup/status?path=" + paths[-1]).get_json()
        self.assertTrue(st["warm"])

    def test_warmup_request_returns_immediately(self):
        fake = FakeCli(delay=1.0)
        server.run_cli = fake
        p = _post(self.client, "/api/sketch/new", {}).get_json()["path"]
        t0 = time.time()
        r = _post(self.client, "/api/warmup", {"path": p}).get_json()
        self.assertLess(time.time() - t0, 0.5, "warmup должен отвечать сразу, не дожидаясь сборки")
        self.assertTrue(r.get("warming"))

    def test_compile_waits_for_warmup_same_project(self):
        fake = FakeCli(delay=0.5)
        server.run_cli = fake
        r = _post(self.client, "/api/sketch/new", {}).get_json()
        _post(self.client, "/api/warmup", {"path": r["path"]})
        time.sleep(0.1)   # прогрев успел взять блокировку
        c = _post(self.client, "/api/compile",
                  {"path": r["path"], "code": "void setup(){}\nvoid loop(){}"}).get_json()
        self.assertTrue(c["ok"])
        # компиляция не пересекалась с прогревом (блокировка build-path)
        self.assertEqual(fake.max_active, 1)
        order = [e[0] for e in fake.events]
        self.assertLess(order.index("end:compile-nice"), order.index("start:compile"),
                        "реальная компиляция должна дождаться конца прогрева")

    def test_warmup_idempotent_when_already_warm(self):
        fake = FakeCli(delay=0.1)
        server.run_cli = fake
        p = _post(self.client, "/api/sketch/new", {}).get_json()["path"]
        _post(self.client, "/api/warmup", {"path": p})
        for _ in range(100):
            with server._warm_guard:
                if not server._warm_state["thread_alive"]:
                    break
            time.sleep(0.05)
        n = len(fake.calls)
        r = _post(self.client, "/api/warmup", {"path": p}).get_json()
        self.assertTrue(r.get("warm"))
        self.assertEqual(len(fake.calls), n, "повторный warmup не должен запускать сборку")


class TestPortsPileup(BaseCase):
    """Регрессия: опрос портов не должен наслаивать процессы arduino-cli."""

    def test_ports_single_flight(self):
        fake = FakeCli(delay=0.4)
        server.run_cli = fake
        results = []

        def hit():
            c = server.app.test_client()
            results.append(c.get("/api/ports").get_json())

        threads = [threading.Thread(target=hit) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        board_calls = [c for c in fake.calls if c[0] == "board"]
        self.assertLessEqual(len(board_calls), 1, "параллельные /api/ports наслоили вызовы CLI")
        self.assertEqual(len(results), 5)          # все запросы получили ответ
        self.assertTrue(all("ports" in r for r in results))

    def test_ports_cached_within_ttl(self):
        fake = FakeCli(delay=0.05)
        server.run_cli = fake
        self.client.get("/api/ports")
        self.client.get("/api/ports")              # в пределах TTL — из кэша
        board_calls = [c for c in fake.calls if c[0] == "board"]
        self.assertEqual(len(board_calls), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
