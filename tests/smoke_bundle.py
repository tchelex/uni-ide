#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_bundle.py — проверка СОБРАННОГО приложения (не исходников).

Запускает готовый бандл без окна (UNI_HEADLESS=1) и через его же HTTP-API
проверяет то, ради чего IDE существует:
  * сервер стартует, тулчейн и ядро ESP32 на месте и видны офлайн;
  * скетч компилируется настоящим компилятором из бандла (и с библиотекой UNI,
    если она вшита);
  * повторная компиляция идёт из кэша (ускорение «Загрузить»);
  * «Стоп» действительно снимает дерево процессов компилятора;
  * автообновление: подложенный payload применяется загрузчиком при
    перезапуске, а нерабочий payload откатывается.

Запуск:
    python tests/smoke_bundle.py <путь к исполняемому файлу бандла>
      Windows: dist/UNI-IDE/UNI-IDE.exe
      macOS:   dist/UNI-IDE.app/Contents/MacOS/UNI-IDE
      Linux:   dist/UNI-IDE/UNI-IDE

На macOS/Linux каталог состояния уводится во временную папку (UNI_STATE_DIR),
домашняя папка пользователя не трогается. Используется GitHub Actions.
"""

import os
import re
import sys
import json
import time
import shutil
import socket
import tempfile
import subprocess
import urllib.request

BLINK = """void setup() {
  pinMode(2, OUTPUT);
}

void loop() {
  digitalWrite(2, HIGH);
  delay(300);
  digitalWrite(2, LOW);
  delay(300);
}
"""

UNI_SKETCH = """#include <UNI.h>

UniBase robot;

void setup() {
  robot.begin("UNI");
}

