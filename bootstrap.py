#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap.py — тонкий загрузчик UNI IDE (вход PyInstaller-сборки).

Сам НЕ содержит логики IDE: применяет отложенное обновление (updates/pending),
затем запускает server.py, лежащий рядом с exe, как обычный файл. Благодаря
этому обновления приложения — это замена нескольких файлов (~0.5 МБ payload
из GitHub Releases), а не переустановка 380-МБ бандла с тулчейном.

Схема каталога рядом с exe (Windows):
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

macOS и Linux — «раздельная» раскладка. Само приложение лежит там, куда писать
нельзя или не следует (.app в /Applications — запись внутрь ломает подпись
бандла; запуск из «Загрузок» вообще идёт с диска только для чтения), поэтому:
    <бандл>/uni/                     <- ЗАВОДСКАЯ часть, только чтение
      app/                           <- payload той версии, что в установщике
      arduino-cli, arduino-data/, arduino-user/
      base-version.txt, build-id.txt
    <каталог состояния>/             <- ~/Library/Application Support/UNI-IDE
                                        или ~/.local/share/UNI-IDE
      app/                           <- РАБОЧИЙ payload (копия заводского,
                                        поверх него применяются обновления)
      updates/                       <- pending / backup / applying.marker
      arduino-data/, arduino-user/,  <- готовит server.py
      build-tmp/ ...
Загрузчик сообщает server.py эти каталоги через UNI_APP_DIR / UNI_TOOL_DIR /
UNI_STATE_DIR. Новая полная установка (другой build-id) сбрасывает рабочий
payload на заводской — как Windows-установщик, перезаписывающий файлы.

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
if sys.platform.startswith("linux"):
    # Linux-сборка несёт собственный движок окна (Qt WebEngine), чтобы не
    # зависеть от набора библиотек конкретного дистрибутива.
    try:
        import qtpy                                    # noqa: F401
        from PyQt6 import QtCore, QtGui, QtWidgets     # noqa: F401
        from PyQt6 import QtNetwork, QtWebChannel      # noqa: F401
        from PyQt6 import QtWebEngineCore              # noqa: F401
        from PyQt6 import QtWebEngineWidgets           # noqa: F401
    except Exception:
        pass


def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# Файлы payload, которыми управляет автообновление (совпадает со сборкой
# payload-zip в build.py). update.json — служебный, поверх BASE не копируется.
PAYLOAD_META = "update.json"

APP_ID = "UNI-IDE"       # имя каталога состояния на macOS/Linux


def factory_dir(base):
    """Заводская часть бандла на macOS/Linux (см. схему в шапке). None, если
    её нет — тогда работаем по-старому, одной папкой (Windows, dev-режим)."""
    if sys.platform == "win32":
        return None
    cands = [os.path.join(base, "uni")]
    if sys.platform == "darwin":
        # UNI-IDE.app/Contents/MacOS/UNI-IDE -> UNI-IDE.app/Contents/Resources/uni
        cands.insert(0, os.path.join(os.path.dirname(base), "Resources", "uni"))
    for c in cands:
        if os.path.isfile(os.path.join(c, "app", "server.py")):
            return os.path.realpath(c)
    return None


def state_dir():
    """Каталог изменяемых данных пользователя на macOS/Linux."""
    forced = os.environ.get("UNI_STATE_DIR")
    if forced:
        return forced
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", APP_ID)
    xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(xdg, APP_ID)


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def factory_stamp(factory):
    """Отпечаток полной установки: build-id.txt (пишет build.py), иначе версия."""
    return (_read_text(os.path.join(factory, "build-id.txt"))
            or _read_text(os.path.join(factory, "base-version.txt")) or "unknown")


def seed_app(factory, state, force=False):
    """Готовит рабочий payload <state>/app из заводского <factory>/app.
    Копирует при первом запуске и после новой полной установки (сменился
    build-id) — тогда же сбрасывает скачанные обновления: полный установщик
    всегда свежее отложенного payload. Возвращает True, если скопировал."""
    app = os.path.join(state, "app")
    stamp_file = os.path.join(state, "app-seeded.txt")
    stamp = factory_stamp(factory)
    have = os.path.isfile(os.path.join(app, "server.py"))
    if have and not force and _read_text(stamp_file) == stamp:
        return False
    os.makedirs(state, exist_ok=True)
    tmp = os.path.join(state, "app.new")
    old = os.path.join(state, "app.old")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    shutil.copytree(os.path.join(factory, "app"), tmp)
    if os.path.isdir(app):
        os.replace(app, old)
    os.replace(tmp, app)
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(os.path.join(state, "updates"), ignore_errors=True)
    with open(stamp_file, "w", encoding="utf-8") as f:
        f.write(stamp)
    return True


