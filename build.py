#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — сборка UNI IDE в исполняемое приложение и установщик.

Кроссплатформенный оркестратор сборки. Собирает ТОЛЬКО под ту систему, на
которой запущен (PyInstaller и тулчейн ESP32 не умеют кросс-сборку):

  Windows → dist/UNI-IDE/UNI-IDE.exe
            installer-out/UNI-IDE-Setup-<версия>.exe      (Inno Setup)
            installer-out/payload-<версия>.zip (+ .sha256) — автообновление
  macOS   → dist/UNI-IDE.app
            installer-out/UNI-IDE-<версия>-macos-<arm64|x64>.dmg
  Linux   → dist/UNI-IDE/UNI-IDE
            installer-out/UNI-IDE-<версия>-linux-<x86_64|aarch64>.tar.xz
            (внутри — программа, install.sh и uninstall.sh)

Сборки macOS и Linux делает GitHub Actions (.github/workflows/build.yml) —
свой Mac или Linux-машина для этого не нужны.

ПЕРЕД ПЕРВОЙ СБОРКОЙ один раз выполните (нужен интернет):
     python prepare_bundle.py
        — скачивает arduino-cli и ядро ESP32 в ./arduino-data

ЗАТЕМ:
     python build.py                 # бандл + установщик/образ/архив
     python build.py --no-installer  # только бандл в dist/

ТРЕБОВАНИЯ:
  - Python 3.9+
  - Windows: Inno Setup 6. Установить: https://jrsoftware.org/isdl.php
    или в терминале:  winget install JRSoftware.InnoSetup
  - macOS: штатные sips, iconutil, codesign, hdiutil (есть в системе)
  - Linux: tar с поддержкой xz
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import zipfile
import argparse
import platform
import subprocess

# --------------------------------------------------------------------------- #
# Параметры сборки
# --------------------------------------------------------------------------- #
APP_NAME = "UNI-IDE"        # имя exe и папки бандла (без пробелов)
APP_TITLE = "UNI IDE"       # имя для людей (том DMG, пункт меню)
BUNDLE_ID = "tech.uni-lab.ide"

HERE     = os.path.dirname(os.path.abspath(__file__))

IS_WIN   = sys.platform == "win32"
IS_MAC   = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _read_version():
    """Единый источник версии — VERSION в server.py (читаем без импорта)."""
    try:
        with open(os.path.join(HERE, "server.py"), encoding="utf-8") as f:
            m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', f.read(), re.M)
        if m:
            return m.group(1)
    except Exception:
        pass
    return "0.0.0"


VERSION  = _read_version()  # передаётся в Inno Setup и в имя установщика

# Минимальная версия ПОЛНОЙ установки, на которую применим лёгкий payload-апдейт.
# Поднимать ТОЛЬКО при несовместимых изменениях bootstrap.py или python-зависимостей
# (flask/pyserial/pywebview): тогда старые установки увидят «нужен полный установщик».
PAYLOAD_MIN_BASE = "1.3.0"


def _arch():
    m = platform.machine().lower()
    arm = m in ("arm64", "aarch64")
    if IS_MAC:
        return "arm64" if arm else "x64"
    return "aarch64" if arm else "x86_64"


ARCH = _arch()

DIST_ROOT = os.path.join(HERE, "dist")
DIST_DIR  = os.path.join(DIST_ROOT, APP_NAME)             # выход PyInstaller (--onedir)
MAC_APP   = os.path.join(DIST_ROOT, APP_NAME + ".app")    # macOS-бандл
OUT_DIR   = os.path.join(HERE, "installer-out")           # куда кладём установщики
ISS_FILE  = os.path.join(HERE, "installer", "UNI-IDE.iss")
LINUX_DIR = os.path.join(HERE, "installer", "linux")
MAC_DIR   = os.path.join(HERE, "installer", "macos")

# Загрузчик Linux-бандла (обёртка с авто-выбором графического режима, см. файл).
LAUNCHER_SRC = os.path.join(LINUX_DIR, "UNI-IDE.sh")