void loop() {
}
"""

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail and not cond else ""),
          flush=True)
    if not cond:
        FAILED.append(name)
    return cond


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class App:
    def __init__(self, exe, state, home):
        self.exe, self.state, self.home = exe, state, home
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.proc = None

    def start(self, timeout=90):
        env = dict(os.environ)
        env.update({
            "UNI_HEADLESS": "1",
            "UNI_PORT": str(self.port),
            "UNI_STATE_DIR": self.state,
            "HOME": self.home,
        })
        env.pop("UNI_UPDATE", None)
        self.proc = subprocess.Popen([self.exe], env=env)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            try:
                self.get("/api/update/status", timeout=2)
                return True
            except Exception:
                time.sleep(0.5)
        return False

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        self.proc = None

    def get(self, path, timeout=30):
        with urllib.request.urlopen(self.url + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def post(self, path, body, timeout=1200):
        req = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))


def sketch(root, name, code):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name + ".ino")
    with open(p, "w", encoding="utf-8") as f:
        f.write(code)
    return p


def compiler_procs():
    """Сколько процессов компилятора ESP32 сейчас живо (для проверки «Стоп»)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist"], capture_output=True, text=True).stdout
        else:
            out = subprocess.run(["ps", "-A", "-o", "comm="], capture_output=True, text=True).stdout
    except Exception:
        return -1
    return len(re.findall(r"xtensa-esp|arduino-cli", out))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    exe = os.path.abspath(sys.argv[1])
    if not os.path.isfile(exe):
        print("нет файла: " + exe)
        return 2

    tmp = tempfile.mkdtemp(prefix="uni-smoke-")
    state = os.path.join(tmp, "state")
    home = os.path.join(tmp, "home")
    work = os.path.join(tmp, "sketches")
    for d in (state, home, work):
        os.makedirs(d)
    split = os.name != "nt"          # раздельная раскладка: macOS/Linux
    app = App(exe, state, home)

    try:
        print("== запуск", flush=True)
        if not check("приложение стартовало и отвечает", app.start()):
            return 1

        env = app.get("/api/env", timeout=120)
        print("   env:", json.dumps(env, ensure_ascii=False), flush=True)
        check("arduino-cli найден", env.get("arduino_cli"))
        check("ядро ESP32 установлено (офлайн-тулчейн)", env.get("esp32_core"))
        check("переносимый режим", env.get("portable"))
        check("pyserial в бандле", env.get("pyserial"))
        app.get("/api/ports")
        check("опрос портов работает", True)

        if split:
            check("рабочий payload развёрнут в каталоге состояния",
                  os.path.isfile(os.path.join(state, "app", "server.py")))
            check("ферма ссылок на тулчейн создана",
                  os.path.islink(os.path.join(state, "arduino-data", "packages")))

        print("== компиляция", flush=True)
        ino = sketch(work, "blink", BLINK)
        t0 = time.time()
        r = app.post("/api/compile", {"code": BLINK, "path": ino})
        cold = time.time() - t0
        check("blink компилируется", r.get("ok"), (r.get("log") or "")[-1500:])
        t0 = time.time()
        r = app.post("/api/compile", {"code": BLINK + "\n// правка\n", "path": ino})
        warm = time.time() - t0
        print(f"   первая сборка {cold:.0f} с, повторная {warm:.0f} с", flush=True)
        check("повторная сборка идёт из кэша (быстрее первой)",
              r.get("ok") and warm < cold, f"{warm:.0f}c против {cold:.0f}c")
        st = app.get("/api/warmup/status?path=" + urllib.request.quote(ino))
        check("проект помечен прогретым", st.get("warm"))

        libs = app.get("/api/libs", timeout=120)
        names = json.dumps(libs, ensure_ascii=False)
        if '"UNI"' in names:
            ino2 = sketch(work, "uni_demo", UNI_SKETCH)
            r = app.post("/api/compile", {"code": UNI_SKETCH, "path": ino2})
            check("скетч с библиотекой UNI компилируется", r.get("ok"), (r.get("log") or "")[-1500:])
        else:
            print("   (библиотека UNI в бандл не вшита — проверка пропущена)", flush=True)

        print("== остановка сборки", flush=True)
        ino3 = sketch(work, "stopme", BLINK)
        app.post("/api/save", {"code": BLINK, "path": ino3})
        stream = urllib.request.urlopen(
            app.url + "/api/compile/stream?path=" + urllib.request.quote(ino3), timeout=600)
        time.sleep(1.5)                                 # компилятор уже запущен, но ещё не закончил
        r = app.post("/api/compile/stop", {"path": ino3})
        check("«Стоп» принят", r.get("stopped"))
        tail = stream.read().decode("utf-8", "replace")
        stream.close()
        check("сборка завершилась как отменённая", '"cancelled": true' in tail, tail[-300:])
        time.sleep(3)
        left = compiler_procs()
        check("процессы компилятора сняты", left == 0, f"осталось {left}")

        print("== автообновление", flush=True)
        cur = env.get("app_version")
        app.stop()
        app_dir = os.path.join(state, "app") if split else os.path.dirname(exe)
        upd = os.path.join(state if split else os.path.dirname(exe), "updates")
        pending = os.path.join(upd, "pending")

        def stage(version, broken=False):
            shutil.rmtree(pending, ignore_errors=True)
            os.makedirs(pending)
            with open(os.path.join(app_dir, "server.py"), encoding="utf-8") as f:
                src = f.read()
            src = re.sub(r'^VERSION\s*=\s*"[^"]+"', f'VERSION = "{version}"', src, flags=re.M)
            if broken:
                src = "raise RuntimeError('broken payload')\n" + src
            with open(os.path.join(pending, "server.py"), "w", encoding="utf-8") as f:
                f.write(src)
            with open(os.path.join(pending, "update.json"), "w", encoding="utf-8") as f:
                json.dump({"version": version, "min_base": "1.3.0"}, f)

        stage("98.0.0")
        check("перезапуск после обновления", app.start())
        v = app.get("/api/env", timeout=120).get("app_version")
        check("payload применён загрузчиком", v == "98.0.0", v)
        app.stop()

        stage("99.0.0", broken=True)
        check("перезапуск с нерабочим payload", app.start())
        v = app.get("/api/env", timeout=120).get("app_version")
        check("нерабочий payload откатился на прежнюю версию", v == "98.0.0", v)
        app.stop()

        if not split:
            # Windows: payload меняли прямо в dist — возвращаем исходный server.py
            with open(os.path.join(app_dir, "server.py"), encoding="utf-8") as f:
                src = f.read()
            src = re.sub(r'^VERSION\s*=\s*"[^"]+"', f'VERSION = "{cur}"', src, flags=re.M)
            with open(os.path.join(app_dir, "server.py"), "w", encoding="utf-8") as f:
                f.write(src)
            shutil.rmtree(upd, ignore_errors=True)
    finally:
        app.stop()
        log = os.path.join(state, "uni-ide-log.txt")
        if FAILED and os.path.isfile(log):
            print("== журнал приложения ==")
            try:
                print(open(log, encoding="utf-8", errors="replace").read()[-4000:])
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print("ПРОВАЛЕНО: " + "; ".join(FAILED))
        return 1
    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
