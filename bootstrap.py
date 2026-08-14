#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap.py — тонкий загрузчик UNI IDE (вход PyInstaller-сборки).

Сам НЕ содержит логики IDE: применяет отложенное обновление (updates/pending),
затем запускает server.py, лежащий рядом с exe, как обычный файл. Благодаря
этому обновления приложения — это замена нескольких файлов (~0.5 МБ payload
из GitHub Releases), а не переустановка 380-МБ бандла с тулчейном.

Схема каталога рядом с exe:
    UNI-IDE.exe          <- этот загрузчик (замороженный; меняется только
                            полным установщиком)
    server.py            <- логика IDE (payload, обновляется автоапдейтом)
    index.html, vendor/  <- интерфейс (payload)
    base-version.txt     <- версия полной установки (пишет установщик)
    updates/
      pending/           <- распакованный payload следующей версии
                            (готовит фоновый поток server.py)
      backup/            <- заменённые файлы последнего применения (для отката)
      applying.marker    <- флаг «применение не завершено» (крэш посреди)

Правила надёжности:
  * применение атомарно настолько, насколько возможно: сначала все текущие
    файлы уходят в backup, затем pending переезжает на их место;
  * если при прошлом запуске применение оборвалось (остался applying.marker) —
    восстанавливаем backup и удаляем повреждённый pending;
  * если новый server.py падает на старте — откатываемся на backup и
    запускаем прежнюю версию.

Изменения в этом файле требуют выпуска ПОЛНОГО установщика.
"""

import os
import sys
import shutil
import runpy
import traceback

# --------------------------------------------------------------------------- #
# Предзагрузка модулей для payload.
# PyInstaller включает в бандл только то, что импортируется отсюда (server.py
# анализу не подвергается — он загружается динамически). Любой новый import в
# server.py должен быть либо в этом списке, либо появиться вместе с полным
# установщиком.
# --------------------------------------------------------------------------- #
import re                      # noqa: F401
import json                    # noqa: F401
import time                    # noqa: F401
import glob                    # noqa: F401
import ssl                     # noqa: F401
import socket                  # noqa: F401
import ctypes                  # noqa: F401
import atexit                  # noqa: F401
import string                  # noqa: F401
import struct                  # noqa: F401
import base64                  # noqa: F401
import hashlib                 # noqa: F401
import zipfile                 # noqa: F401
import tempfile                # noqa: F401
import threading               # noqa: F401
import subprocess              # noqa: F401
import webbrowser              # noqa: F401
import urllib.parse            # noqa: F401
import urllib.request          # noqa: F401
import urllib.error            # noqa: F401
import http.client             # noqa: F401
import email.utils             # noqa: F401
import datetime                # noqa: F401
import logging                 # noqa: F401
import queue                   # noqa: F401
import uuid                    # noqa: F401
import random                  # noqa: F401
import platform                # noqa: F401
import getpass                 # noqa: F401

try:
    import flask               # noqa: F401
    import werkzeug            # noqa: F401
    import jinja2              # noqa: F401
    import click               # noqa: F401
    import itsdangerous        # noqa: F401
    import markupsafe          # noqa: F401
    import blinker             # noqa: F401
except Exception:
    pass
try:
    import serial                       # noqa: F401
    import serial.tools.list_ports      # noqa: F401
except Exception:
    pass
try:
    import webview             # noqa: F401
except Exception:
    pass


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Файлы payload, которыми управляет автообновление (совпадает со сборкой
# payload-zip в build.py). update.json — служебный, поверх BASE не копируется.
PAYLOAD_META = "update.json"


def _updates_dir(base):
    return os.path.join(base, "updates")


def _log(base, msg):
    """Журнал загрузчика — в файл, окна консоли у exe нет."""
    try:
        with open(os.path.join(base, "updates", "bootstrap-log.txt"), "a",
                  encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def restore_backup(base):
    """Возвращает файлы из updates/backup на место. True, если что-то вернули."""
    upd = _updates_dir(base)
    backup = os.path.join(upd, "backup")
    if not os.path.isdir(backup):
        return False
    restored = False
    for name in os.listdir(backup):
        dst = os.path.join(base, name)
        src = os.path.join(backup, name)
        try:
            if os.path.isdir(dst):
                shutil.rmtree(dst, ignore_errors=True)
            elif os.path.exists(dst):
                os.remove(dst)
            shutil.move(src, dst)
            restored = True
        except Exception:
            pass
    shutil.rmtree(backup, ignore_errors=True)
    return restored


def apply_pending(base):
    """Применяет updates/pending, если он есть.
    Возвращает: None — нечего применять; "applied" — применено;
    "rolled_back" — прошлое применение оборвалось, откатились."""
    upd = _updates_dir(base)
    pending = os.path.join(upd, "pending")
    marker = os.path.join(upd, "applying.marker")

    if os.path.exists(marker):
        # крэш посреди прошлого применения: файлы могли переехать частично
        _log(base, "найден applying.marker — откат на backup")
        restore_backup(base)
        shutil.rmtree(pending, ignore_errors=True)
        try:
            os.remove(marker)
        except OSError:
            pass
        return "rolled_back"

    if not os.path.isdir(pending):
        return None
    names = [n for n in os.listdir(pending) if n != PAYLOAD_META]
    if not names:
        shutil.rmtree(pending, ignore_errors=True)
        return None

    try:
        os.makedirs(upd, exist_ok=True)
        with open(marker, "w") as f:
            f.write("applying")
    except Exception:
        return None            # каталог не записываем — тихо пропускаем

    backup = os.path.join(upd, "backup")
    shutil.rmtree(backup, ignore_errors=True)
    os.makedirs(backup, exist_ok=True)

    try:
        # 1) текущие версии файлов — в backup
        for n in names:
            cur = os.path.join(base, n)
            if os.path.exists(cur):
                shutil.move(cur, os.path.join(backup, n))
        # 2) pending — на их место
        for n in names:
            shutil.move(os.path.join(pending, n), os.path.join(base, n))
        shutil.rmtree(pending, ignore_errors=True)
        os.remove(marker)
        _log(base, "обновление применено: " + ", ".join(sorted(names)))
        return "applied"
    except Exception:
        _log(base, "сбой применения:\n" + traceback.format_exc())
        restore_backup(base)
        shutil.rmtree(pending, ignore_errors=True)
        try:
            os.remove(marker)
        except OSError:
            pass
        return "rolled_back"


def run_server(base):
    """Запускает server.py как __main__ (обычный сценарий работы IDE)."""
    server = os.path.join(base, "server.py")
    runpy.run_path(server, run_name="__main__")


def main():
    base = base_dir()
    applied = None
    try:
        applied = apply_pending(base)
    except Exception:
        _log(base, "apply_pending упал:\n" + traceback.format_exc())

    try:
        run_server(base)
    except SystemExit:
        raise
    except Exception:
        _log(base, "server.py упал на старте:\n" + traceback.format_exc())
        if applied == "applied" and restore_backup(base):
            # свежий payload оказался нерабочим — вернулись и пробуем ещё раз
            _log(base, "откат после сбоя старта — запускаю прежнюю версию")
            run_server(base)
        else:
            raise


if __name__ == "__main__":
    main()