def _updates_dir(base, upd=None):
    return upd or os.path.join(base, "updates")


def _log(base, msg, upd=None):
    """Журнал загрузчика — в файл, окна консоли у приложения нет."""
    try:
        d = _updates_dir(base, upd)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "bootstrap-log.txt"), "a",
                  encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + msg + "\n")
    except Exception:
        pass


def restore_backup(base, upd=None):
    """Возвращает файлы из updates/backup на место. True, если что-то вернули.
    base — папка payload; upd — папка updates (по умолчанию base/updates)."""
    upd = _updates_dir(base, upd)
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


def apply_pending(base, upd=None):
    """Применяет updates/pending, если он есть.
    base — папка payload; upd — папка updates (по умолчанию base/updates).
    Возвращает: None — нечего применять; "applied" — применено;
    "rolled_back" — прошлое применение оборвалось, откатились."""
    upd = _updates_dir(base, upd)
    pending = os.path.join(upd, "pending")
    marker = os.path.join(upd, "applying.marker")

    if os.path.exists(marker):
        # крэш посреди прошлого применения: файлы могли переехать частично
        _log(base, "найден applying.marker — откат на backup", upd)
        restore_backup(base, upd)
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
        _log(base, "обновление применено: " + ", ".join(sorted(names)), upd)
        return "applied"
    except Exception:
        _log(base, "сбой применения:\n" + traceback.format_exc(), upd)
        restore_backup(base, upd)
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


def resolve_layout(base):
    """Возвращает (app, upd, factory, state).
    Windows/dev: всё в одной папке — (base, None, None, None).
    macOS/Linux-бандл: рабочий payload и обновления — в каталоге состояния."""
    factory = factory_dir(base)
    if not factory:
        return base, None, None, None
    state = state_dir()
    try:
        seed_app(factory, state)
    except Exception:
        # каталог состояния недоступен — работаем прямо с заводского payload
        # (без автообновления), лишь бы IDE запустилась
        return os.path.join(factory, "app"), None, factory, None
    app = os.path.join(state, "app")
    os.environ["UNI_APP_DIR"] = app
    os.environ["UNI_TOOL_DIR"] = factory
    os.environ["UNI_STATE_DIR"] = state
    return app, os.path.join(state, "updates"), factory, state


def platform_env():
    """Переменные окружения, нужные движку окна на Linux."""
    if not sys.platform.startswith("linux"):
        return
    os.environ.setdefault("PYWEBVIEW_GUI", "qt")
    os.environ.setdefault("QT_API", "pyqt6")
    try:
        if os.geteuid() == 0:
            # песочница Chromium не работает от root
            os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    except Exception:
        pass


def main():
    base = base_dir()
    platform_env()
    app, upd, factory, state = resolve_layout(base)
    if factory and not state:
        # аварийный режим: заводской payload, каталоги только для чтения
        os.environ["UNI_APP_DIR"] = app
        os.environ["UNI_TOOL_DIR"] = factory
        import tempfile as _tf
        os.environ["UNI_STATE_DIR"] = os.path.join(_tf.gettempdir(), APP_ID)
        run_server(app)
        return

    applied = None
    try:
        applied = apply_pending(app, upd)
    except Exception:
        _log(app, "apply_pending упал:\n" + traceback.format_exc(), upd)

    try:
        run_server(app)
    except SystemExit:
        raise
    except Exception:
        _log(app, "server.py упал на старте:\n" + traceback.format_exc(), upd)
        if applied == "applied" and restore_backup(app, upd):
            # свежий payload оказался нерабочим — вернулись и пробуем ещё раз
            _log(app, "откат после сбоя старта — запускаю прежнюю версию", upd)
            run_server(app)
        elif factory and state:
            # рабочий payload испорчен, отката нет — возвращаем заводской
            _log(app, "сброс рабочего payload на заводской", upd)
            seed_app(factory, state, force=True)
            run_server(app)
        else:
            raise


if __name__ == "__main__":
    main()
