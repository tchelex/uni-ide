#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UNI IDE — простая локальная IDE для ESP32 Dev Module.

Работает в двух режимах:
  1) как обычный python-скрипт (для разработки) — использует системный arduino-cli;
  2) как собранный .exe (PyInstaller) с ПЕРЕНОСИМЫМ тулчейном — всё офлайн,
     рядом с exe лежит arduino-cli.exe и папка arduino-data с ядром ESP32.

Загрузка зашита ТОЛЬКО на плату ESP32 Dev Module (FQBN esp32:esp32:esp32).
"""

import os
import re
import sys
import json
import time
import shutil
import hashlib
import threading
import webbrowser
import subprocess
from urllib.parse import quote

from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context

try:
    import serial
    from serial.tools import list_ports
    HAS_PYSERIAL = True
except Exception:
    HAS_PYSERIAL = False

# --------------------------------------------------------------------------- #
# Расположение файлов: работает и для скрипта, и для собранного .exe
# --------------------------------------------------------------------------- #
def base_dir():
    if getattr(sys, "frozen", False):          # запущено как .exe
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

BASE = base_dir()
IS_FROZEN = getattr(sys, "frozen", False)

# Переносимый тулчейн (присутствует только в подготовленном бандле)
PORTABLE_DATA = os.path.join(BASE, "arduino-data")
PORTABLE_USER = os.path.join(BASE, "arduino-user")
PORTABLE_DL   = os.path.join(BASE, "arduino-downloads")
IS_PORTABLE   = os.path.isdir(PORTABLE_DATA)

# Иконка окна/приложения (рядом со скриптом или exe)
ICON_PATH = os.path.join(BASE, "icon.ico")

# Плата зафиксирована: ESP32 Dev Module
FQBN = "esp32:esp32:esp32"

# Постоянный кэш сборки — ядро и библиотеки компилируются один раз
BUILD_CACHE = os.path.join(BASE, "build-cache")
BUILD_PATH  = os.path.join(BASE, "build-tmp")


def resolve_cli():
    """Бандл: arduino-cli.exe рядом. Иначе — системный из PATH."""
    for name in ("arduino-cli.exe", "arduino-cli"):
        p = os.path.join(BASE, name)
        if os.path.exists(p):
            return p
    return os.environ.get("ARDUINO_CLI", "arduino-cli")

ARDUINO_CLI = resolve_cli()


def cli_env():
    """Окружение для arduino-cli. В бандле указываем переносимые каталоги."""
    env = dict(os.environ)
    if IS_PORTABLE:
        env["ARDUINO_DIRECTORIES_DATA"] = PORTABLE_DATA
        env["ARDUINO_DIRECTORIES_USER"] = PORTABLE_USER
        env["ARDUINO_DIRECTORIES_DOWNLOADS"] = PORTABLE_DL
    return env


def writable_workspace():
    """Папка для скетча. Рядом с exe, если можно писать; иначе — в LOCALAPPDATA."""
    try:
        t = os.path.join(BASE, ".w_test")
        with open(t, "w") as f:
            f.write("x")
        os.remove(t)
        return BASE
    except Exception:
        d = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "UNI-IDE")
        os.makedirs(d, exist_ok=True)
        return d

WORKSPACE      = writable_workspace()
# Папка по умолчанию для новых проектов. Каждый проект — отдельная папка
# <имя>/<имя>.ino (как в Arduino). Но проект можно открыть/сохранить ГДЕ УГОДНО
# на диске — идентичность проекта определяется абсолютным путём к .ino.
SKETCHBOOK        = os.path.join(WORKSPACE, "Uni_Sketches")
NEW_SKETCH_PREFIX = "uni_sketch"
DEFAULT_SKETCH    = NEW_SKETCH_PREFIX + "1"
RECENT_FILE       = os.path.join(WORKSPACE, "uni-ide-recent.json")
RECENT_MAX        = 10

# Какие библиотеки подмешивать в автодополнение (по имени из arduino-cli).
AC_SETTINGS        = os.path.join(WORKSPACE, "uni-ide-autocomplete.json")
AC_DEFAULT_ENABLED = ["UNI"]


def ac_load_enabled():
    """Список библиотек, методы которых добавляются в словарь подсказок."""
    try:
        with open(AC_SETTINGS, "r", encoding="utf-8") as f:
            en = (json.load(f) or {}).get("enabled")
        if isinstance(en, list):
            return [str(x) for x in en]
    except Exception:
        pass
    return list(AC_DEFAULT_ENABLED)


def ac_save_enabled(names):
    try:
        seen = list(dict.fromkeys(n for n in names if n))
        with open(AC_SETTINGS, "w", encoding="utf-8") as f:
            json.dump({"enabled": seen}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

DEFAULT_CODE = """#include <UNI.h>

UniBase robot;
UniDev module;

void setup() {
  robot.begin("UNI");
  // Ваш код, выполняется один раз.

}

