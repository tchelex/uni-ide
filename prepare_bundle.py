#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_bundle.py — ШАГ ДЛЯ УЧИТЕЛЯ, ВЫПОЛНЯЕТСЯ ОДИН РАЗ С ИНТЕРНЕТОМ (Windows).

Что делает:
  1) скачивает arduino-cli.exe (если его ещё нет рядом);
  2) скачивает и устанавливает ядро ESP32 в ПЕРЕНОСИМУЮ папку ./arduino-data;
  (опц.) ставит нужные библиотеки в ./arduino-user.

После этого папка arduino-data содержит весь компилятор и больше не требует
интернета. Дальше запускайте `python build.py` — он соберёт UNI-IDE.exe и
установщик UNI-IDE-Setup-x.x.x.exe.
"""

import os
import sys
import time
import zipfile
import urllib.request
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CLI  = os.path.join(HERE, "arduino-cli.exe")
DATA = os.path.join(HERE, "arduino-data")
USER = os.path.join(HERE, "arduino-user")
DL   = os.path.join(HERE, "arduino-downloads")

CLI_URL   = "https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip"
ESP32_URL = "https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json"

# При желании впишите библиотеки, которые нужны на занятиях (ставятся офлайн потом):
LIBS = [
    # "Adafruit NeoPixel",
    # "Servo",
]


def env():
    e = dict(os.environ)
    e["ARDUINO_DIRECTORIES_DATA"] = DATA
    e["ARDUINO_DIRECTORIES_USER"] = USER
    e["ARDUINO_DIRECTORIES_DOWNLOADS"] = DL
    e["ARDUINO_BOARD_MANAGER_ADDITIONAL_URLS"] = ESP32_URL
    return e


def sh(args):
    print(">", "arduino-cli", " ".join(args))
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


def main():
    keep_system_awake()
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(USER, exist_ok=True)

    if not os.path.exists(CLI):
        print("Скачиваю arduino-cli...")
        tmp = os.path.join(HERE, "_cli.zip")
        urllib.request.urlretrieve(CLI_URL, tmp)
        with zipfile.ZipFile(tmp) as z:
            names = [n for n in z.namelist() if n.lower().endswith("arduino-cli.exe")]
            if not names:
                print("Не нашёл arduino-cli.exe в архиве.")
                sys.exit(1)
            with z.open(names[0]) as src, open(CLI, "wb") as dst:
                dst.write(src.read())
        os.remove(tmp)
        print("arduino-cli.exe готов.")

    print("\nОбновляю индекс плат...")
    sh_retry(["core", "update-index"])

    print("\nУстанавливаю ядро ESP32 (это сотни МБ, подождите)...")
    sh_retry(["core", "install", "esp32:esp32"])

    for lib in LIBS:
        print(f"\nСтавлю библиотеку: {lib}")
        sh(["lib", "install", lib])

    print("\n=== ГОТОВО ===")
    print("Папка arduino-data заполнена. Теперь запустите:  python build.py")


if __name__ == "__main__":
    main()
