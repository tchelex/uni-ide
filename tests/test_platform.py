# -*- coding: utf-8 -*-
"""
Тесты кроссплатформенной части: раздельная раскладка каталогов macOS/Linux
(загрузчик + «ферма ссылок» на тулчейн), фильтр портов, выбор сетевого порта,
остановка дерева процессов.

Запуск из корня проекта:
    python -m unittest discover -s tests -v

Тесты, которым нужны симлинки или POSIX-сигналы, на Windows пропускаются —
они выполняются в GitHub Actions на macOS и Linux.
"""

import os
import sys
import time
import shutil
import socket
import tempfile
import unittest
import subprocess
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server     # noqa: E402
import bootstrap  # noqa: E402

POSIX = os.name != "nt"


def _write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestSeedApp(unittest.TestCase):
    """Загрузчик: рабочий payload в каталоге состояния (macOS/Linux)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uni-seed-")
        self.factory = os.path.join(self.tmp, "bundle", "uni")
        self.state = os.path.join(self.tmp, "state")
        _write(os.path.join(self.factory, "app", "server.py"), 'VERSION = "1.5.0"\n')
        _write(os.path.join(self.factory, "app", "index.html"), "v1.5.0")
        _write(os.path.join(self.factory, "app", "vendor", "a.js"), "js")
        _write(os.path.join(self.factory, "base-version.txt"), "1.5.0")
        _write(os.path.join(self.factory, "build-id.txt"), "1.5.0+A")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_run_copies_factory_payload(self):
        self.assertTrue(bootstrap.seed_app(self.factory, self.state))
        self.assertEqual(_read(os.path.join(self.state, "app", "index.html")), "v1.5.0")
        self.assertTrue(os.path.isfile(os.path.join(self.state, "app", "vendor", "a.js")))

    def test_second_run_keeps_updated_payload(self):
        bootstrap.seed_app(self.factory, self.state)
        # автообновление заменило файл в рабочем payload
        _write(os.path.join(self.state, "app", "index.html"), "v1.6.0")
        self.assertFalse(bootstrap.seed_app(self.factory, self.state))
        self.assertEqual(_read(os.path.join(self.state, "app", "index.html")), "v1.6.0")

    def test_new_full_install_resets_payload_and_updates(self):
        bootstrap.seed_app(self.factory, self.state)
        _write(os.path.join(self.state, "app", "index.html"), "v1.6.0")
        _write(os.path.join(self.state, "updates", "pending", "server.py"), "old pending")
        _write(os.path.join(self.state, "arduino-user", "libraries", "Mine", "x.h"), "keep")
        # поставили новый полный установщик: другой build-id
        _write(os.path.join(self.factory, "build-id.txt"), "1.7.0+B")
        _write(os.path.join(self.factory, "app", "index.html"), "v1.7.0")
        self.assertTrue(bootstrap.seed_app(self.factory, self.state))
        self.assertEqual(_read(os.path.join(self.state, "app", "index.html")), "v1.7.0")
        self.assertFalse(os.path.exists(os.path.join(self.state, "updates")))
        # данные пользователя не тронуты
        self.assertTrue(os.path.isfile(
            os.path.join(self.state, "arduino-user", "libraries", "Mine", "x.h")))

    def test_update_applies_into_state_dir(self):
        bootstrap.seed_app(self.factory, self.state)
        app = os.path.join(self.state, "app")
        upd = os.path.join(self.state, "updates")
        _write(os.path.join(upd, "pending", "index.html"), "v1.6.0")
        _write(os.path.join(upd, "pending", "update.json"), "{}")
        self.assertEqual(bootstrap.apply_pending(app, upd), "applied")
        self.assertEqual(_read(os.path.join(app, "index.html")), "v1.6.0")
        # заводская часть бандла не изменилась
        self.assertEqual(_read(os.path.join(self.factory, "app", "index.html")), "v1.5.0")
        # откат возвращает прежний файл
        self.assertTrue(bootstrap.restore_backup(app, upd))
        self.assertEqual(_read(os.path.join(app, "index.html")), "v1.5.0")

    def test_factory_dir_detection(self):
        bundle = os.path.dirname(self.factory)
        with mock.patch.object(sys, "platform", "linux"):
            self.assertEqual(bootstrap.factory_dir(bundle), os.path.realpath(self.factory))
            self.assertIsNone(bootstrap.factory_dir(self.tmp))
        with mock.patch.object(sys, "platform", "win32"):
            # Windows всегда живёт одной папкой рядом с exe
            self.assertIsNone(bootstrap.factory_dir(bundle))

    def test_mac_bundle_layout(self):
        # UNI-IDE.app/Contents/MacOS/<exe> -> UNI-IDE.app/Contents/Resources/uni
        contents = os.path.join(self.tmp, "UNI-IDE.app", "Contents")
        macos = os.path.join(contents, "MacOS")
        os.makedirs(macos)
        shutil.copytree(self.factory, os.path.join(contents, "Resources", "uni"))
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(bootstrap.factory_dir(macos),
                             os.path.realpath(os.path.join(contents, "Resources", "uni")))

    def test_state_dir_per_platform(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("UNI_STATE_DIR", "XDG_DATA_HOME")}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertTrue(bootstrap.state_dir().endswith(
                    os.path.join("Library", "Application Support", "UNI-IDE")))
            with mock.patch.object(sys, "platform", "linux"):
                self.assertTrue(bootstrap.state_dir().endswith(
                    os.path.join(".local", "share", "UNI-IDE")))
        with mock.patch.dict(os.environ, {"UNI_STATE_DIR": "/x/y"}):
            self.assertEqual(bootstrap.state_dir(), "/x/y")


class TestWindowsLayoutUnchanged(unittest.TestCase):
    """Без переменных загрузчика всё лежит одной папкой — как в установленных
    копиях на Windows (их загрузчик 1.3.0 о новых переменных не знает)."""

    def test_single_folder_layout(self):
        if any(os.environ.get(k) for k in ("UNI_APP_DIR", "UNI_TOOL_DIR", "UNI_STATE_DIR")):
            self.skipTest("запущено с переменными раздельной раскладки")
        self.assertFalse(server.SPLIT_LAYOUT)
        self.assertEqual(server.BASE, server.TOOL_DIR)
        self.assertEqual(server.BASE, server.STATE_DIR)
        self.assertEqual(server.UPDATES_DIR, os.path.join(server.BASE, "updates"))
        self.assertEqual(server.BUILD_PATH, os.path.join(server.BASE, "build-tmp"))
        self.assertEqual(server.PORTABLE_DATA, os.path.join(server.BASE, "arduino-data"))

    def test_prepare_portable_dirs_is_noop(self):
        with mock.patch.object(server, "SPLIT_LAYOUT", False), \
             mock.patch.object(os, "symlink", side_effect=AssertionError("не должно вызываться")):
            server.prepare_portable_dirs()


@unittest.skipUnless(POSIX, "нужны симлинки (macOS/Linux)")
class TestLinkFarm(unittest.TestCase):
    """server.prepare_portable_dirs: изменяемый arduino-data поверх заводского."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uni-farm-")
        self.tool = os.path.join(self.tmp, "uni")
        self.state = os.path.join(self.tmp, "state")
        _write(os.path.join(self.tool, "arduino-data", "packages", "esp32", "big.bin"), "B")
        _write(os.path.join(self.tool, "arduino-data", "package_index.json"), "{}")
        _write(os.path.join(self.tool, "arduino-data", "tmp", "junk"), "j")
        _write(os.path.join(self.tool, "arduino-user", "libraries", "UNI", "UNI.h"), "v1")
        _write(os.path.join(self.tool, "build-id.txt"), "1.5.0+A")
        self._p = [
            mock.patch.object(server, "SPLIT_LAYOUT", True),
            mock.patch.object(server, "IS_PORTABLE", True),
            mock.patch.object(server, "TOOL_DIR", self.tool),
            mock.patch.object(server, "STATE_DIR", self.state),
            mock.patch.object(server, "FACTORY_DATA", os.path.join(self.tool, "arduino-data")),
            mock.patch.object(server, "FACTORY_USER", os.path.join(self.tool, "arduino-user")),
            mock.patch.object(server, "PORTABLE_DATA", os.path.join(self.state, "arduino-data")),
            mock.patch.object(server, "PORTABLE_USER", os.path.join(self.state, "arduino-user")),
        ]
        for p in self._p:
            p.start()
        os.makedirs(self.state)

    def tearDown(self):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_links_big_dirs_copies_small_files(self):
        server.prepare_portable_dirs()
        data = os.path.join(self.state, "arduino-data")
        self.assertTrue(os.path.islink(os.path.join(data, "packages")))
        self.assertEqual(_read(os.path.join(data, "packages", "esp32", "big.bin")), "B")
        idx = os.path.join(data, "package_index.json")
        self.assertTrue(os.path.isfile(idx) and not os.path.islink(idx))
        self.assertFalse(os.path.exists(os.path.join(data, "tmp")))
        # каталог данных пригоден для записи (arduino-cli пишет туда индексы)
        _write(os.path.join(data, "inventory.yaml"), "id")

    def test_relinks_after_app_moved(self):
        server.prepare_portable_dirs()
        moved = os.path.join(self.tmp, "moved")
        shutil.move(self.tool, moved)
        with mock.patch.object(server, "TOOL_DIR", moved), \
             mock.patch.object(server, "FACTORY_DATA", os.path.join(moved, "arduino-data")), \
             mock.patch.object(server, "FACTORY_USER", os.path.join(moved, "arduino-user")):
            server.prepare_portable_dirs()
        link = os.path.join(self.state, "arduino-data", "packages")
        self.assertEqual(os.readlink(link), os.path.join(moved, "arduino-data", "packages"))
        self.assertEqual(_read(os.path.join(link, "esp32", "big.bin")), "B")

    def test_bundled_libs_seeded_and_user_libs_kept(self):
        server.prepare_portable_dirs()
        libs = os.path.join(self.state, "arduino-user", "libraries")
        self.assertEqual(_read(os.path.join(libs, "UNI", "UNI.h")), "v1")
        _write(os.path.join(libs, "Mine", "m.h"), "mine")
        # новая полная установка привезла свежую UNI
        _write(os.path.join(self.tool, "arduino-user", "libraries", "UNI", "UNI.h"), "v2")
        _write(os.path.join(self.tool, "build-id.txt"), "1.6.0+B")
        server.prepare_portable_dirs()
        self.assertEqual(_read(os.path.join(libs, "UNI", "UNI.h")), "v2")
        self.assertEqual(_read(os.path.join(libs, "Mine", "m.h")), "mine")


