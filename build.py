#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build.py — сборка UNI IDE в исполняемое приложение и установщик.

Кроссплатформенный оркестратор сборки (заменяет старый build_exe.bat).
Сейчас полностью поддержана сборка под Windows; для macOS оставлены заготовки.

ЧТО ДЕЛАЕТ (Windows):
  1) PyInstaller (--onedir, --noconsole) → dist/UNI-IDE/UNI-IDE.exe
  2) копирует index.html, vendor, иконки и ПЕРЕНОСИМЫЙ тулчейн
     (arduino-cli.exe + arduino-data/arduino-user) рядом с exe
  3) Inno Setup (ISCC.exe) → installer-out/UNI-IDE-Setup-<версия>.exe

ПЕРЕД ПЕРВОЙ СБОРКОЙ один раз выполните (нужен интернет):
     python prepare_bundle.py
        — скачивает arduino-cli и ядро ESP32 в ./arduino-data

ЗАТЕМ:
     python build.py                 # exe-бандл + установщик
     python build.py --no-installer  # только exe-бандл (без Inno Setup)

ТРЕБОВАНИЯ:
  - Python 3.9+
  - Inno Setup 6 (для шага 3). Установить: https://jrsoftware.org/isdl.php
    или в терминале:  winget install JRSoftware.InnoSetup
"""

import os
import re
import sys
import json
import shutil
import hashlib
import zipfile
import argparse
import subprocess

# --------------------------------------------------------------------------- #
# Параметры сборки
# --------------------------------------------------------------------------- #
APP_NAME = "UNI-IDE"        # имя exe и папки бандла (без пробелов)

HERE     = os.path.dirname(os.path.abspath(__file__))


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

DIST_DIR = os.path.join(HERE, "dist", APP_NAME)          # выход PyInstaller (--onedir)
OUT_DIR  = os.path.join(HERE, "installer-out")           # куда кладём setup.exe
ISS_FILE = os.path.join(HERE, "installer", "UNI-IDE.iss")

# Ресурсы, которые должны лежать РЯДОМ с exe. server.py — тоже обычный файл:
# exe (bootstrap.py) запускает его с диска, что и делает возможными лёгкие
# payload-обновления без переустановки.
RES_FILES = ["server.py", "index.html", "icon.ico", "icon.png", "student-README.txt"]
RES_DIRS  = ["vendor"]

# Состав payload-обновления (см. bootstrap.py и автоапдейт в server.py).
PAYLOAD_FILES = ["server.py", "index.html", "icon.ico", "icon.png", "student-README.txt"]
PAYLOAD_DIRS  = ["vendor"]

# Переносимый тулчейн (создаётся prepare_bundle.py).
TOOLCHAIN_FILES = ["arduino-cli.exe"]
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
    cli_name = "arduino-cli.exe" if os.name == "nt" else "arduino-cli"
    cli  = os.path.join(HERE, cli_name)
    data = os.path.join(HERE, "arduino-data")
    if not (os.path.exists(cli) and os.path.isdir(data)):
        log("ОШИБКА: не найден переносимый тулчейн (arduino-cli + arduino-data).")
        log("Сначала выполните (нужен интернет):  python prepare_bundle.py")
        sys.exit(1)


def pyinstaller_build():
    """Собирает onedir-бандл PyInstaller."""
    run([sys.executable, "-m", "pip", "install", "--upgrade",
         "pyinstaller", "flask", "pyserial", "pywebview"])

    # чистим прошлый dist, чтобы не тащить устаревшие файлы
    dist_root = os.path.join(HERE, "dist")
    if os.path.isdir(dist_root):
        shutil.rmtree(dist_root, ignore_errors=True)

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
           "--onedir", "--noconsole", "--name", APP_NAME,
           # pywebview подтягивает свой GUI-бэкенд динамически — забираем целиком
           "--collect-all", "webview",
           "--collect-submodules", "serial",
           # flask и его свита: bootstrap.py импортирует их явно, но подстрахуемся
           "--hidden-import", "flask",
           "--hidden-import", "werkzeug",
           "--hidden-import", "jinja2"]
    icon = os.path.join(HERE, "icon.ico")
    if os.path.exists(icon):
        cmd += ["--icon", icon]
    # Вход — тонкий загрузчик; server.py остаётся обычным файлом рядом с exe
    cmd += [os.path.join(HERE, "bootstrap.py")]
    run(cmd)

    if not os.path.isdir(DIST_DIR):
        log("ОШИБКА: PyInstaller не создал " + DIST_DIR)
        sys.exit(1)


def copy_resources(dst):
    """Кладёт ресурсы и тулчейн рядом с exe внутри бандла."""
    for f in RES_FILES + TOOLCHAIN_FILES:
        src = os.path.join(HERE, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, os.path.basename(f)))
        else:
            log("  (пропуск, нет файла) " + f)
    for d in RES_DIRS + TOOLCHAIN_DIRS:
        src = os.path.join(HERE, d)
        if os.path.isdir(src):
            dest = os.path.join(dst, os.path.basename(d))
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(src, dest, ignore=COPY_IGNORE)
        else:
            log("  (пропуск, нет папки) " + d)
    # версия полной установки — по ней автоапдейт решает, применим ли payload
    with open(os.path.join(dst, "base-version.txt"), "w", encoding="utf-8") as f:
        f.write(VERSION)


def build_payload():
    """Собирает лёгкий payload-архив для автообновления установленных копий:
    installer-out/payload-<версия>.zip (+ .sha256). Внутри — только файлы
    приложения (server.py, index.html, vendor…) и update.json с версией и
    минимальной совместимой полной установкой (PAYLOAD_MIN_BASE)."""
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
                    z.write(p, os.path.relpath(p, HERE))
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


def build_installer():
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
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Сборка UNI IDE")
    ap.add_argument("--no-installer", action="store_true",
                    help="собрать только exe-бандл, без Inno Setup")
    args = ap.parse_args()

    if sys.platform != "win32":
        log("Пока поддержана только сборка под Windows.")
        log("Сборку под macOS (.app/.dmg) добавим отдельно — для неё нужен Mac.")
        sys.exit(1)

    keep_system_awake()
    ensure_toolchain()
    pyinstaller_build()
    log("Копирую ресурсы и тулчейн в бандл...")
    copy_resources(DIST_DIR)
    log("Бандл готов: " + DIST_DIR)
    build_payload()

    if args.no_installer:
        log("Установщик пропущен (--no-installer).")
        return
    build_installer()


if __name__ == "__main__":
    main()
