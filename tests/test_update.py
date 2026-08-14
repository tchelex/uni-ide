# -*- coding: utf-8 -*-
"""
Тесты автообновления: разбор версий, staging payload-архива (server.py)
и применение/откат обновления загрузчиком (bootstrap.py).

Без сети: все операции — с локальными файлами во временных папках.
Запуск:  python -m unittest discover -s tests -v
"""

import os
import sys
import json
import shutil
import zipfile
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server     # noqa: E402
import bootstrap  # noqa: E402


class TestVersions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(server.parse_ver("1.2.3"), (1, 2, 3))
        self.assertEqual(server.parse_ver("v1.2.10"), (1, 2, 10))
        self.assertIsNone(server.parse_ver(""))
        self.assertIsNone(server.parse_ver("abc"))

    def test_newer(self):
        self.assertTrue(server.ver_newer("1.2.10", "1.2.9"))    # не лексикографически!
        self.assertTrue(server.ver_newer("1.3.0", "1.2.10"))
        self.assertFalse(server.ver_newer("1.2.2", "1.2.2"))
        self.assertFalse(server.ver_newer("1.2.1", "1.2.2"))
        self.assertFalse(server.ver_newer("мусор", "1.0.0"))


class UpdateSandbox(unittest.TestCase):
    """Песочница: подменяет UPDATES_DIR/BASE_VERSION и чистит состояние."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uni-upd-")
        self._saved = (server.UPDATES_DIR, server.BASE_VERSION)
        server.UPDATES_DIR = os.path.join(self.tmp, "updates")
        server.BASE_VERSION = "1.3.0"
        with server._upd_lock:
            server._upd_state.update(
                {"checking": False, "staged": None, "need_full": None, "worker": False})

    def tearDown(self):
        server.UPDATES_DIR, server.BASE_VERSION = self._saved
        with server._upd_lock:
            server._upd_state.update(
                {"checking": False, "staged": None, "need_full": None, "worker": False})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_zip(self, version, min_base="1.3.0", files=None, meta=True):
        files = files if files is not None else {
            "server.py": "print('new')\n",
            "index.html": "<html>new</html>",
            "vendor/lib/x.js": "// new",
        }
        p = os.path.join(self.tmp, f"payload-{version}.zip")
        with zipfile.ZipFile(p, "w") as z:
            for name, text in files.items():
                z.writestr(name, text)
            if meta:
                z.writestr("update.json",
                           json.dumps({"version": version, "min_base": min_base}))
        return p


class TestStaging(UpdateSandbox):
    def test_stage_ok(self):
        ok, what = server.stage_payload_zip(self.make_zip("1.3.1"), "1.3.1")
        self.assertTrue(ok)
        self.assertEqual(what, "staged")
        pending = os.path.join(server.UPDATES_DIR, "pending")
        self.assertTrue(os.path.exists(os.path.join(pending, "server.py")))
        self.assertTrue(os.path.exists(os.path.join(pending, "vendor", "lib", "x.js")))
        st = server._upd_state
        self.assertEqual(st["staged"], "1.3.1")
        self.assertIsNone(st["need_full"])

    def test_min_base_gate_requires_full_installer(self):
        ok, what = server.stage_payload_zip(self.make_zip("2.0.0", min_base="2.0.0"))
        self.assertTrue(ok)
        self.assertEqual(what, "need_full")
        self.assertFalse(os.path.exists(os.path.join(server.UPDATES_DIR, "pending")))
        self.assertEqual(server._upd_state["need_full"], "2.0.0")
        self.assertIsNone(server._upd_state["staged"])

    def test_version_mismatch_rejected(self):
        ok, _ = server.stage_payload_zip(self.make_zip("1.3.1"), expect_version="1.9.9")
        self.assertFalse(ok)

    def test_zip_slip_rejected(self):
        p = os.path.join(self.tmp, "evil.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("../evil.txt", "boom")
            z.writestr("update.json", json.dumps({"version": "1.3.1"}))
        ok, _ = server.stage_payload_zip(p)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_missing_meta_rejected(self):
        ok, _ = server.stage_payload_zip(self.make_zip("1.3.1", meta=False))
        self.assertFalse(ok)


class TestBootstrapApply(unittest.TestCase):
    """Применение pending загрузчиком + откаты."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="uni-boot-")
        self._write("server.py", "OLD-SERVER")
        self._write("index.html", "OLD-HTML")
        self._write(os.path.join("vendor", "x.js"), "OLD-JS")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _write(self, rel, text):
        p = os.path.join(self.base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def _read(self, rel):
        with open(os.path.join(self.base, rel), encoding="utf-8") as f:
            return f.read()

    def _make_pending(self):
        pend = os.path.join(self.base, "updates", "pending")
        for rel, text in [("server.py", "NEW-SERVER"),
                          (os.path.join("vendor", "x.js"), "NEW-JS"),
                          ("update.json", '{"version":"1.3.1"}')]:
            p = os.path.join(pend, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)

    def test_apply_pending(self):
        self._make_pending()
        self.assertEqual(bootstrap.apply_pending(self.base), "applied")
        self.assertEqual(self._read("server.py"), "NEW-SERVER")
        self.assertEqual(self._read(os.path.join("vendor", "x.js")), "NEW-JS")
        self.assertEqual(self._read("index.html"), "OLD-HTML")   # не входил в payload
        upd = os.path.join(self.base, "updates")
        self.assertFalse(os.path.exists(os.path.join(upd, "pending")))
        self.assertFalse(os.path.exists(os.path.join(upd, "applying.marker")))
        # прежние файлы — в backup (для отката при сбое старта)
        self.assertEqual(
            open(os.path.join(upd, "backup", "server.py"), encoding="utf-8").read(),
            "OLD-SERVER")

    def test_nothing_to_apply(self):
        self.assertIsNone(bootstrap.apply_pending(self.base))

    def test_rollback_after_failed_start(self):
        self._make_pending()
        self.assertEqual(bootstrap.apply_pending(self.base), "applied")
        # сценарий: новый server.py упал на старте → bootstrap откатывает
        self.assertTrue(bootstrap.restore_backup(self.base))
        self.assertEqual(self._read("server.py"), "OLD-SERVER")
        self.assertEqual(self._read(os.path.join("vendor", "x.js")), "OLD-JS")

    def test_interrupted_apply_rolls_back(self):
        # крэш посреди прошлого применения: остались marker, backup и pending
        upd = os.path.join(self.base, "updates")
        os.makedirs(os.path.join(upd, "backup"), exist_ok=True)
        with open(os.path.join(upd, "backup", "server.py"), "w", encoding="utf-8") as f:
            f.write("OLD-SERVER")
        self._make_pending()
        with open(os.path.join(upd, "applying.marker"), "w") as f:
            f.write("applying")
        self._write("server.py", "HALF-APPLIED")

        self.assertEqual(bootstrap.apply_pending(self.base), "rolled_back")
        self.assertEqual(self._read("server.py"), "OLD-SERVER")
        self.assertFalse(os.path.exists(os.path.join(upd, "pending")))
        self.assertFalse(os.path.exists(os.path.join(upd, "applying.marker")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