# Ресурсы приложения. server.py — тоже обычный файл: загрузчик (bootstrap.py)
# запускает его с диска, что и делает возможными лёгкие payload-обновления
# без переустановки.
RES_FILES = ["server.py", "index.html", "icon.ico", "icon.png", "student-README.txt"]
# vendor — CodeMirror и шрифты; sim — модули 2D-симулятора; image — мнемосхемы
# конструктора (UniBase/UniDev) для конфигуратора исполнителя.
RES_DIRS  = ["vendor", "sim", "image"]

# Состав payload-обновления (см. bootstrap.py и автоапдейт в server.py).
PAYLOAD_FILES = ["server.py", "index.html", "icon.ico", "icon.png", "student-README.txt"]
PAYLOAD_DIRS  = ["vendor", "sim", "image"]

# Переносимый тулчейн (создаётся prepare_bundle.py).
CLI_NAME        = "arduino-cli.exe" if IS_WIN else "arduino-cli"
TOOLCHAIN_FILES = [CLI_NAME]
TOOLCHAIN_DIRS  = ["arduino-data", "arduino-user"]

# Папки тулчейна, которые НЕ кладём в бандл — IDE жёстко нацелена только на
# классический ESP32 Dev Module (Xtensa, FQBN esp32:esp32:esp32). Это срезает
# ~4 ГБ установленного размера. Проверено: blink-скетч компилируется без них.
COPY_IGNORE = shutil.ignore_patterns(
    # SDK протокола Matter («умный дом»): не нужен и создаёт пути >260 символов,
    # из-за чего падают упаковка Inno Setup и установка без long-path поддержки.
    "espressif__esp_matter",
    # RISC-V компилятор и отладчик — для Xtensa-ESP32 не используются.
    "esp-rv32", "riscv32-esp-elf-gdb",
    # precompiled-библиотеки других вариантов чипов (нужен только esp32-libs).
    "esp32c3-libs", "esp32c5-libs", "esp32c6-libs", "esp32h2-libs",
    "esp32p4-libs", "esp32p4_es-libs", "esp32s2-libs", "esp32s3-libs",
    # мусор Finder
    ".DS_Store",
)


def log(msg):
    print("[build] " + msg, flush=True)


