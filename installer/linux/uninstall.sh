#!/bin/sh
# ============================================================================
#  UNI IDE — удаление с Linux.
#
#     ./uninstall.sh           удалить программу (проекты и настройки остаются)
#     ./uninstall.sh --purge   удалить ещё и настройки, кэш сборки и библиотеки
#
#  Папка проектов ~/Uni_Sketches НЕ удаляется никогда — это работы учеников.
# ============================================================================
set -e

DEST="${UNI_IDE_HOME:-$HOME/.local/opt/UNI-IDE}"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE="$DATA/UNI-IDE"

PURGE=no
[ "$1" = "--purge" ] && PURGE=yes

rm -f "$DATA/applications/uni-ide.desktop" "$DATA/icons/hicolor/256x256/apps/uni-ide.png"
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESK=$(xdg-user-dir DESKTOP 2>/dev/null || true)
    [ -n "$DESK" ] && rm -f "$DESK/uni-ide.desktop"
fi
update-desktop-database "$DATA/applications" >/dev/null 2>&1 || true

if [ "$PURGE" = "yes" ]; then
    rm -rf "$STATE"
    echo "Настройки и кэш удалены: $STATE"
else
    # кэш сборки и скачанные обновления восстановимы — чистим; библиотеки и настройки оставляем
    rm -rf "$STATE/build-tmp" "$STATE/build-cache" "$STATE/updates" "$STATE/arduino-downloads"
fi

# последним — сама программа (скрипт может лежать внутри неё)
rm -rf "$DEST"
echo "UNI IDE удалена. Проекты остались в ~/Uni_Sketches."
echo "Правило доступа к плате (если ставили):  sudo rm /etc/udev/rules.d/60-uni-ide.rules"
