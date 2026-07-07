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
            "HAS_PYSERIAL": server.HAS_PYSERIAL,
            "list_ports": getattr(server, "list_ports", None),
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
        with server._procs_guard:
            server._procs.clear()
            server._inflight.clear()
            server._cancelled.clear()
        server.run_cli = self._saved["run_cli"]
        server.HAS_WEBVIEW = self._saved["HAS_WEBVIEW"]
        server.HAS_PYSERIAL = self._saved["HAS_PYSERIAL"]
        server.list_ports = self._saved["list_ports"]


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

    def test_env_reports_app_version(self):
        # версия приложения для заголовка окна — из единого источника server.VERSION
        server.run_cli = lambda *a, **k: (True, "", "")     # без реального arduino-cli
        e = self.client.get("/api/env").get_json()
        self.assertTrue(e["app_version"])
        self.assertEqual(e["app_version"], server.VERSION)


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


class _FakePort:
    def __init__(self, device, vid=None, description="", manufacturer=""):
        self.device = device
        self.vid = vid
        self.description = description
        self.manufacturer = manufacturer


class _FakeListPorts:
    """Подмена serial.tools.list_ports: порты из памяти + счётчик сканирований."""

    def __init__(self, ports, delay=0.0):
        self.ports = ports
        self.delay = delay
        self.scans = 0

    def comports(self):
        self.scans += 1
        if self.delay:
            time.sleep(self.delay)
        return list(self.ports)


class TestPortsPileup(BaseCase):
    """Регрессия: опрос портов не должен запускать процессы arduino-cli.
    С ПОДКЛЮЧЁННОЙ платой `arduino-cli board list` шёл непрерывно (каждые 2 c и
    подолгу — discovery активно опрашивает устройство), плодя дочерние процессы и
    забивая ЦП, из-за чего «прекомпиляция бесконечна». Теперь порты берём из
    pyserial — в процессе, без подпроцессов."""

    def _use_fake_serial(self, ports, delay=0.0):
        server.HAS_PYSERIAL = True
        lp = _FakeListPorts(ports, delay)
        server.list_ports = lp
        return lp

    def test_ports_from_pyserial_without_cli(self):
        fake = FakeCli(delay=0.2)
        server.run_cli = fake
        self._use_fake_serial([
            _FakePort("COM5", vid=0x1A86, description="USB-SERIAL CH340"),
            _FakePort("COM3", vid=0x1234, description="Some UART"),
        ])
        r = self.client.get("/api/ports").get_json()
        addrs = {p["addr"]: p for p in r["ports"]}
        self.assertIn("COM5", addrs)
        self.assertTrue(addrs["COM5"]["ch340"])      # UniBase (CH340) распознан
        self.assertIn("COM3", addrs)
        self.assertFalse(addrs["COM3"]["ch340"])
        # ключевая регрессия: arduino-cli board list НЕ вызывался
        self.assertEqual([c for c in fake.calls if c[0] == "board"], [])

    def test_no_cli_pileup_when_board_connected(self):
        # «плата подключена» → медленный скан; куча одновременных опросов не должна
        # ни запускать arduino-cli, ни наслаивать сканы (single-flight + кэш)
        fake = FakeCli(delay=0.2)
        server.run_cli = fake
        lp = self._use_fake_serial([_FakePort("COM5", vid=0x1A86, description="CH340")], delay=0.3)
        results = []

        def hit():
            c = server.app.test_client()
            results.append(c.get("/api/ports").get_json())

        threads = [threading.Thread(target=hit) for _ in range(6)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(results), 6)
        self.assertTrue(all("ports" in r for r in results))
        self.assertEqual([c for c in fake.calls if c[0] == "board"], [])   # без подпроцессов
        self.assertLessEqual(lp.scans, 1)                                  # single-flight/кэш

    def test_ports_cached_within_ttl(self):
        lp = self._use_fake_serial([_FakePort("COM5", vid=0x1A86)])
        self.client.get("/api/ports")
        self.client.get("/api/ports")              # в пределах TTL — из кэша
        self.assertEqual(lp.scans, 1)


