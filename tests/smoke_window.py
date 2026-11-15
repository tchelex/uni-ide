#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
smoke_window.py — проверка, что собранное приложение открывает ОКНО и
интерфейс в нём действительно загрузился (а не только стартовал сервер).

Запускает бандл в обычном режиме (с окном), ждёт и смотрит журнал приложения:
страница внутри окна сама запрашивает /api/env — если этот запрос есть в
журнале, значит движок окна (WKWebView на macOS, Qt WebEngine на Linux)
отрисовал страницу и выполнил её JavaScript.

Нужна графическая сессия (на Linux в CI — xvfb-run).

    python tests/smoke_window.py <путь к исполняемому файлу бандла>
"""

import os
import sys
import time
import shutil
import socket
import tempfile
import subprocess
import urllib.request


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    exe = os.path.abspath(sys.argv[1])
    tmp = tempfile.mkdtemp(prefix="uni-window-")
    state = os.path.join(tmp, "state")
    home = os.path.join(tmp, "home")
    os.makedirs(state)
    os.makedirs(home)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = dict(os.environ, UNI_PORT=str(port), UNI_STATE_DIR=state, HOME=home)
    env.pop("UNI_HEADLESS", None)
    proc = subprocess.Popen([exe], env=env)
    log_path = os.path.join(state, "uni-ide-log.txt")
    ok_server = ok_page = False
    try:
        t0 = time.time()
        while time.time() - t0 < 90 and proc.poll() is None:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/update/status", timeout=2).read()
                ok_server = True
                break
            except Exception:
                time.sleep(0.5)
        t0 = time.time()
        while ok_server and time.time() - t0 < 60 and proc.poll() is None:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    if "GET /api/env" in f.read():
                        ok_page = True
                        break
            except Exception:
                pass
            time.sleep(1)
        alive = proc.poll() is None
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()

    log = ""
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            log = f.read()
    except Exception:
        pass
    fell_back = "Окно приложения не открылось" in log
    shutil.rmtree(tmp, ignore_errors=True)

    print("сервер отвечает:            ", ok_server)
    print("процесс жив:                ", alive)
    print("страница загрузилась в окне:", ok_page)
    print("откат на браузер:           ", fell_back)
    if ok_server and alive and ok_page and not fell_back:
        print("Окно приложения работает.")
        return 0
    print("== журнал приложения ==")
    print(log[-4000:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