void loop() {
  // Ваш код, выполняется в бесконечном цикле

}
"""

app = Flask(__name__, static_folder=None)

# --------------------------------------------------------------------------- #
# Хелперы
# --------------------------------------------------------------------------- #
def safe_sketch_name(name):
    """Очищает имя скетча: запрещает разделители пути и спецсимволы.
    Возвращает None, если имя недопустимо."""
    name = (name or "").strip()
    if not name or name in (".", ".."):
        return None
    if any(c in name for c in '/\\:*?"<>|'):
        return None
    return name[:64]


def _ino_for_dir(d):
    """Путь к основному .ino внутри папки скетча: предпочитаем <имя_папки>.ino,
    иначе — первый попавшийся .ino-файл."""
    base = os.path.basename(os.path.normpath(d))
    primary = os.path.join(d, base + ".ino")
    if os.path.exists(primary):
        return primary
    try:
        for entry in sorted(os.listdir(d), key=str.lower):
            if entry.lower().endswith(".ino"):
                return os.path.join(d, entry)
    except Exception:
        pass
    return primary


def resolve_sketch_path(path):
    """Приводит произвольный путь к абсолютному пути .ino-файла.
    Принимает путь к .ino или к папке скетча. None при пустом/неверном."""
    if not path:
        return None
    try:
        p = os.path.abspath(path)
    except Exception:
        return None
    if os.path.isdir(p):
        return _ino_for_dir(p)
    if p.lower().endswith(".ino"):
        return p
    # путь без расширения — трактуем как папку скетча
    return _ino_for_dir(p)


def sketch_name_of(ino):
    """Имя проекта = имя .ino без расширения."""
    return os.path.splitext(os.path.basename(ino or ""))[0]


def build_path_for_dir(sketch_dir):
    """Отдельный build-path на каждый проект (по абсолютному пути папки).
    Имя = имя_папки + хеш полного пути, чтобы у одноимённых проектов из разных
    мест не пересекались каталоги сборки. Кэш ядра ESP32 при этом общий."""
    sketch_dir = os.path.abspath(sketch_dir)
    base = os.path.basename(os.path.normpath(sketch_dir)) or "sketch"
    h = hashlib.md5(os.path.normcase(sketch_dir).encode("utf-8")).hexdigest()[:10]
    p = os.path.join(BUILD_PATH, base + "-" + h)
    os.makedirs(p, exist_ok=True)
    return p


def next_sketch_path():
    """Следующее свободное стандартное имя uni_sketchN в SKETCHBOOK."""
    os.makedirs(SKETCHBOOK, exist_ok=True)
    n = 1
    while True:
        name = NEW_SKETCH_PREFIX + str(n)
        d = os.path.join(SKETCHBOOK, name)
        f = os.path.join(d, name + ".ino")
        if not os.path.exists(d) and not os.path.exists(f):
            return name, d, f
        n += 1


def load_recent():
    """Список абсолютных путей последних проектов (новые — первыми)."""
    try:
        with open(RECENT_FILE, "r", encoding="utf-8") as f:
            arr = json.load(f)
        if isinstance(arr, list):
            return [str(x) for x in arr if x]
    except Exception:
        pass
    return []


def save_recent(arr):
    """Сохраняет список, убирая дубликаты (без учёта регистра) и обрезая до RECENT_MAX."""
    seen, keys = [], set()
    for p in arr:
        if not p:
            continue
        key = os.path.normcase(os.path.abspath(p))
        if key in keys:
            continue
        keys.add(key)
        seen.append(os.path.abspath(p))
        if len(seen) >= RECENT_MAX:
            break
    try:
        with open(RECENT_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return seen


def add_recent(ino):
    if not ino:
        return
    arr = load_recent()
    arr.insert(0, os.path.abspath(ino))
    save_recent(arr)


def ensure_default_sketch():
    """Гарантирует наличие открываемого проекта при старте.
    Берёт первый существующий из «последних», иначе создаёт uni_sketch1."""
    for p in load_recent():
        if os.path.exists(p):
            return p
    name, d, f = next_sketch_path()
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(f):
        with open(f, "w", encoding="utf-8") as fh:
            fh.write(DEFAULT_CODE)
    add_recent(f)
    return f


def pick_file(open_mode, directory=None, save_filename=None):
    """Системный диалог выбора файла (pywebview). Возвращает абсолютный путь
    или None (отмена / нет реального окна). В headless-режиме (webview.windows
    пуст) всегда None — диалоги доступны только в настоящем окне приложения."""
    if not HAS_WEBVIEW or not getattr(webview, "windows", None):
        return None
    try:
        win = webview.windows[0]
        directory = directory or SKETCHBOOK
        os.makedirs(directory, exist_ok=True)
        dtype = webview.OPEN_DIALOG if open_mode else webview.SAVE_DIALOG
        kwargs = {
            "directory": directory,
            "allow_multiple": False,
            "file_types": ("Скетч Arduino (*.ino)", "Все файлы (*.*)"),
        }
        if not open_mode and save_filename:
            kwargs["save_filename"] = save_filename
        res = win.create_file_dialog(dtype, **kwargs)
    except Exception:
        return None
    if not res:
        return None
    if isinstance(res, (list, tuple)):
        return res[0] if res else None
    return res


# Скрываем консольное окно на Windows для всех дочерних процессов
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

def run_cli(args, timeout=900):
    """Запуск arduino-cli. Возвращает (ok, stdout, stderr)."""
    cmd = [ARDUINO_CLI] + args
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=cli_env(),
            creationflags=NO_WINDOW,
        )
        return p.returncode == 0, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return False, "", (
            "arduino-cli не найден. В бандле он должен лежать рядом с UNI-IDE.exe; "
            "в режиме разработки — быть в PATH. См. README."
        )
    except subprocess.TimeoutExpired:
        return False, "", "Превышено время ожидания операции."
    except Exception as e:  # noqa: BLE001
        return False, "", f"Ошибка запуска arduino-cli: {e}"


def save_code(code, path=None):
    """Пишет код по абсолютному пути проекта. Если путь не задан/неверен —
    падает на проект по умолчанию. Возвращает абсолютный путь .ino."""
    ino = resolve_sketch_path(path) or ensure_default_sketch()
    os.makedirs(os.path.dirname(ino), exist_ok=True)
    with open(ino, "w", encoding="utf-8") as fh:
        fh.write(code)
    return ino


# --------------------------------------------------------------------------- #
# Serial-монитор (pyserial)
# --------------------------------------------------------------------------- #
class Monitor:
    def __init__(self):
        self.ser = None
        self.port = None
        self.baud = None
        self.buffer = []
        self.lock = threading.Lock()
        self.running = False
        self.thread = None

    def start(self, port, baud):
        self.stop()
        if not HAS_PYSERIAL:
            raise RuntimeError("pyserial не установлен (pip install pyserial)")
        self.ser = serial.Serial(port, int(baud), timeout=0.2)
        self.port = port
        self.baud = baud
        self.running = True
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _reader(self):
        while self.running and self.ser:
            try:
                line = self.ser.readline()
                if line:
                    text = line.decode("utf-8", errors="replace")
                    with self.lock:
                        self.buffer.append(text)
                        if len(self.buffer) > 2000:
                            self.buffer = self.buffer[-1000:]
            except Exception:
                break

    def read(self):
        with self.lock:
            out = "".join(self.buffer)
            self.buffer = []
        return out

    def send(self, data):
        if self.ser and self.running:
            self.ser.write((data + "\n").encode("utf-8"))

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
            self.thread = None
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
            self.ser = None

    def is_on(self, port=None):
        if not self.running:
            return False
        if port is None:
            return True
        return self.port == port


monitor = Monitor()

# --------------------------------------------------------------------------- #
# Маршруты — страница
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.route("/vendor/<path:filename>")
def vendor(filename):
    """Локальные ресурсы (CodeMirror, шрифты) — чтобы IDE работала без интернета."""
    return send_from_directory(os.path.join(BASE, "vendor"), filename)


@app.route("/favicon.ico")
def favicon():
    """Иконка приложения (для вкладки/окна браузера в режиме отката)."""
    p = os.path.join(BASE, "icon.ico")
    if os.path.exists(p):
        return send_from_directory(BASE, "icon.ico")
    return ("", 404)


@app.route("/icon.png")
def icon_png():
    """PNG-иконка приложения (favicon высокого разрешения)."""
    p = os.path.join(BASE, "icon.png")
    if os.path.exists(p):
        return send_from_directory(BASE, "icon.png")
    return ("", 404)


# --------------------------------------------------------------------------- #
# Маршруты — код
# --------------------------------------------------------------------------- #
@app.route("/api/code", methods=["GET"])
def get_code():
    ino = resolve_sketch_path(request.args.get("path"))
    if not ino or not os.path.exists(ino):
        ino = ensure_default_sketch()
    try:
        with open(ino, "r", encoding="utf-8") as fh:
            code = fh.read()
    except Exception:
        code = DEFAULT_CODE
    add_recent(ino)
    return jsonify({"ok": True, "code": code, "fqbn": FQBN,
                    "path": ino, "name": sketch_name_of(ino)})


@app.route("/api/save", methods=["POST"])
def save():
    body = request.json or {}
    ino = save_code(body.get("code", ""), body.get("path"))
    return jsonify({"ok": True, "path": ino, "name": sketch_name_of(ino)})


# --------------------------------------------------------------------------- #
# Маршруты — управление проектами (скетчами)
# --------------------------------------------------------------------------- #
@app.route("/api/sketch/new", methods=["POST"])
def sketch_new():
    """Создаёт новый стандартный скетч uni_sketchN мгновенно, без диалога."""
    body = request.json or {}
    name, d, f = next_sketch_path()
    os.makedirs(d, exist_ok=True)
    code = body.get("code")
    text = code if code is not None else DEFAULT_CODE
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(text)
    add_recent(f)
    return jsonify({"ok": True, "path": f, "name": name, "code": text})


@app.route("/api/dialog/open", methods=["POST"])
def dialog_open():
    """Системный диалог «Открыть»: пользователь выбирает любой .ino на диске."""
    body = request.json or {}
    start_dir = body.get("dir") or SKETCHBOOK
    picked = pick_file(True, directory=start_dir)
    if not picked:
        return jsonify({"ok": False, "cancelled": True})
    ino = resolve_sketch_path(picked)
    if not ino or not os.path.exists(ino):
        return jsonify({"ok": False, "log": "Файл скетча не найден."})
    try:
        with open(ino, "r", encoding="utf-8") as fh:
            code = fh.read()
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "log": f"Не удалось открыть файл: {e}"})
    add_recent(ino)
    return jsonify({"ok": True, "path": ino, "name": sketch_name_of(ino), "code": code})


@app.route("/api/dialog/saveas", methods=["POST"])
def dialog_saveas():
    """Системный диалог «Сохранить как»: задаёт новое имя файла и папки проекта."""
    body = request.json or {}
    code = body.get("code", "")
    cur = safe_sketch_name(body.get("name")) or DEFAULT_SKETCH
    picked = pick_file(False, directory=SKETCHBOOK, save_filename=cur + ".ino")
    if not picked:
        return jsonify({"ok": False, "cancelled": True})
    picked = os.path.abspath(picked)
    parent = os.path.dirname(picked)
    base = os.path.basename(picked)
    if base.lower().endswith(".ino"):
        base = base[:-4]
    name = safe_sketch_name(base) or DEFAULT_SKETCH
    # Скетч Arduino должен лежать в одноимённой папке <name>/<name>.ino.
    # Если пользователь уже выбрал папку с этим именем — не вкладываем ещё раз.
    if os.path.basename(os.path.normpath(parent)).lower() == name.lower():
        sketch_dir = parent
    else:
        sketch_dir = os.path.join(parent, name)
    ino = os.path.join(sketch_dir, name + ".ino")
    try:
        os.makedirs(sketch_dir, exist_ok=True)
        with open(ino, "w", encoding="utf-8") as fh:
            fh.write(code)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "log": f"Не удалось сохранить: {e}"})
    add_recent(ino)
    return jsonify({"ok": True, "path": ino, "name": name})


@app.route("/api/recent", methods=["GET", "POST"])
def recent_list():
    """Список последних проектов. POST {clear:true} — очистить список."""
    if request.method == "POST":
        body = request.json or {}
        if body.get("clear"):
            save_recent([])
    out = []
    for p in load_recent():
        out.append({"path": p, "name": sketch_name_of(p), "exists": os.path.exists(p)})
    return jsonify({"ok": True, "recent": out})


@app.route("/api/window/new", methods=["POST"])
def window_new():
    """Открывает новое окно (или вкладку браузера) с указанным проектом."""
    body = request.json or {}
    ino = resolve_sketch_path(body.get("path")) or ensure_default_sketch()
    name = sketch_name_of(ino)
    url = "http://127.0.0.1:5000/?path=" + quote(ino)
    opened = False
    if HAS_WEBVIEW:
        try:
            webview.create_window(
                "UNI IDE — " + name, url,
                width=1280, height=800, min_size=(900, 600), maximized=True,
            )
            opened = True
        except Exception:
            opened = False
    if not opened:
        try:
            webbrowser.open(url)
            opened = True
        except Exception:
            opened = False
    return jsonify({"ok": opened, "url": url})


@app.route("/api/open", methods=["POST"])
def open_external():
    """Открывает внешнюю http(s)-ссылку в системном браузере."""
    url = ((request.json or {}).get("url", "") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return jsonify({"ok": False, "log": "Недопустимый URL."})
    try:
        webbrowser.open(url)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "log": str(e)})


# --------------------------------------------------------------------------- #
# Маршруты — порты
# --------------------------------------------------------------------------- #
CH340_VIDS = {0x1A86}   # Jiangsu QinHeng (CH340/CH341)

def _is_ch340(port_info):
    vid = getattr(port_info, "vid", None)
    desc = (getattr(port_info, "description", "") or "").upper()
    mfr  = (getattr(port_info, "manufacturer", "") or "").upper()
    return (vid in CH340_VIDS) or ("CH340" in desc) or ("CH341" in desc) \
           or ("CH340" in mfr) or ("CH341" in mfr)

@app.route("/api/ports", methods=["GET"])
def ports():
    found = {}   # addr -> {addr, ch340, description}

    if HAS_PYSERIAL:
        for p in list_ports.comports():
            found[p.device] = {
                "addr": p.device,
                "ch340": _is_ch340(p),
                "desc": p.description or "",
            }

    # дополняем данными arduino-cli (может добавить board-matched порты)
    ok, out, _ = run_cli(["board", "list", "--format", "json"])
    if ok and out.strip():
        try:
            data = json.loads(out)
            rows = data if isinstance(data, list) else data.get("detected_ports", [])
            for row in rows:
                port_info = row.get("port", row)
                addr = port_info.get("address")
                if addr and addr not in found:
                    found[addr] = {"addr": addr, "ch340": False, "desc": ""}
        except Exception:
            pass

    return jsonify({"ports": list(found.values())})


# --------------------------------------------------------------------------- #
# Прогрев среды компиляции
# --------------------------------------------------------------------------- #
# Первая компиляция проекта долгая (~минуту): собирается ядро ESP32 и тяжёлые
# библиотеки. Эти артефакты живут в build-path проекта и НЕ переносятся между
# проектами/машинами (ключ кэша зависит от пути). Поэтому при появлении нового
# проекта (старт, новое окно, «Сохранить как») запускаем ФОНОВУЮ компиляцию,
# чтобы ядро+библиотеки уже были собраны к первой явной «Проверке» (тогда она
# ~9 с). Прогрев и реальная компиляция делят блокировку на build-path — реальная
# компиляция дождётся прогрева, а не запустит второй процесс в том же каталоге.
_bp_locks = {}
_bp_guard = threading.Lock()
_warmed = set()        # build-path с уже собранным ядром
_warming = set()       # build-path, прогрев которых идёт сейчас


def _bp_lock(bp):
    with _bp_guard:
        lk = _bp_locks.get(bp)
        if lk is None:
            lk = threading.Lock()
            _bp_locks[bp] = lk
        return lk


def _bp_is_warm(bp):
    return bp in _warmed or os.path.exists(os.path.join(bp, "core", "core.a"))


def _warmup_worker(d, bp):
    try:
        with _bp_lock(bp):
            if not _bp_is_warm(bp):
                run_cli(["compile", "--fqbn", FQBN, "--build-path", bp, d])
            _warmed.add(bp)
    except Exception:
        pass
    finally:
        _warming.discard(bp)


@app.route("/api/warmup", methods=["POST"])
def warmup():
    """Фоновый прогрев build-path проекта (один раз). Возвращает сразу."""
    ino = resolve_sketch_path((request.json or {}).get("path"))
    if not ino:
        return jsonify({"ok": False})
    bp = build_path_for_dir(os.path.dirname(ino))
    if _bp_is_warm(bp):
        _warmed.add(bp)
        return jsonify({"ok": True, "warm": True})
    if bp not in _warming:
        _warming.add(bp)
        threading.Thread(target=_warmup_worker, args=(os.path.dirname(ino), bp), daemon=True).start()
    return jsonify({"ok": True, "warming": True})


@app.route("/api/warmup/status", methods=["GET"])
def warmup_status():
    ino = resolve_sketch_path(request.args.get("path"))
    bp = build_path_for_dir(os.path.dirname(ino)) if ino else None
    warm = bool(bp) and _bp_is_warm(bp)
    return jsonify({"ok": True, "warm": warm,
                    "warming": bool(bp) and (bp in _warming) and not warm})


# --------------------------------------------------------------------------- #
# Маршруты — компиляция / загрузка
# --------------------------------------------------------------------------- #
@app.route("/api/compile", methods=["POST"])
def compile_sketch():
    body = request.json or {}
    ino = save_code(body.get("code", ""), body.get("path"))
    d = os.path.dirname(ino)
    bp = build_path_for_dir(d)
    with _bp_lock(bp):                       # дождётся фонового прогрева, если он идёт
        ok, out, err = run_cli(["compile", "--fqbn", FQBN, "--build-path", bp, d])
        if ok:
            _warmed.add(bp)
    return jsonify({"ok": ok, "log": (out + err).strip()})


@app.route("/api/upload", methods=["POST"])
def upload_sketch():
    body = request.json or {}
    port = body.get("port")
    if not port:
        return jsonify({"ok": False, "log": "Не выбран COM-порт."})

    ino = save_code(body.get("code", ""), body.get("path"))
    d = os.path.dirname(ino)

    monitor_was_on = monitor.is_on(port)
    monitor_baud = monitor.baud
    if monitor_was_on:
        monitor.stop()

    bp = build_path_for_dir(d)
    with _bp_lock(bp):
        ok, out, err = run_cli(
            ["compile", "--upload", "-p", port, "--fqbn", FQBN, "--build-path", bp, d]
        )
        if ok:
            _warmed.add(bp)

    if monitor_was_on:
        try:
            monitor.start(port, monitor_baud or 115200)
        except Exception:
            pass

    return jsonify({"ok": ok, "log": (out + err).strip()})


def _stream_cli(args, on_stop=None):
    """Запускает arduino-cli и стримит вывод построчно как SSE."""
    import json as _json

    cmd = [ARDUINO_CLI] + args
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=cli_env(),
            creationflags=NO_WINDOW,
        )
    except FileNotFoundError:
        yield "data: " + _json.dumps({"line": "arduino-cli не найден.", "done": True, "ok": False}) + "\n\n"
        return

    for line in proc.stdout:
        line = line.rstrip("\n\r")
        if line:
            yield "data: " + _json.dumps({"line": line}) + "\n\n"

    proc.wait()
    ok = proc.returncode == 0
    yield "data: " + _json.dumps({"done": True, "ok": ok}) + "\n\n"

    if on_stop:
        on_stop()


def _stream_cli_locked(bp, args, on_stop=None):
    """_stream_cli под блокировкой build-path: если идёт фоновый прогрев,
    стрим дождётся его (фронт в это время крутит плавный прогресс-бар)."""
    lk = _bp_lock(bp)
    lk.acquire()
    try:
        for chunk in _stream_cli(args, on_stop=on_stop):
            yield chunk
        _warmed.add(bp)
    finally:
        lk.release()


@app.route("/api/compile/stream", methods=["GET"])
def compile_stream():
    ino = resolve_sketch_path(request.args.get("path")) or ensure_default_sketch()
    d = os.path.dirname(ino)
    bp = build_path_for_dir(d)
    return Response(
        stream_with_context(_stream_cli_locked(bp,
            ["compile", "--fqbn", FQBN, "--build-path", bp, d])),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/api/upload/stream", methods=["GET"])
def upload_stream():
    port = request.args.get("port", "")
    if not port:
        def _err():
            import json as _j
            yield "data: " + _j.dumps({"line": "Не выбран COM-порт.", "done": True, "ok": False}) + "\n\n"
        return Response(stream_with_context(_err()), mimetype="text/event-stream")

    ino = resolve_sketch_path(request.args.get("path")) or ensure_default_sketch()
    d = os.path.dirname(ino)
    bp = build_path_for_dir(d)

    monitor_was_on = monitor.is_on(port)
    monitor_baud = monitor.baud
    if monitor_was_on:
        monitor.stop()

    def on_done():
        if monitor_was_on:
            try:
                monitor.start(port, monitor_baud or 115200)
            except Exception:
                pass

    return Response(
        stream_with_context(_stream_cli_locked(bp,
            ["compile", "--upload", "-p", port, "--fqbn", FQBN, "--build-path", bp, d],
            on_stop=on_done,
        )),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------- #
# Разбор заголовков библиотеки → символы для автодополнения
# --------------------------------------------------------------------------- #
_CPP_KEYWORDS = {
    "if", "else", "for", "while", "switch", "case", "return", "sizeof", "do",
    "goto", "break", "continue", "default", "new", "delete", "public", "private",
    "protected", "class", "struct", "enum", "union", "namespace", "template",
    "typename", "operator", "const", "constexpr", "static", "virtual", "inline",
    "friend", "explicit", "volatile", "using", "typedef", "void", "true", "false",
    "this", "nullptr", "mutable", "register", "extern", "signed", "unsigned",
    "throw", "try", "catch", "and", "or", "not", "int", "float", "double", "bool",
    "char", "long", "short", "byte",
}
_HDR_EXT = (".h", ".hpp", ".hh")
_SKIP_DIRS = {"examples", "example", "extras", "test", "tests", "doc", "docs"}
_SYM_CACHE = {}  # install_dir -> (signature, symbols)


def _strip_comments(src):
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    return src


def _match_brace_block(text, open_idx):
    """text[open_idx] == '{'. Вернуть текст тела до парной '}'."""
    depth = 0
    n = len(text)
    i = open_idx
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1:i]
        i += 1
    return text[open_idx + 1:]


def _public_methods(body, cname, default_public):
    """Имена public-методов класса. Тела методов и вложенные типы схлопываются
    в ';' — берём только объявления на верхнем уровне тела класса."""
    out, depth, i, n = [], 0, 0, len(body)
    while i < n:
        c = body[i]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
            out.append(";")
        elif depth == 0:
            out.append(c)
        i += 1
    flat = "".join(out)
    parts = re.split(r"\b(public|private|protected)\s*:", flat)
    segments = [("public" if default_public else "private", parts[0])]
    k = 1
    while k < len(parts):
        segments.append((parts[k], parts[k + 1] if k + 1 < len(parts) else ""))
        k += 2
    methods = set()
    for acc, txt in segments:
        if acc != "public":
            continue
        for m in re.finditer(
            r"([A-Za-z_]\w*)\s*\([^()]*\)\s*(?:const\b)?\s*(?:override\b)?"
            r"\s*(?:noexcept\b)?\s*(?:=\s*0\s*)?;", txt):
            name = m.group(1)
            if name != cname and name not in _CPP_KEYWORDS:
                methods.add(name)
    return methods


def parse_library_symbols(install_dir):
    """Извлечь классы→методы, глобальные объекты и константы из заголовков."""
    empty = {"classes": {}, "objects": {}, "constants": []}
    if not install_dir or not os.path.isdir(install_dir):
        return empty
    classes, objects, constants = {}, {}, set()
    headers = []
    for root, dirs, files in os.walk(install_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(_HDR_EXT):
                headers.append(os.path.join(root, fn))
    for hp in headers[:120]:
        try:
            with open(hp, "r", encoding="utf-8", errors="ignore") as f:
                src = _strip_comments(f.read())
        except Exception:
            continue
        for m in re.finditer(r"#\s*define\s+([A-Za-z_]\w*)", src):
            constants.add(m.group(1))
        for em in re.finditer(
            r"\benum\s+(?:class\s+|struct\s+)?[A-Za-z_]*\s*(?::[^{]+)?\{([^}]*)\}", src):
            for vm in re.finditer(r"[A-Za-z_]\w*", em.group(1)):
                if vm.group(0) not in _CPP_KEYWORDS:
                    constants.add(vm.group(0))
        for om in re.finditer(r"\bextern\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*;", src):
            objects[om.group(2)] = om.group(1)
        for cm in re.finditer(r"\b(class|struct)\s+([A-Za-z_]\w*)\b[^{;]*\{", src):
            brace = src.find("{", cm.end() - 1)
            if brace < 0:
                continue
            body = _match_brace_block(src, brace)
            ms = _public_methods(body, cm.group(2), default_public=(cm.group(1) == "struct"))
            if ms:
                classes.setdefault(cm.group(2), set()).update(ms)
    return {
        "classes": {k: sorted(v) for k, v in classes.items() if v},
        "objects": objects,
        "constants": sorted(constants),
    }


def _dir_signature(install_dir):
    """Дешёвая подпись по mtime заголовков — для кэша и авто-обновления."""
    sig = 0
    for root, dirs, files in os.walk(install_dir):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]
        for fn in files:
            if fn.lower().endswith(_HDR_EXT):
                try:
                    sig ^= hash((fn, int(os.path.getmtime(os.path.join(root, fn)))))
                except Exception:
                    pass
    return sig


def parse_library_symbols_cached(install_dir):
    sig = _dir_signature(install_dir)
    hit = _SYM_CACHE.get(install_dir)
    if hit and hit[0] == sig:
        return hit[1]
    res = parse_library_symbols(install_dir)
    _SYM_CACHE[install_dir] = (sig, res)
    return res


def lib_install_dirs():
    """name → install_dir для установленных библиотек."""
    ok, out, _ = run_cli(["lib", "list", "--format", "json"])
    dirs = {}
    if ok and out.strip():
        try:
            data = json.loads(out)
            rows = data if isinstance(data, list) else data.get("installed_libraries", [])
            for row in rows:
                lib = row.get("library", row)
                nm = lib.get("name")
                idir = lib.get("install_dir") or lib.get("source_dir") or ""
                if nm and idir:
                    dirs[nm] = idir
        except Exception:
            pass
    return dirs


# --------------------------------------------------------------------------- #
# Маршруты — библиотеки
# --------------------------------------------------------------------------- #
@app.route("/api/libs", methods=["GET"])
def libs_list():
    ok, out, err = run_cli(["lib", "list", "--format", "json"])
    items = []
    if ok and out.strip():
        try:
            data = json.loads(out)
            rows = data if isinstance(data, list) else data.get("installed_libraries", [])
            for row in rows:
                lib = row.get("library", row)
                items.append({
                    "name":          lib.get("name"),
                    "version":       lib.get("version"),
                    "author":        lib.get("author", ""),
                    "maintainer":    lib.get("maintainer", ""),
                    "sentence":      lib.get("sentence", ""),
                    "paragraph":     lib.get("paragraph", ""),
                    "website":       lib.get("website", ""),
                    "category":      lib.get("category", ""),
                    "architectures": lib.get("architectures", []) or [],
                    "location":      lib.get("location", ""),
                    "examples":      lib.get("examples", []) or [],
                    "install_dir":   lib.get("install_dir") or lib.get("source_dir") or "",
                })
        except Exception:
            pass
    items.sort(key=lambda x: (x.get("name") or "").lower())
    return jsonify({"ok": ok, "libs": items, "log": err.strip()})


# --------------------------------------------------------------------------- #
# Маршруты — примеры из библиотек
# --------------------------------------------------------------------------- #
_EXAMPLES_CACHE = {"t": 0.0, "libs": None}
_EXAMPLES_TTL = 120  # сек; список меняется только при установке библиотек


def _scan_examples_dir(exdir):
    """[{name, path}] для папки examples библиотеки.
    Пример — подпапка с .ino внутри; поддерживаем 1 уровень группировки
    (examples/Группа/Пример/Пример.ino), как в Arduino IDE."""
    out = []

    def walk(d, prefix, depth):
        try:
            entries = sorted(os.listdir(d), key=str.lower)
        except Exception:
            return
        for e in entries:
            p = os.path.join(d, e)
            if not os.path.isdir(p):
                continue
            try:
                has_ino = any(f.lower().endswith(".ino") for f in os.listdir(p))
            except Exception:
                continue
            label = (prefix + " / " + e) if prefix else e
            if has_ino:
                out.append({"name": label, "path": p})
            elif depth < 2:
                walk(p, label, depth + 1)

    walk(exdir, "", 0)
    return out


@app.route("/api/examples", methods=["GET"])
def examples_list():
    """Примеры всех установленных библиотек (включая встроенные в ядро ESP32),
    сгруппированные по библиотеке. Пользовательские библиотеки — первыми."""
    now = time.time()
    if _EXAMPLES_CACHE["libs"] is not None and now - _EXAMPLES_CACHE["t"] < _EXAMPLES_TTL \
            and not request.args.get("refresh"):
        return jsonify({"ok": True, "libs": _EXAMPLES_CACHE["libs"]})

    ok, out, _ = run_cli(["lib", "list", "--all", "--fqbn", FQBN, "--format", "json"])
    libs = []
    if ok and out.strip():
        try:
            data = json.loads(out)
            rows = data if isinstance(data, list) else data.get("installed_libraries", [])
            for row in rows:
                lib = row.get("library", row)
                name = lib.get("name") or ""
                idir = lib.get("install_dir") or lib.get("source_dir") or ""
                loc = (lib.get("location") or "").lower()
                # arduino-cli сам отдаёт пути примеров; если нет — сканируем сами
                exs = [{"name": os.path.basename(os.path.normpath(p)), "path": p}
                       for p in (lib.get("examples") or []) if p]
                if not exs and idir:
                    exs = _scan_examples_dir(os.path.join(idir, "examples"))
                if not exs:
                    continue
                exs.sort(key=lambda x: x["name"].lower())
                libs.append({
                    "lib": name or os.path.basename(idir or "?"),
                    "user": ("user" in loc or "sketchbook" in loc),
                    "examples": exs,
                })
        except Exception:
            pass
    # порядок: библиотека UNI (родная для платформы) → пользовательские → остальные
    libs.sort(key=lambda L: (0 if L["lib"].upper() == "UNI" else (1 if L["user"] else 2),
                             L["lib"].lower()))
    _EXAMPLES_CACHE["t"] = now
    _EXAMPLES_CACHE["libs"] = libs
    return jsonify({"ok": True, "libs": libs})


@app.route("/api/example/open", methods=["POST"])
def example_open():
    """Открывает пример КАК КОПИЮ в Uni_Sketches (оригинал в библиотеке
    остаётся нетронутым — автосохранение пишет уже в копию)."""
    body = request.json or {}
    src = (body.get("path") or "").strip()
    if not src:
        return jsonify({"ok": False, "log": "Не указан путь примера."})
    src = os.path.abspath(src)
    src_dir = src if os.path.isdir(src) else os.path.dirname(src)
    src_ino = resolve_sketch_path(src_dir)
    if not src_ino or not os.path.exists(src_ino):
        return jsonify({"ok": False, "log": "Пример не найден."})

    base = safe_sketch_name(sketch_name_of(src_ino)) or "example"
    os.makedirs(SKETCHBOOK, exist_ok=True)
    name, n = base, 1
    while os.path.exists(os.path.join(SKETCHBOOK, name)):
        n += 1
        name = f"{base}{n}"
    dst_dir = os.path.join(SKETCHBOOK, name)

    try:
        # копируем папку целиком: рядом с .ino могут лежать .h/.cpp/данные
        shutil.copytree(src_dir, dst_dir)
        old_ino = os.path.join(dst_dir, os.path.basename(src_ino))
        dst_ino = os.path.join(dst_dir, name + ".ino")
        if os.path.normcase(old_ino) != os.path.normcase(dst_ino):
            os.rename(old_ino, dst_ino)
        with open(dst_ino, "r", encoding="utf-8", errors="replace") as fh:
            code = fh.read()
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(dst_dir, ignore_errors=True)
        return jsonify({"ok": False, "log": f"Не удалось скопировать пример: {e}"})

    add_recent(dst_ino)
    return jsonify({"ok": True, "path": dst_ino, "name": name, "code": code})


def _parse_lib_search(out):
    results = []
    try:
        data = json.loads(out)
        rows = data.get("libraries", data if isinstance(data, list) else [])
        for row in rows[:30]:
            latest = row.get("latest", {})
            results.append({
                "name": row.get("name"),
                "version": latest.get("version", ""),
                "sentence": latest.get("sentence", ""),
            })
    except Exception:
        pass
    return results


@app.route("/api/lib/search", methods=["POST"])
def libs_search():
    q = (request.json or {}).get("q", "").strip()
    if not q:
        return jsonify({"ok": False, "results": [], "log": "Пустой запрос."})

    ok, out, err = run_cli(["lib", "search", q, "--format", "json"])
    results = _parse_lib_search(out) if ok else []

    # Каталог библиотек отсутствует/устарел → поиск падает (или пуст).
    # Пробуем один раз скачать каталог и повторить (нужен интернет один раз).
    index_missing = (not ok) or ("index" in (err or "").lower())
    if index_missing and not results:
        up_ok, _, up_err = run_cli(["lib", "update-index"])
        if up_ok:
            ok, out, err = run_cli(["lib", "search", q, "--format", "json"])
            results = _parse_lib_search(out) if ok else []
        else:
            err = (err or "") + "\n" + (up_err or "")

    log = (err or "").strip()
    if not ok and not log:
        log = "Не удалось получить каталог библиотек."
    # подсказка для UI: проблема именно с каталогом/сетью, а не «нет совпадений»
    needs_net = (not ok) or (index_missing and not results)
    return jsonify({"ok": ok, "results": results, "log": log, "needs_net": needs_net})


@app.route("/api/lib/install", methods=["POST"])
def libs_install():
    """Установка библиотеки. Для уже установленной ставит свежую версию
    (этим же маршрутом работает кнопка «обновить»)."""
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "log": "Не указано имя библиотеки."})
    ok, out, err = run_cli(["lib", "install", name])
    if ok:
        # примеры новой библиотеки должны сразу появиться в меню «Примеры»
        _EXAMPLES_CACHE["libs"] = None
    return jsonify({"ok": ok, "log": (out + err).strip()})


@app.route("/api/lib/uninstall", methods=["POST"])
def libs_uninstall():
    """Удаление установленной библиотеки (только пользовательские;
    встроенные в ядро ESP32 удалить нельзя — arduino-cli вернёт ошибку)."""
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "log": "Не указано имя библиотеки."})
    ok, out, err = run_cli(["lib", "uninstall", name])
    if ok:
        _EXAMPLES_CACHE["libs"] = None
    return jsonify({"ok": ok, "log": (out + err).strip()})


# --------------------------------------------------------------------------- #
# Маршруты — автодополнение (динамический словарь из библиотек)
# --------------------------------------------------------------------------- #
@app.route("/api/autocomplete/libs", methods=["GET", "POST"])
def ac_libs():
    """GET → список библиотек, методы которых включены в автодополнение.
       POST {name, enabled} → включить/выключить библиотеку."""
    enabled = ac_load_enabled()
    if request.method == "POST":
        body = request.json or {}
        name = (body.get("name") or "").strip()
        want = bool(body.get("enabled"))
        if name:
            s = set(enabled)
            if want:
                s.add(name)
            else:
                s.discard(name)
            enabled = sorted(s, key=str.lower)
            ac_save_enabled(enabled)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/autocomplete/symbols", methods=["GET"])
def ac_symbols():
    """Объединённый словарь символов по всем включённым библиотекам.
       Парсит заголовки на лету (с кэшем по mtime), поэтому после обновления
       библиотеки словарь обновляется автоматически."""
    enabled = set(ac_load_enabled())
    dirs    = lib_install_dirs()
    classes, objects, constants, used = {}, {}, set(), []
    for name, idir in dirs.items():
        if name not in enabled or not idir:
            continue
        try:
            sym = parse_library_symbols_cached(idir)
        except Exception:
            continue
        used.append(name)
        for cname, methods in sym.get("classes", {}).items():
            classes.setdefault(cname, set()).update(methods)
        objects.update(sym.get("objects", {}))
        constants.update(sym.get("constants", []))
    classes_out = {c: sorted(m, key=str.lower) for c, m in classes.items()}
    return jsonify({
        "ok":        True,
        "libs":      sorted(used, key=str.lower),
        "classes":   classes_out,
        "objects":   objects,
        "constants": sorted(constants, key=str.lower),
    })


# --------------------------------------------------------------------------- #
# Маршруты — serial-монитор
# --------------------------------------------------------------------------- #
@app.route("/api/monitor/start", methods=["POST"])
def mon_start():
    body = request.json or {}
    port = body.get("port")
    baud = body.get("baud", 115200)
    if not port:
        return jsonify({"ok": False, "log": "Не выбран порт."})
    try:
        monitor.start(port, baud)
        return jsonify({"ok": True})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "log": str(e)})


@app.route("/api/monitor/stop", methods=["POST"])
def mon_stop():
    monitor.stop()
    return jsonify({"ok": True})


@app.route("/api/monitor/read", methods=["GET"])
def mon_read():
    return jsonify({"data": monitor.read(), "on": monitor.is_on()})


@app.route("/api/monitor/send", methods=["POST"])
def mon_send():
    monitor.send((request.json or {}).get("data", ""))
    return jsonify({"ok": True})


# --------------------------------------------------------------------------- #
# Проверка окружения
# --------------------------------------------------------------------------- #
@app.route("/api/env", methods=["GET"])
def env():
    cli_ok = shutil.which(ARDUINO_CLI) is not None or os.path.exists(ARDUINO_CLI)
    core_ok = False
    ver = ""
    if cli_ok:
        ok, out, _ = run_cli(["version"])
        ver = out.strip() if ok else ""
        ok2, out2, _ = run_cli(["core", "list", "--format", "json"])
        if ok2 and "esp32:esp32" in (out2 or ""):
            core_ok = True
    return jsonify({
        "arduino_cli": cli_ok,
        "esp32_core": core_ok,
        "pyserial": HAS_PYSERIAL,
        "portable": IS_PORTABLE,
        "version": ver,
        "fqbn": FQBN,
    })


try:
    import webview
    HAS_WEBVIEW = True
except ImportError:
    HAS_WEBVIEW = False


def start_flask():
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)


def set_taskbar_app_id():
    """Windows: задать собственный AppUserModelID, чтобы на панели задач
    отображался значок приложения, а не значок интерпретатора Python/IDLE.
    Должно вызываться ДО создания окна."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "UNI.IDE.ESP32.UniBase"
            )
        except Exception:
            pass


if __name__ == "__main__":
    set_taskbar_app_id()

    # В собранном .exe без консоли пишем ошибки в лог-файл
    if IS_FROZEN:
        try:
            logf = open(os.path.join(WORKSPACE, "uni-ide-log.txt"), "w", encoding="utf-8")
            sys.stdout = logf
            sys.stderr = logf
        except Exception:
            pass

    ensure_default_sketch()

    if HAS_WEBVIEW:
        # Flask в фоновом потоке, pywebview держит главный поток
        t = threading.Thread(target=start_flask, daemon=True)
        t.start()
        webview.create_window(
            "UNI IDE",
            "http://127.0.0.1:5000",
            width=1280,
            height=800,
            min_size=(900, 600),
            maximized=True,
        )
        # Иконка окна → отображается в заголовке (верхний левый угол),
        # на панели задач и в Диспетчере задач Windows.
        if os.path.exists(ICON_PATH):
            try:
                webview.start(icon=ICON_PATH)
            except TypeError:
                webview.start()   # старые версии pywebview без параметра icon
        else:
            webview.start()
    else:
        # Fallback: открыть в браузере
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
        print("UNI IDE -> http://127.0.0.1:5000")
        start_flask()