class FakePopen:
    """Подмена subprocess.Popen для проверки контракта _stream_cli
    (регистрация процесса + пометка отмены в финальном событии)."""

    def __init__(self, lines=(), rc=0):
        self.pid = 4321
        self._lines = list(lines)
        self._rc = rc
        self.returncode = None
        self.stdout = iter(self._lines)

    def wait(self, timeout=None):
        self.returncode = self._rc
        return self._rc

    def poll(self):
        return self.returncode


class TestStopBuild(BaseCase):
    """Регрессия: запущенную компиляцию/прошивку можно прервать
    (раньше процесс arduino-cli молотил до конца, остановить было нельзя)."""

    def test_kill_tree_terminates_real_process(self):
        # настоящий дочерний процесс: _kill_tree должен его завершить
        # (на Windows — деревом через taskkill, на POSIX — terminate)
        import subprocess as sp
        p = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.assertIsNone(p.poll())
        server._kill_tree(p)
        self.assertIsNotNone(p.poll())      # процесс завершён

    def test_kill_tree_none_is_safe(self):
        server._kill_tree(None)             # не должно падать

    def test_cancel_build_no_inflight_leaves_no_stale_flag(self):
        bp = os.path.join(server.BUILD_PATH, "nothing")
        self.assertFalse(server.cancel_build(bp))
        # ключевой момент: «висящего» флага отмены не осталось,
        # иначе он прервал бы следующую нормальную сборку
        self.assertNotIn(bp, server._cancelled)

    def test_cancel_all_idle_returns_false(self):
        self.assertFalse(server.cancel_all())

    def test_stop_endpoint_idle(self):
        r = _post(self.client, "/api/sketch/new", {}).get_json()
        s = _post(self.client, "/api/compile/stop", {"path": r["path"]}).get_json()
        self.assertTrue(s["ok"])
        self.assertFalse(s["stopped"])      # нечего останавливать

    def test_prelock_cancellation_skips_build(self):
        # «Стоп» пришёл, пока ждали прогрев: реальную сборку запускать нельзя
        bp = os.path.join(server.BUILD_PATH, "proj-abc")
        called = []
        orig = server._stream_cli
        server._stream_cli = lambda *a, **k: called.append(1) or iter(())
        server._cancelled.add(bp)
        try:
            out = "".join(server._stream_cli_locked(bp, ["compile"]))
        finally:
            server._stream_cli = orig
        self.assertEqual(called, [], "тяжёлую сборку не должны были запускать")
        self.assertIn('"cancelled": true', out)
        self.assertIn('"done": true', out)
        # флаги подчищены (иначе сломают следующую сборку)
        self.assertNotIn(bp, server._cancelled)
        self.assertNotIn(bp, server._inflight)

    def test_stream_cli_reports_cancelled_and_deregisters(self):
        import subprocess as sp
        bp = os.path.join(server.BUILD_PATH, "proj-xyz")
        orig = sp.Popen
        sp.Popen = lambda *a, **k: FakePopen(lines=["Compiling sketch\n"], rc=1)
        server._cancelled.add(bp)
        try:
            events = list(server._stream_cli(["compile", "--build-path", bp, "x"], bp=bp))
        finally:
            sp.Popen = orig
        done = json.loads(events[-1].split("data: ", 1)[1].strip())
        self.assertTrue(done["done"])
        self.assertFalse(done["ok"])        # отмена ⇒ не «успех»
        self.assertTrue(done["cancelled"])
        with server._procs_guard:            # процесс снят с регистрации
            self.assertNotIn(bp, server._procs)

    def test_stream_cli_normal_run_ok(self):
        import subprocess as sp
        bp = os.path.join(server.BUILD_PATH, "proj-ok")
        orig = sp.Popen
        sp.Popen = lambda *a, **k: FakePopen(lines=["Sketch uses 1 bytes\n"], rc=0)
        try:
            events = list(server._stream_cli(["compile", "--build-path", bp, "x"], bp=bp))
        finally:
            sp.Popen = orig
        done = json.loads(events[-1].split("data: ", 1)[1].strip())
        self.assertTrue(done["ok"])
        self.assertFalse(done["cancelled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
