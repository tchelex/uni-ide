#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_bundle.py — ШАГ ДЛЯ УЧИТЕЛЯ/СБОРЩИКА, ВЫПОЛНЯЕТСЯ ОДИН РАЗ С ИНТЕРНЕТОМ.
Работает на Windows, macOS и Linux (тулчейн скачивается под ту систему, на
которой запущен скрипт — собрать бандл «для другой ОС» нельзя).

Что делает:
  1) скачивает arduino-cli (если его ещё нет рядом);
  2) скачивает и устанавливает ядро ESP32 в ПЕРЕНОСИМУЮ папку ./arduino-data;
  3) с флагом --libs ставит в ./arduino-user библиотеки, которые входят в
     комплект UNI IDE (UNI, Adafruit…), — так бандл собирается «с нуля»
     на чистой машине (например, в GitHub Actions).

После этого папка arduino-data содержит весь компилятор и больше не требует
интернета. Дальше запускайте `python build.py`.

Версии можно закрепить переменными окружения (иначе берутся последние):
    UNI_CLI_VERSION=1.5.1      версия arduino-cli
    UNI_ESP32_CORE=3.3.10      версия ядра esp32:esp32
    UNI_LIB_REF=v1.2.1         тег/ветка библиотеки UNI (для --libs)
"""

import os
import sys
import stat
import time
import shutil
import tarfile
import zipfile
import argparse
import platform
import urllib.request
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"
CLI_NAME = "arduino-cli.exe" if IS_WIN else "arduino-cli"
CLI  = os.path.join(HERE, CLI_NAME)
DATA = os.path.join(HERE, "arduino-data")
USER = os.path.join(HERE, "arduino-user")
DL   = os.path.join(HERE, "arduino-downloads")

ESP32_URL = "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"

CLI_VERSION = os.environ.get("UNI_CLI_VERSION", "").strip() or "latest"
ESP32_CORE  = os.environ.get("UNI_ESP32_CORE", "").strip()
UNI_LIB_URL = "https://github.com/uni-robotics/UNI.git"
UNI_LIB_REF = os.environ.get("UNI_LIB_REF", "").strip()

# При желании впишите библиотеки, которые нужны на занятиях (ставятся всегда):
LIBS = [
    # "Servo",
]

# Комплект UNI IDE (ставится с флагом --libs). Зависимости (Adafruit BusIO)
# arduino-cli подтянет сам. Библиотека UNI берётся из её git-репозитория.
BUNDLE_LIBS = [
    "Adafruit GFX Library",
    "Adafruit SSD1306",
    "Adafruit NeoPixel",
    "BluetoothSerial",
]


def cli_asset():
    """Имя архива arduino-cli для текущей ОС и архитектуры."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if IS_WIN:
        return "Windows_64bit.zip"
    if sys.platform == "darwin":
        return "macOS_ARM64.tar.gz" if arm else "macOS_64bit.tar.gz"
    return "Linux_ARM64.tar.gz" if arm else "Linux_64bit.tar.gz"


def cli_url():
    return ("https://downloads.arduino.cc/arduino-cli/arduino-cli_"
            + CLI_VERSION + "_" + cli_asset())


def env():
    e = dict(os.environ)
    e["ARDUINO_DIRECTORIES_DATA"] = DATA
    e["ARDUINO_DIRECTORIES_USER"] = USER
    e["ARDUINO_DIRECTORIES_DOWNLOADS"] = DL
    e["ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS"] = ESP32_URL
    # установка библиотеки UNI по git-ссылке
    e["ARDUINO_LIBRARY_ENABLE_UNSAFE_INSTALL"] = "true"
    return e


def sh(args):
    print(">", "arduino-cli", " ".join(args), flush=True)
    subprocess.run([CLI] + args, env=env(), check=True)


def sh_retry(args, tries=6, delay=10):
    """Как sh(), но с повтором при сбое. Уже скачанные архивы кэшируются,
    поэтому каждая попытка докачивает только недостающее — устойчиво к обрывам сети."""
    for i in range(1, tries + 1):
        try:
            sh(args)
            return
        except subprocess.CalledProcessError as e:
            if i == tries:
                raise
            print(f"[повтор {i}/{tries}] не удалось ({e}). Жду {delay}с и пробую снова "
                  f"(докачаю из кэша)...", flush=True)
            time.sleep(delay)


def keep_system_awake():
    """Не даём Windows уснуть во время долгой загрузки тулчейна (сотни МБ).
    Флаг держится, пока жив этот процесс, и сам сбрасывается при выходе."""
    if sys.platform == "win32":
        try:
            import ctypes
            ES_CONTINUOUS      = 0x80000000
            ES_SYSTEM_REQUIRED = 0x00000001
            ctypes.windll.kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        except Exception:
            pass


