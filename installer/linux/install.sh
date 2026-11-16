#!/bin/sh
# ============================================================================
#  UNI IDE — установка на Linux.
#
#  Запуск (из распакованной папки):   ./install.sh
#
#  Что делает:
#    1) копирует программу в ~/.local/opt/UNI-IDE (права администратора НЕ нужны);
#    2) добавляет «UNI IDE» в меню приложений и ярлык на рабочий стол;
#    3) предлагает один раз настроить доступ к плате (USB-порт) — только на
#       этот шаг нужен пароль администратора. Пропустить:  ./install.sh --no-serial
#
#  Проекты учеников (~/Uni_Sketches) и настройки при переустановке сохраняются.
# ============================================================================
set -e

HERE=$(cd "$(dirname "$0")" && pwd)
DEST="${UNI_IDE_HOME:-$HOME/.local/opt/UNI-IDE}"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
DESKTOP_FILE="$APPS/uni-ide.desktop"
RULES=/etc/udev/rules.d/60-uni-ide.rules

SERIAL=ask
for a in "$@"; do
    case "$a" in
        --no-serial) SERIAL=no ;;
        --serial)    SERIAL=yes ;;
        -h|--help)   sed -n '2,15p' "$0"; exit 0 ;;
    esac
done

if [ ! -x "$HERE/UNI-IDE/UNI-IDE" ]; then
    echo "Не найдена папка UNI-IDE рядом с install.sh — распакуйте архив целиком." >&2
    exit 1
fi

echo "==> Устанавливаю UNI IDE в $DEST"
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST.new"
cp -a "$HERE/UNI-IDE" "$DEST.new"
rm -rf "$DEST"
mv "$DEST.new" "$DEST"
cp "$HERE/uninstall.sh" "$DEST/uninstall.sh" 2>/dev/null || true
chmod +x "$DEST/UNI-IDE" "$DEST/uninstall.sh" 2>/dev/null || true

echo "==> Добавляю ярлык в меню приложений"
mkdir -p "$APPS" "$ICONS"
cp "$HERE/icon.png" "$ICONS/uni-ide.png"
cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=UNI IDE
Comment=Среда программирования UniBase (ESP32)
Exec="$DEST/UNI-IDE"
Icon=$ICONS/uni-ide.png
Terminal=false
Categories=Development;Education;
StartupWMClass=UNI-IDE
EOF
chmod +x "$DESKTOP_FILE"
update-desktop-database "$APPS" >/dev/null 2>&1 || true

# ярлык на рабочий стол (если он есть)
DESK=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESK=$(xdg-user-dir DESKTOP 2>/dev/null || true)
fi
if [ -n "$DESK" ] && [ -d "$DESK" ] && [ "$DESK" != "$HOME" ]; then
    cp "$DESKTOP_FILE" "$DESK/uni-ide.desktop"
    chmod +x "$DESK/uni-ide.desktop"
    # GNOME требует пометить ярлык доверенным
    gio set "$DESK/uni-ide.desktop" metadata::trusted true >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# Доступ к плате. USB-порт (/dev/ttyUSB0) в Linux принадлежит группе
# dialout/uucp; без настройки плата видна, но прошивка падает с
# «Permission denied». Правило udev открывает порт для USB-UART-мостов,
# которые стоят на ESP32 (CH340, CP210x, FTDI, встроенный USB Espressif).
# ---------------------------------------------------------------------------
setup_serial() {
    SUDO=""
    if [ "$(id -u)" != "0" ]; then
        if ! command -v sudo >/dev/null 2>&1; then
            echo "   sudo не найден — попросите администратора выполнить: sh \"$HERE/install.sh\" --serial" >&2
            return 1
        fi
        SUDO=sudo
    fi
    TMP=$(mktemp)
    cat > "$TMP" <<'EOF'
# UNI IDE: доступ к плате ESP32 (UniBase) без прав администратора
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", MODE="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", MODE="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", MODE="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", MODE="0666", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF
    $SUDO install -m 0644 "$TMP" "$RULES"
    rm -f "$TMP"
    $SUDO udevadm control --reload-rules >/dev/null 2>&1 || true
    $SUDO udevadm trigger --subsystem-match=tty >/dev/null 2>&1 || true
    # на случай систем без udev-правил: ещё и группа порта
    for g in dialout uucp; do
        if getent group "$g" >/dev/null 2>&1; then
            $SUDO usermod -aG "$g" "${SUDO_USER:-$USER}" >/dev/null 2>&1 || true
        fi
    done
    echo "   Доступ к плате настроен. Если плата уже подключена — переподключите её."
}

if [ "$SERIAL" = "ask" ]; then
    if [ -f "$RULES" ]; then
        SERIAL=no          # уже настроено прошлой установкой
    elif [ -t 0 ]; then
        printf "==> Настроить доступ к плате ESP32 (нужен пароль администратора)? [Д/н] "
        read -r ans || ans=""
        case "$ans" in
            n|N|н|Н|no|нет) SERIAL=no ;;
            *) SERIAL=yes ;;
        esac
    else
        SERIAL=no
        echo "==> Доступ к плате не настраивался (запуск без терминала)."
        echo "    Позже выполните в терминале:  ./install.sh --serial"
    fi
fi
if [ "$SERIAL" = "yes" ]; then
    echo "==> Настраиваю доступ к плате"
    setup_serial || echo "   Не удалось — IDE установлена, но для прошивки доступ придётся настроить позже." >&2
fi

# brltty (экранный помощник для дисплеев Брайля) на Ubuntu/Debian перехватывает
# адаптер CH340 — плата тогда вообще не появляется в списке портов.
if command -v brltty >/dev/null 2>&1 || [ -f /usr/lib/udev/rules.d/85-brltty.rules ]; then
    echo
    echo "ВНИМАНИЕ: в системе установлен brltty. Он может перехватывать плату (CH340),"
    echo "и она не появится в списке портов. Если так — удалите его:"
    echo "    sudo apt remove brltty"
fi

echo
echo "Готово. UNI IDE — в меню приложений. Запуск из терминала:"
echo "    \"$DEST/UNI-IDE\""