class _Port:
    def __init__(self, device, vid=None, description="", manufacturer=""):
        self.device, self.vid = device, vid
        self.description, self.manufacturer = description, manufacturer


class TestPortFilter(unittest.TestCase):
    def test_windows_keeps_everything(self):
        with mock.patch.object(server, "IS_WIN", True):
            self.assertTrue(server._is_board_port(_Port("COM1")))
            self.assertTrue(server._port_access("COM3"))

    def test_mac_and_linux_drop_non_usb(self):
        with mock.patch.object(server, "IS_WIN", False):
            keep = [
                _Port("/dev/cu.wchusbserial1410", vid=0x1A86),
                _Port("/dev/cu.usbserial-0001"),
                _Port("/dev/cu.SLAB_USBtoUART"),
                _Port("/dev/ttyUSB0", vid=0x1A86),
                _Port("/dev/ttyACM0"),
            ]
            drop = [
                _Port("/dev/cu.Bluetooth-Incoming-Port"),
                _Port("/dev/cu.debug-console"),
                _Port("/dev/ttyS0"),
            ]
            for p in keep:
                self.assertTrue(server._is_board_port(p), p.device)
            for p in drop:
                self.assertFalse(server._is_board_port(p), p.device)

    def test_ports_api_reports_access_flag(self):
        class _LP:
            @staticmethod
            def comports():
                return [_Port("/dev/ttyUSB0", vid=0x1A86, description="USB Serial"),
                        _Port("/dev/ttyS0")]
        saved = (server.HAS_PYSERIAL, getattr(server, "list_ports", None))
        server.HAS_PYSERIAL, server.list_ports = True, _LP
        server._ports_cache["data"] = None
        try:
            with mock.patch.object(server, "IS_WIN", False), \
                 mock.patch.object(os, "access", return_value=False):
                r = server.app.test_client().get("/api/ports").get_json()
        finally:
            server.HAS_PYSERIAL, server.list_ports = saved
            server._ports_cache["data"] = None
        self.assertEqual([p["addr"] for p in r["ports"]], ["/dev/ttyUSB0"])
        self.assertTrue(r["ports"][0]["ch340"])
        self.assertFalse(r["ports"][0]["access"])