def keep_system_awake():
    """Не даём Windows уснуть во время долгой сборки. Сбрасывается при выходе."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        except Exception:
            pass


def run(cmd, **kw):
    log("> " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, check=True, **kw)


# --------------------------------------------------------------------------- #
# Шаги
# --------------------------------------------------------------------------- #
def ensure_toolchain():
    """Проверяет, что переносимый тулчейн запечён (prepare_bundle.py)."""
    cli  = os.path.join(HERE, CLI_NAME)
    data = os.path.join(HERE, "arduino-data")
    if not (os.path.exists(cli) and os.path.isdir(data)):
        log("ОШИБКА: не найден переносимый тулчейн (arduino-cli + arduino-data).")
        log("Сначала выполните (нужен интернет):  python prepare_bundle.py")
        sys.exit(1)


def python_deps():
    """Зависимости для сборки. На Linux окно рисует собственный Qt WebEngine
    (вшивается в бандл) — так программа не зависит от набора GTK/WebKit в
    конкретном дистрибутиве."""
    deps = ["pyinstaller", "flask", "pyserial", "pywebview"]
    if IS_LINUX:
        deps += ["qtpy", "PyQt6", "PyQt6-WebEngine"]
    return deps


def make_icns():
    """macOS: собирает icon.icns из icon.png штатными sips + iconutil."""
    src = os.path.join(HERE, "icon.png")
    work = os.path.join(HERE, "build", "UNI-IDE.iconset")
    out = os.path.join(HERE, "build", "icon.icns")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}" + ("@2x" if scale == 2 else "") + ".png"
            run(["sips", "-z", str(px), str(px), src, "--out", os.path.join(work, name)],
                stdout=subprocess.DEVNULL)
    run(["iconutil", "-c", "icns", work, "-o", out])
    return out


def pyinstaller_build(install_deps=True):
    """Собирает бандл PyInstaller: onedir на Windows/Linux, .app на macOS."""
    if install_deps:
        pip = [sys.executable, "-m", "pip", "install", "--upgrade"]
        if IS_LINUX:
            # только готовые колёса: pip сам возьмёт самую новую версию PyQt6,
            # у которой есть сборка под glibc этой системы (а не полезет
            # компилировать Qt из исходников)
            pip += ["--only-binary",
                    "PyQt6,PyQt6-Qt6,PyQt6-sip,PyQt6-WebEngine,PyQt6-WebEngine-Qt6"]
        run(pip + python_deps())

    # чистим прошлый dist, чтобы не тащить устаревшие файлы
    if os.path.isdir(DIST_ROOT):
        shutil.rmtree(DIST_ROOT, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onedir", "--noconsole", "--name", APP_NAME,
           # pywebview подтягивает свой GUI-бэкенд динамически — забираем целиком
           "--collect-all", "webview",
           "--collect-submodules", "serial",
           # flask и его свита: bootstrap.py импортирует их явно, но подстрахуемся
           "--hidden-import", "flask",
           "--hidden-import", "werkzeug",
           "--hidden-import", "jinja2"]
    if IS_MAC:
        cmd += ["--osx-bundle-identifier", BUNDLE_ID, "--icon", make_icns()]
    elif IS_LINUX:
        # GTK-бэкенд pywebview не берём: в бандле только Qt
        cmd += ["--exclude-module", "gi",
                "--hidden-import", "qtpy",
                "--hidden-import", "PyQt6.QtWebEngineWidgets",
                "--hidden-import", "PyQt6.QtWebEngineCore",
                "--hidden-import", "PyQt6.QtWebChannel",
                "--hidden-import", "PyQt6.QtNetwork"]
    else:
        icon = os.path.join(HERE, "icon.ico")
        if os.path.exists(icon):
            cmd += ["--icon", icon]
    # Вход — тонкий загрузчик; server.py остаётся обычным файлом вне exe
    cmd += [os.path.join(HERE, "bootstrap.py")]
    run(cmd, cwd=HERE)

    built = MAC_APP if IS_MAC else DIST_DIR
    if not os.path.isdir(built):
        log("ОШИБКА: PyInstaller не создал " + built)
        sys.exit(1)


# --------------------------------------------------------------------------- #
# Linux: системный графический стек и загрузчик
# --------------------------------------------------------------------------- #
# PyInstaller складывает в _internal/ и системные библиотеки, от которых
# зависят Qt6-колёса. Часть из них — это ДРАЙВЕРНЫЙ стек (libglvnd/Mesa/DRM):
# бутлоадер ставит _internal первым в LD_LIBRARY_PATH, и собранные на
# AlmaLinux 8 libdrm/libGL/libEGL затеняют более новые системные. На свежих
# Mesa (radeonsi, iris) и проприетарном NVIDIA это ломает согласование
# GLX/EGL — «Failed to finding matching FBConfig», «Could not initialize GLX»
# и падение с SIGABRT (см. issue #2). На любом настольном Linux эти
# библиотеки уже есть как часть видеодрайвера, поэтому из бандла их убираем:
# линкер возьмёт системные копии. Чистим ТОЛЬКО корень _internal/ — Qt6 из
# wheel PyQt6-Qt6 (PyQt6/Qt6/...) не трогаем.
LINUX_GL_PRUNE = (
    "libdrm",         # libdrm.so.2, libdrm_amdgpu/intel/nouveau/radeon…
    "libgbm",         # Mesa GBM
    "libglapi",       # диспетчер старой Mesa
    "libxshmfence",   # хелпер libdrm/Mesa
    "libGL.so",       # libglvnd…
    "libGLX",
    "libEGL.so",
    "libGLdispatch",
    "libOpenGL",
    "libGLESv2",
)


def prune_linux_graphics_stack():
    """Убирает собранный системный GL-стек из корня dist/UNI-IDE/_internal."""
    internal = os.path.join(DIST_DIR, "_internal")
    if not os.path.isdir(internal):
        return
    removed = []
    for name in sorted(os.listdir(internal)):
        path = os.path.join(internal, name)
        if not (os.path.isfile(path) or os.path.islink(path)):
            continue
        if any(name.startswith(p) for p in LINUX_GL_PRUNE):
            os.remove(path)
            removed.append(name)
    if removed:
        log("Из бандла убран системный графический стек "
            "(на целевой системе возьмутся драйверные копии): "
            + ", ".join(removed))
    else:
        log("ВНИМАНИЕ: системный GL-стек в _internal не найден — "
            "возможно, изменился состав колёс Qt6; проверьте бандл вручную.")


def make_linux_launcher():
    """Заменяет входной бинарь shell-обёрткой (installer/linux/UNI-IDE.sh):
    настоящий бинарь переименовывается в UNI-IDE.bin, а под именем UNI-IDE
    оказывается загрузчик с авто-выбором графического режима. Бутлоадер
    PyInstaller считает пути от /proc/self/exe, поэтому переименование
    безопасно, пока .bin лежит в той же папке."""
    binary = os.path.join(DIST_DIR, APP_NAME)
    real = os.path.join(DIST_DIR, APP_NAME + ".bin")
    if not os.path.isfile(binary):
        log("ОШИБКА: не найден входной бинарь " + binary)
        sys.exit(1)
    os.replace(binary, real)
    os.chmod(real, 0o755)
    with open(LAUNCHER_SRC, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n")   # скрипты — строго с LF
    with open(binary, "wb") as f:
        f.write(data)
    os.chmod(binary, 0o755)
    log("Загрузчик Linux (авто-выбор графического режима): "
        + binary + " -> " + APP_NAME + ".bin")


def _copytree(src, dest):
    shutil.rmtree(dest, ignore_errors=True)
    # На macOS/Linux в тулчейне много симлинков (версии библиотек, алиасы
    # компилятора) — сохраняем их ссылками, иначе бандл раздуется в разы.
    shutil.copytree(src, dest, ignore=COPY_IGNORE, symlinks=not IS_WIN)


def _copy_set(files, dirs, dst):
    os.makedirs(dst, exist_ok=True)
    for f in files:
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, os.path.basename(f)))
        else:
            log("  (пропуск, нет файла) " + f)
    for d in dirs:
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            _copytree(src, os.path.join(dst, os.path.basename(d)))
        else:
            log("  (пропуск, нет папки) " + d)


def _write_versions(dst):
    # версия полной установки — по ней автоапдейт решает, применим ли payload
    with open(os.path.join(dst, "base-version.txt"), "w", encoding="utf-8") as f:
        f.write(VERSION)
    # отпечаток именно этой сборки — по нему macOS/Linux-загрузчик понимает,
    # что поверх поставили новый полный установщик
    with open(os.path.join(dst, "build-id.txt"), "w", encoding="utf-8") as f:
        f.write(VERSION + "+" + time.strftime("%Y%m%d%H%M%S", time.gmtime()))


def factory_root():
    """Куда в бандле кладётся заводская часть (см. схему в bootstrap.py)."""
    if IS_WIN:
        return DIST_DIR                                    # рядом с exe
    if IS_MAC:
        return os.path.join(MAC_APP, "Contents", "Resources", "uni")
    return os.path.join(DIST_DIR, "uni")


def copy_resources():
    """Кладёт ресурсы и тулчейн в бандл.
    Windows — всё рядом с exe (историческая раскладка, её ждут установленные
    копии). macOS/Linux — заводская папка uni/: payload в uni/app, тулчейн рядом."""
    root = factory_root()
    if IS_WIN:
        _copy_set(RES_FILES + TOOLCHAIN_FILES, RES_DIRS + TOOLCHAIN_DIRS, root)
    else:
        _copy_set(RES_FILES, RES_DIRS, os.path.join(root, "app"))
        _copy_set(TOOLCHAIN_FILES, TOOLCHAIN_DIRS, root)
        cli = os.path.join(root, CLI_NAME)
        if os.path.exists(cli):
            os.chmod(cli, 0o755)
    _write_versions(root)
    return root


def build_payload():
    """Собирает лёгкий payload-архив для автообновления установленных копий:
    installer-out/payload-<версия>.zip (+ .sha256). Внутри — только файлы
    приложения (server.py, index.html, vendor…) и update.json с версией и
    минимальной совместимой полной установкой (PAYLOAD_MIN_BASE).
    Payload один на все системы — в релиз кладётся тот, что собран на Windows."""
    os.makedirs(OUT_DIR, exist_ok=True)
    zpath = os.path.join(OUT_DIR, f"payload-{VERSION}.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for f in PAYLOAD_FILES:
            src = os.path.join(HERE, f)
            if os.path.exists(src):
                z.write(src, f)
        for d in PAYLOAD_DIRS:
            src = os.path.join(HERE, d)
            for root, _dirs, files in os.walk(src):
                for fn in files:
                    p = os.path.join(root, fn)
                    z.write(p, os.path.relpath(p, HERE).replace(os.sep, "/"))
        z.writestr("update.json", json.dumps(
            {"version": VERSION, "min_base": PAYLOAD_MIN_BASE},
            ensure_ascii=False, indent=2))
    h = hashlib.sha256()
    with open(zpath, "rb") as f:
        for chunk in iter(lambda: f.read(256 * 1024), b""):
            h.update(chunk)
    with open(zpath + ".sha256", "w", encoding="utf-8") as f:
        f.write(h.hexdigest() + "  " + os.path.basename(zpath) + "\n")
    log("Payload для автообновления: " + zpath)


# --------------------------------------------------------------------------- #
# Windows: Inno Setup
# --------------------------------------------------------------------------- #
def find_iscc():
    """Ищет компилятор Inno Setup (ISCC.exe) в PATH и типичных местах.
    Учитывает и per-machine (Program Files), и per-user установку
    (winget часто ставит в %LOCALAPPDATA%\\Programs)."""
    p = shutil.which("iscc") or shutil.which("ISCC")
    if p:
        return p
    local = os.environ.get("LOCALAPPDATA", os.path.expanduser(r"~\AppData\Local"))
    bases = (
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("ProgramFiles",      r"C:\Program Files"),
        os.path.join(local, "Programs"),
    )
    for base in bases:
        for ver in ("Inno Setup 6", "Inno Setup 5"):
            cand = os.path.join(base, ver, "ISCC.exe")
            if os.path.exists(cand):
                return cand
    return None


def build_installer_windows():
    """Собирает setup.exe через Inno Setup."""
    iscc = find_iscc()
    if not iscc:
        log("Inno Setup (ISCC.exe) не найден.")
        log("Установите Inno Setup 6 и повторите:")
        log("   https://jrsoftware.org/isdl.php")
        log("   или:  winget install JRSoftware.InnoSetup")
        log("Бандл собран в dist/UNI-IDE, но установщик НЕ создан.")
        sys.exit(2)

    os.makedirs(OUT_DIR, exist_ok=True)
    run([iscc,
         "/DAppVersion=" + VERSION,
         "/DSourceDir=" + DIST_DIR,
         "/DRepoDir="  + HERE,
         "/O" + OUT_DIR,
         ISS_FILE])

    setup = os.path.join(OUT_DIR, "UNI-IDE-Setup-" + VERSION + ".exe")
    log("ГОТОВО. Установщик: " + setup)


# --------------------------------------------------------------------------- #
# macOS: .app + .dmg
# --------------------------------------------------------------------------- #
def finish_mac_app():
    """Дописывает Info.plist и подписывает бандл.

    Подпись — «ad-hoc» (без сертификата разработчика): она обязательна, чтобы
    бандл считался целым после того, как мы положили в него ресурсы и тулчейн.
    Если задана переменная UNI_CODESIGN_IDENTITY (Developer ID) — подписываем ею."""
    import plistlib
    plist_path = os.path.join(MAC_APP, "Contents", "Info.plist")
    with open(plist_path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleName": APP_TITLE,
        "CFBundleDisplayName": APP_TITLE,
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.education",
        # локальный сервер на 127.0.0.1 без https
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHumanReadableCopyright": "UniBase",
    })
    with open(plist_path, "wb") as f:
        plistlib.dump(info, f)

    # расширенные атрибуты (карантин и т.п.) мешают подписи
    subprocess.run(["xattr", "-cr", MAC_APP], check=False)
    identity = os.environ.get("UNI_CODESIGN_IDENTITY", "").strip() or "-"
    cmd = ["codesign", "--force", "--deep", "--sign", identity]
    if identity != "-":
        cmd += ["--options", "runtime", "--timestamp"]
    run(cmd + [MAC_APP])
    run(["codesign", "--verify", "--deep", "--strict", MAC_APP])


def build_installer_mac():
    """Упаковывает UNI-IDE.app в сжатый .dmg со ссылкой на «Программы»."""
    os.makedirs(OUT_DIR, exist_ok=True)
    stage = os.path.join(HERE, "build", "dmg-stage")
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    # ditto сохраняет симлинки, права и подпись бандла
    run(["ditto", MAC_APP, os.path.join(stage, APP_NAME + ".app")])
    os.symlink("/Applications", os.path.join(stage, "Applications"))
    readme = os.path.join(MAC_DIR, "ПРОЧТИ МЕНЯ.txt")
    if os.path.exists(readme):
        shutil.copy2(readme, stage)

    dmg = os.path.join(OUT_DIR, f"{APP_NAME}-{VERSION}-macos-{ARCH}.dmg")
    if os.path.exists(dmg):
        os.remove(dmg)
    run(["hdiutil", "create", "-volname", APP_TITLE, "-srcfolder", stage,
         "-ov", "-format", "UDZO", "-imagekey", "zlib-level=9", dmg])
    shutil.rmtree(stage, ignore_errors=True)
    log("ГОТОВО. Образ: " + dmg)


# --------------------------------------------------------------------------- #
# Linux: .tar.xz с install.sh
# --------------------------------------------------------------------------- #
def build_installer_linux():
    """Архив: программа + install.sh/uninstall.sh. Распаковал, запустил
    install.sh — появился ярлык в меню; права администратора нужны только
    на необязательный шаг «доступ к плате»."""
    os.makedirs(OUT_DIR, exist_ok=True)
    name = f"{APP_NAME}-{VERSION}-linux-{ARCH}"
    stage_root = os.path.join(HERE, "build", "linux-stage")
    stage = os.path.join(stage_root, name)
    shutil.rmtree(stage_root, ignore_errors=True)
    os.makedirs(stage)
    # перенос, а не копия: бандл весит под 2 ГБ
    shutil.move(DIST_DIR, os.path.join(stage, APP_NAME))
    for fn in ("install.sh", "uninstall.sh", "README.txt"):
        src = os.path.join(LINUX_DIR, fn)
        dst = os.path.join(stage, fn)
        # скрипты обязаны быть с LF, даже если репозиторий выгружен с CRLF
        with open(src, "rb") as f:
            data = f.read().replace(b"\r\n", b"\n")
        with open(dst, "wb") as f:
            f.write(data)
        if fn.endswith(".sh"):
            os.chmod(dst, 0o755)
    shutil.copy2(os.path.join(HERE, "icon.png"), os.path.join(stage, "icon.png"))

    out = os.path.join(OUT_DIR, name + ".tar.xz")
    if os.path.exists(out):
        os.remove(out)
    env = dict(os.environ, XZ_OPT="-T0 -6")
    run(["tar", "-C", stage_root, "-cJf", out, name], env=env)
    # возвращаем бандл на место — dist/ остаётся пригодным для проверки
    shutil.move(os.path.join(stage, APP_NAME), DIST_DIR)
    shutil.rmtree(stage_root, ignore_errors=True)
    log("ГОТОВО. Архив: " + out)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Сборка UNI IDE")
    ap.add_argument("--no-installer", action="store_true",
                    help="собрать только бандл в dist/, без установщика/образа/архива")
    ap.add_argument("--no-pip", action="store_true",
                    help="не ставить/обновлять python-зависимости (уже установлены)")
    ap.add_argument("--payload", action="store_true",
                    help="собрать payload-архив автообновления и на macOS/Linux")
    args = ap.parse_args()

    if not (IS_WIN or IS_MAC or IS_LINUX):
        log("Неподдерживаемая система: " + sys.platform)
        sys.exit(1)

    log(f"UNI IDE {VERSION} — сборка под {sys.platform} ({ARCH})")
    keep_system_awake()
    ensure_toolchain()
    pyinstaller_build(install_deps=not args.no_pip)
    if IS_LINUX:
        prune_linux_graphics_stack()
        make_linux_launcher()
    log("Копирую ресурсы и тулчейн в бандл...")
    root = copy_resources()
    if IS_MAC:
        finish_mac_app()
    log("Бандл готов: " + (MAC_APP if IS_MAC else DIST_DIR) + "  (заводская часть: " + root + ")")
    if IS_WIN or args.payload:
        build_payload()

    if args.no_installer:
        log("Установщик пропущен (--no-installer).")
        return
    if IS_WIN:
        build_installer_windows()
    elif IS_MAC:
        build_installer_mac()
    else:
        build_installer_linux()


if __name__ == "__main__":
    main()
