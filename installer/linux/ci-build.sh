#!/bin/bash
# ============================================================================
#  ci-build.sh — сборка Linux-бандла UNI IDE внутри контейнера AlmaLinux 8.
#  Вызывается из .github/workflows/build.yml:
#      docker run --rm -v "$PWD":/src -w /src almalinux:8 bash installer/linux/ci-build.sh
#
#  Зачем контейнер: в AlmaLinux 8 glibc 2.28 — собранное здесь запускается на
#  всех более новых системах (Ubuntu 20.04+, Debian 10+, Astra 1.7+, Альт 10+,
#  РЕД ОС 7.3+). Сборка на свежей Ubuntu таких систем бы не охватила.
#
#  Тулчейн (arduino-cli, arduino-data, arduino-user) к этому моменту уже лежит
#  в /src — его готовит prepare_bundle.py на раннере.
# ============================================================================
set -euo pipefail

echo "==> Системные библиотеки (для анализа зависимостей PyInstaller)"
# Важно: mesa-libGL/libdrm/libgbm нужны здесь только чтобы PyInstaller
# корректно разрешил зависимости Qt6. В готовый бандл они НЕ попадают —
# build.py (prune_linux_graphics_stack) вычищает драйверный стек из
# _internal: иначе собранные на AlmaLinux 8 libdrm/libGL/libEGL затеняют
# более новые системные и ломают запуск на свежих Mesa (issue #2).
dnf -y -q install epel-release >/dev/null
dnf -y -q install \
    binutils tar xz findutils which \
    libxcb xcb-util xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
    xcb-util-cursor libxkbcommon libxkbcommon-x11 \
    libX11 libXext libXrender libXcomposite libXdamage libXrandr libXtst libXi \
    libXfixes libXcursor libxkbfile libxshmfence libdrm mesa-libGL mesa-libEGL mesa-libgbm \
    nss nspr alsa-lib dbus-libs fontconfig freetype cups-libs \
    atk at-spi2-atk at-spi2-core pango cairo krb5-libs \
    >/dev/null

echo "==> Python 3.12 (сборка python-build-standalone, с общей libpython)"
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null
uv python install 3.12
uv venv --seed --python 3.12 /opt/venv
PY=/opt/venv/bin/python
$PY -m pip install --quiet --upgrade pip

echo "==> Сборка"
$PY build.py

# файлы созданы от root — возвращаем владельца, чтобы раннер мог их читать и чистить
if [ -n "${HOST_UID:-}" ]; then
    chown -R "$HOST_UID:${HOST_GID:-$HOST_UID}" dist build installer-out 2>/dev/null || true
    chown "$HOST_UID:${HOST_GID:-$HOST_UID}" ./*.spec 2>/dev/null || true
fi