class TestChoosePort(unittest.TestCase):
    def test_forced_port(self):
        with mock.patch.dict(os.environ, {"UNI_PORT": "5123"}):
            self.assertEqual(server.choose_port(), (5123, False))

    def _clean_env(self):
        return mock.patch.dict(os.environ, {"UNI_PORT": ""})

    def test_windows_always_5000(self):
        with self._clean_env(), mock.patch.object(server, "IS_WIN", True), \
             mock.patch.object(server, "_port_is_free", return_value=False):
            self.assertEqual(server.choose_port(), (5000, False))

    def test_posix_free_5000(self):
        with self._clean_env(), mock.patch.object(server, "IS_WIN", False), \
             mock.patch.object(server, "_port_is_free", return_value=True):
            self.assertEqual(server.choose_port(), (5000, False))

    def test_posix_attaches_to_running_copy(self):
        with self._clean_env(), mock.patch.object(server, "IS_WIN", False), \
             mock.patch.object(server, "_port_is_free", return_value=False), \
             mock.patch.object(server, "_our_server_on", return_value=True):
            self.assertEqual(server.choose_port(), (5000, True))

    def test_posix_foreign_program_on_5000(self):
        # например, «Приёмник AirPlay» на macOS
        with self._clean_env(), mock.patch.object(server, "IS_WIN", False), \
             mock.patch.object(server, "_port_is_free", return_value=False), \
             mock.patch.object(server, "_our_server_on", return_value=False):
            port, attached = server.choose_port()
        self.assertFalse(attached)
        self.assertNotEqual(port, 5000)
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))        # порт действительно свободен
        finally:
            s.close()

    def test_port_is_free_detects_listener(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertFalse(server._port_is_free(s.getsockname()[1]))
        finally:
            s.close()


@unittest.skipUnless(POSIX, "группы процессов POSIX (macOS/Linux)")
class TestKillTree(unittest.TestCase):
    def test_stop_kills_children_too(self):
        # «arduino-cli» с дочерним «компилятором»: sh запускает sleep и ждёт
        marker = "uni-kill-test-%d" % os.getpid()
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 300 & echo $! ; exec -a %s sleep 300" % marker],
            stdout=subprocess.PIPE, text=True, **server._popen_extra())
        child_pid = int(proc.stdout.readline().strip())
        server._kill_tree(proc)
        self.assertIsNotNone(proc.poll())
        time.sleep(0.3)
        with self.assertRaises(OSError):
            os.kill(child_pid, 0)              # потомок тоже снят

    def test_nice_prefix_used_for_warmup(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(subprocess, "run", side_effect=fake_run):
            server.run_cli(["version"], nice=True)
        if server._NICE:
            self.assertEqual(seen["cmd"][:3], [server._NICE, "-n", "10"])


if __name__ == "__main__":
    unittest.main()
