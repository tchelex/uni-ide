#!/bin/bash
# ============================================================================
#  UNI IDE — загрузчик для Linux с авто-выбором графического режима.
#
#  Этот скрипт кладётся сборкой (build.py) рядом с настоящим бинарём
#  UNI-IDE.bin и запускается вместо него: из меню приложений, с рабочего
#  стола и из терминала.
#
#  Зачем он нужен. На части систем (AMD + свежая Mesa/radeonsi, отдельные
#  конфигурации Wayland) Qt WebEngine не может создать графический контекст
#  («Could not initialize GLX», «Failed to finding matching FBConfig») и
#  приложение падает с SIGABRT ещё до появления окна. Скрипт в режиме auto
#  сначала пробует обычный запуск, а при таком падении на старте повторяет
#  запуск в программном рендеринге — вместо крэша пользователь получает
#  работающее окно (чуть медленнее отрисовка).
#
#  Режимы (переменная UNI_IDE_GL):
#    auto      — аппаратный, при падении графики на старте — программный
#    software  — всегда программный рендеринг (обходной путь вручную)
#    hardware  — всегда аппаратный, без повторных попыток
#
#  Журнал запусков: ~/.local/state/uni-ide/launch.log — прикладывайте его
#  к баг-репортам о запуске.
# ============================================================================

SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BIN="$SELF/UNI-IDE.bin"

LOGDIR="${XDG_STATE_HOME:-$HOME/.local/state}/uni-ide"
LOG="$LOGDIR/launch.log"
ATTEMPT="$LOGDIR/gpu-attempt.log"
mkdir -p "$LOGDIR" 2>/dev/null || true

say() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG" 2>/dev/null || true
}

# Сколько секунд длилась попытка: падение графического стека случается на
# старте; крэш через полчаса работы перезапускать «тихо» нельзя — это будет
# неожиданно для пользователя.
STARTUP_WINDOW=120

soft_env() {
    # программный рендеринг: Mesa llvmpipe для GLX/EGL, SwiftShader/без GPU
    # для Qt WebEngine и Qt Quick
    export LIBGL_ALWAYS_SOFTWARE=1
    export QT_QUICK_BACKEND=software
    export QTWEBENGINE_CHROMIUM_FLAGS="${QTWEBENGINE_CHROMIUM_FLAGS:+$QTWEBENGINE_CHROMIUM_FLAGS }--disable-gpu"
}

GL_MODE=$(printf '%s' "${UNI_IDE_GL:-auto}" | tr '[:upper:]' '[:lower:]')

case "$GL_MODE" in
    software|soft)
        say "запуск: принудительный программный рендеринг (UNI_IDE_GL=software)"
        soft_env
        # exec -a сохраняет имя процесса/класс окна «UNI-IDE» (ярлык в меню
        # ссылается на StartupWMClass=UNI-IDE)
        exec -a "UNI-IDE" "$BIN" "$@"
        ;;
    hardware|hw)
        say "запуск: принудительный аппаратный режим (UNI_IDE_GL=hardware)"
        exec -a "UNI-IDE" "$BIN" "$@"
        ;;
esac

say "запуск: режим auto, попытка с аппаратным ускорением"

# Первая попытка — в фоне, чтобы обёртка могла пересылать сигналы:
# без этого закрытие обёртки (terminate, systemd) оставляло бы сироту.
: >"$ATTEMPT" 2>/dev/null || ATTEMPT=/dev/null
SECONDS=0
"$BIN" "$@" 2> >(tee -a "$ATTEMPT" >&2) &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 143' TERM
trap 'kill -INT  "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 130' INT
trap 'kill -HUP  "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; exit 129' HUP
wait "$pid"
code=$?
trap - TERM INT HUP
elapsed=$SECONDS

# 128+N — выход по сигналу N. Падение графики — это SIGABRT/SIGSEGV/SIGTRAP
# плюс характерные строки Qt/ANGLE в stderr (см. issue #2).
if [ "$code" -ge 128 ] && [ "$code" -le 192 ] && [ "$elapsed" -lt "$STARTUP_WINDOW" ] \
   && grep -qiE 'FBConfig|GLX|EGL|QRhi|Vulkan|ANGLE|GLOzone' "$ATTEMPT" 2>/dev/null; then
    say "падение за ${elapsed}с (сигнал $((code - 128))): графический стек не завёлся — повторяю в программном режиме"
    echo "UNI IDE: графический драйвер не сработал, перезапускаюсь в режиме" >&2
    echo "программного рендеринга (медленнее, но надёжно)." >&2
    echo "Чтобы всегда запускать так:  UNI_IDE_GL=software" >&2
    soft_env
    exec -a "UNI-IDE" "$BIN" "$@"
fi

if [ "$code" -ge 128 ]; then
    say "выход по сигналу $((code - 128)) за ${elapsed}с, без признаков проблем с GL — повтор не выполнялся"
else
    say "штатный выход (код $code, ${elapsed}с)"
fi
exit "$code"