def download_cli():
    url = cli_url()
    print("Скачиваю arduino-cli: " + url, flush=True)
    tmp = os.path.join(HERE, "_cli.download")
    urllib.request.urlretrieve(url, tmp)
    try:
        if url.endswith(".zip"):
            with zipfile.ZipFile(tmp) as z:
                names = [n for n in z.namelist()
                         if os.path.basename(n).lower() == CLI_NAME]
                if not names:
                    print("Не нашёл " + CLI_NAME + " в архиве.")
                    sys.exit(1)
                with z.open(names[0]) as src, open(CLI, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        else:
            with tarfile.open(tmp) as t:
                members = [m for m in t.getmembers()
                           if m.isfile() and os.path.basename(m.name) == CLI_NAME]
                if not members:
                    print("Не нашёл " + CLI_NAME + " в архиве.")
                    sys.exit(1)
                with t.extractfile(members[0]) as src, open(CLI, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    finally:
        os.remove(tmp)
    if not IS_WIN:
        os.chmod(CLI, os.stat(CLI).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(CLI_NAME + " готов.", flush=True)


def _rm_readonly(func, path, _exc):
    """Объекты git помечены «только чтение» — на Windows их иначе не удалить."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def install_bundle_libs():
    for lib in BUNDLE_LIBS:
        print(f"\nСтавлю библиотеку: {lib}", flush=True)
        sh_retry(["lib", "install", lib], tries=3)
    url = UNI_LIB_URL + ("#" + UNI_LIB_REF if UNI_LIB_REF else "")
    print(f"\nСтавлю библиотеку UNI: {url}", flush=True)
    uni_dir = os.path.join(USER, "libraries", "UNI")
    if os.path.isdir(uni_dir):
        shutil.rmtree(uni_dir, onerror=_rm_readonly)
    sh_retry(["lib", "install", "--git-url", url], tries=3)
    # история git в бандле не нужна
    git_dir = os.path.join(uni_dir, ".git")
    if os.path.isdir(git_dir):
        shutil.rmtree(git_dir, onerror=_rm_readonly)


# То, что build.py всё равно не кладёт в бандл (см. COPY_IGNORE там): IDE
# нацелена только на классический ESP32 (Xtensa). Список должен совпадать.
PRUNE_NAMES = {
    "espressif__esp_matter",
    "esp-rv32", "riscv32-esp-elf-gdb",
    "esp32c3-libs", "esp32c5-libs", "esp32c6-libs", "esp32h2-libs",
    "esp32p4-libs", "esp32p4_es-libs", "esp32s2-libs", "esp32s3-libs",
}


def prune():
    """Удаляет из arduino-data части, не нужные ESP32 Dev Module (~4 ГБ).
    Для локальной сборки не требуется (build.py их просто не копирует), но
    сильно уменьшает кэш тулчейна в GitHub Actions."""
    removed = 0
    for cur, dirs, _files in os.walk(DATA):
        for d in list(dirs):
            if d in PRUNE_NAMES:
                shutil.rmtree(os.path.join(cur, d), onerror=_rm_readonly)
                dirs.remove(d)
                removed += 1
    print(f"Удалено лишних каталогов тулчейна: {removed}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Подготовка офлайн-тулчейна UNI IDE")
    ap.add_argument("--libs", action="store_true",
                    help="поставить библиотеки из комплекта UNI IDE (UNI, Adafruit…)")
    ap.add_argument("--prune", action="store_true",
                    help="удалить из arduino-data то, что не идёт в бандл (экономит ~4 ГБ)")
    args = ap.parse_args()

    keep_system_awake()
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(USER, exist_ok=True)

    if not os.path.exists(CLI):
        download_cli()

    print("\nОбновляю индекс плат...", flush=True)
    sh_retry(["core", "update-index"])

    print("\nОбновляю каталог библиотек (для офлайн-поиска)...", flush=True)
    # без этого `lib search` на машине без интернета вернёт пустой список
    sh_retry(["lib", "update-index"])

    core = "esp32:esp32" + ("@" + ESP32_CORE if ESP32_CORE else "")
    print(f"\nУстанавливаю ядро ESP32 ({core}) — это сотни МБ, подождите...", flush=True)
    sh_retry(["core", "install", core])

    for lib in LIBS:
        print(f"\nСтавлю библиотеку: {lib}", flush=True)
        sh(["lib", "install", lib])

    if args.libs:
        install_bundle_libs()

    if args.prune:
        prune()

    print("\n=== ГОТОВО ===")
    print("Папка arduino-data заполнена. Теперь запустите:  python build.py")


if __name__ == "__main__":
    main()
