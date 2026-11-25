# -*- coding: utf-8 -*-
"""
Тесты Linux-специфики сборки (issue #2: запуск на AMD + свежей Mesa):

  * build.prune_linux_graphics_stack вычищает из _internal драйверный стек
    (libdrm, libglvnd, Mesa GBM) и НЕ трогает остальное (Qt6, Python);
  * build.make_linux_launcher заменяет входной бинарь обёрткой
    installer/linux/UNI-IDE.sh (LF, исполняемая), настоящий бинарь
    переименовывается в UNI-IDE.bin;
  * сама обёртка синтаксически корректна (bash -n) и умеет все
    задокументированные режимы UNI_IDE_GL.

Запуск из корня проекта:
    python -m unittest discover -s tests -v

Тесты платформенно-независимы: build.py на Windows не выполняет Linux-шаги
в main(), но сами функции работают и в режиме «песочницы» над временной
папкой. bash -n пропускается там, где нет bash.
"""

import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import build    # noqa: E402


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# То, что PyInstaller реально собирал с AlmaLinux 8 (из анализа issue #2),
# рядом — то, что остаться ДОЛЖНО.
PRUNED = [
    "libdrm.so.2", "libdrm_amdgpu.so.1", "libdrm_intel.so.1",
    "libdrm_nouveau.so.2", "libdrm_radeon.so.1",
    "libgbm.so.1", "libglapi.so.0", "libxshmfence.so.1",
    "libGL.so.1", "libGLX.so.0", "libGLdispatch.so.0",
    "libEGL.so.1", "libOpenGL.so.0", "libGLESv2.so.2",
]
KEPT = [
    "libpython3.12.so.1.0",
    "libQt6Core.so.6", "libQt6OpenGL.so.6",     # Qt6 — не трогаем
    "libQt6WebEngineCore.so.6",
    "libX11.so.6", "libxcb.so.1",               # X11-стек остаётся в бандле
    "libnss3.so", "libasound.so.2",             # зависимости QtWebEngine
    "libGLU.so.1",                              # НЕ совпадает с 'libGL.so'
]


class TestPruneGraphicsStack(unittest.TestCase):
    """Вычистка системного GL-стека из _internal (корень, не подпапки)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uni-prune-")
        self.internal = os.path.join(self.tmp, "dist", "UNI-IDE", "_internal")
        for name in PRUNED + KEPT:
            _touch(os.path.join(self.internal, name))
        # библиотеки wheel PyQt6-Qt6 живут глубже — их чистка не касается
        _touch(os.path.join(self.internal, "PyQt6", "Qt6", "lib",
                            "libQt6Gui.so.6"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self):
        dist = os.path.join(self.tmp, "dist", "UNI-IDE")
        with mock.patch.object(build, "DIST_DIR", dist):
            build.prune_linux_graphics_stack()

    def test_driver_stack_removed(self):
        self._run()
        for name in PRUNED:
            self.assertFalse(os.path.exists(os.path.join(self.internal, name)),
                             name + " должен быть вычищен")

    def test_everything_else_kept(self):
        self._run()
        for name in KEPT:
            self.assertTrue(os.path.isfile(os.path.join(self.internal, name)),
                            name + " трогать нельзя")
        self.assertTrue(os.path.isfile(os.path.join(
            self.internal, "PyQt6", "Qt6", "lib", "libQt6Gui.so.6")))

    def test_no_internal_dir_is_not_an_error(self):
        shutil.rmtree(self.internal)
        self._run()      # просто не должно упасть


class TestLinuxLauncher(unittest.TestCase):
    """Обёртка UNI-IDE.sh устанавливается входным файлом бандла."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="uni-launcher-")
        self.dist = os.path.join(self.tmp, "UNI-IDE")
        _touch(os.path.join(self.dist, "UNI-IDE"), b"fake-binary")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_binary_renamed_and_wrapper_installed(self):
        with mock.patch.object(build, "DIST_DIR", self.dist):
            build.make_linux_launcher()
        wrapper = os.path.join(self.dist, "UNI-IDE")
        real = os.path.join(self.dist, "UNI-IDE.bin")
        with open(real, "rb") as f:
            self.assertEqual(f.read(), b"fake-binary")
        with open(wrapper, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"#!/bin/bash"), "обёртка — bash-скрипт")
        self.assertNotIn(b"\r\n", data, "скрипт обязан быть с LF")
        if os.name != "nt":
            self.assertTrue(os.access(wrapper, os.X_OK), "обёртка исполняемая")
            self.assertTrue(os.access(real, os.X_OK), "бинарь исполняемый")

    def test_missing_binary_fails(self):
        os.remove(os.path.join(self.dist, "UNI-IDE"))
        with mock.patch.object(build, "DIST_DIR", self.dist):
            with self.assertRaises(SystemExit):
                build.make_linux_launcher()

    def test_wrapper_documents_all_modes(self):
        with open(build.LAUNCHER_SRC, encoding="utf-8") as f:
            src = f.read()
        for mode in ("auto", "software", "hardware"):
            self.assertIn(mode, src)
        # переменная управления режимом задокументирована и используется
        self.assertIn("UNI_IDE_GL", src)
        # журнал запусков — там, где обещано в README
        self.assertIn(".local/state", src)

    @unittest.skipIf(os.name == "nt", "bash -n проверяется в CI на Linux/macOS")
    @unittest.skipUnless(shutil.which("bash"), "нет bash")
    def test_wrapper_syntax(self):
        import subprocess
        r = subprocess.run(["bash", "-n", build.LAUNCHER_SRC],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class TestWorkflowGuards(unittest.TestCase):
    """CI обязан ловить регресс: драйверный стек в бандле = падение сборки."""

    def test_workflow_checks_pruned_libs(self):
        wf = os.path.join(build.HERE, ".github", "workflows", "build.yml")
        with open(wf, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("видеодрайверный стек не попал в бандл", text)
        for pat in ("libdrm", "libGL.so", "libEGL.so", "libGLdispatch"):
            self.assertIn(pat, text, pat + " должен проверяться в workflow")
        # системный GL-стек для smoke-теста ставится пакетами (как на
        # настольном Linux), а не берётся из бандла
        self.assertRegex(text, re.compile(r"apt-get install[^\n]*libegl1"))
        self.assertRegex(text, re.compile(r"apt-get install[^\n]*libgles2"))


if __name__ == "__main__":
    unittest.main()
