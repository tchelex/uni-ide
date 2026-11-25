/* ============================================================================
   i18n.js — локализация интерфейса UNI IDE.

   Русский — исходный язык разметки и сообщений (он же ключи словаря, как
   msgid в gettext). Этот файл добавляет английскую локаль:

     t("русская строка")            — перевод JS-сообщений (вызывается в
                                      index.html; при lang=ru возвращает ключ);
     t("строка с %s", значение)     — подстановка параметров;
     UNI_I18N.applyI18n(document.body) — перевод статической разметки:
                                      текстовые узлы и атрибуты title/
                                      placeholder/aria-label по точному
                                      совпадению с ключом (пробелы и переводы
                                      строк нормализуются);
     UNI_I18N.setLang("en"|"ru")    — переключение языка (сохраняется в
                                      localStorage и cookie uni_lang — по cookie
                                      server.py переводит сообщения API).

   Покрытие словаря проверяет tests/test_i18n.py: любой t()-ключ и любой
   кириллический текст в разметке обязан иметь перевод в EN.
   ============================================================================ */
(function () {
"use strict";

const EN = {
  /* ---- линтер кода ---- */
  "фигурная скобка «}»": "curly brace “}”",
  "круглая скобка «)»": "parenthesis “)”",
  "квадратная скобка «]»": "square bracket “]”",
  "Не закрыта кавычка «\"»": "Unterminated quote «\"»",
  "Не закрыт символ «'»": "Unterminated character literal «'»",
  "Лишняя или несогласованная скобка «": "Extra or mismatched bracket «",
  "Не закрыт комментарий «/*»": "Unterminated comment «/*»",
  "Не закрыта ": "Unterminated ",
  "скобка": "bracket",
  "не закрыта кавычка «\"»": "unterminated quote «\"»",
  "не закрыт символ «'»": "unterminated character literal «'»",
  "лишняя или несогласованная скобка «": "extra or mismatched bracket «",
  "не закрыт комментарий «/*»": "unterminated comment «/*»",
  "не закрыта ": "unterminated ",

  /* ---- строка состояния: среда, плата, обновления ---- */
  "среда: ошибка": "environment: error",
  "среда: готовится…": "environment: preparing…",
  "среда: готова": "environment: ready",
  "среда": "environment",
  "найден": "found",
  "НЕ найден": "NOT found",
  "ядро esp32: ": "esp32 core: ",
  "установлено": "installed",
  "НЕ установлено": "NOT installed",
  "serial-порт (pyserial): ": "serial port (pyserial): ",
  "доступен": "available",
  "недоступен": "unavailable",
  "⚠ arduino-cli не найден — см. README, раздел установки.":
    "⚠ arduino-cli not found — see the README, installation section.",
  "⚠ Ядро esp32:esp32 не установлено. Выполните: arduino-cli core install esp32:esp32":
    "⚠ The esp32:esp32 core is not installed. Run: arduino-cli core install esp32:esp32",
  "UniBase подключён": "UniBase connected",
  "UniBase не подключён": "UniBase not connected",
  "UniBase на ": "UniBase on ",
  "плата не подключена": "board not connected",
  "Нет прав на порт ": "No permission for port ",
  "✗ Плата найдена на ": "✗ Board found on ",
  ", но у пользователя нет прав на этот порт.":
    ", but the user has no permission for this port.",
  "  Один раз выполните в терминале:  sudo usermod -aG dialout $USER":
    "  Run once in a terminal:  sudo usermod -aG dialout $USER",
  "  и перезайдите в систему (или запустите install.sh из архива UNI IDE — он настроит доступ).":
    "  and log in again (or run install.sh from the UNI IDE archive — it sets up access).",
  "нет прав на порт ": "no permission for port ",
  "обновление ": "update ",
  " готово — перезапустите": " ready — restart",
  "Новая версия уже скачана и включится при следующем запуске UNI IDE":
    "The new version is already downloaded and will be enabled on the next UNI IDE start",
  "доступна ": "available ",
  " — скачать установщик": " — download installer",
  "Это обновление ставится полным установщиком. Нажмите, чтобы открыть страницу загрузки.":
    "This update requires the full installer. Click to open the download page.",

  /* ---- компиляция и прошивка ---- */
  "Компиляция скетча…": "Compiling sketch…",
  "Компиляция библиотек…": "Compiling libraries…",
  "Компиляция ядра ESP32…": "Compiling ESP32 core…",
  "Линковка…": "Linking…",
  "Готово, проверка размера…": "Done, checking size…",
  "Подключение к плате…": "Connecting to board…",
  "Запись прошивки…": "Flashing firmware…",
  "Проверка прошивки…": "Verifying firmware…",
  "Компиляция…": "Compiling…",
  "Готово": "Done",
  "Остановлено": "Stopped",
  "Ошибка": "Error",
  "✗ Потеряно соединение со сервером.": "✗ Connection to the server lost.",
  "⏹ Останавливаю…": "⏹ Stopping…",
  "остановка…": "stopping…",
  "→ Компиляция (esp32:esp32:esp32)…": "→ Compiling (esp32:esp32:esp32)…",
  "компиляция…": "compiling…",
  "⏹ Компиляция остановлена.": "⏹ Compilation stopped.",
  "✓ Компиляция успешна.": "✓ Compilation successful.",
  "✗ Ошибка компиляции.": "✗ Compilation error.",
  "остановлено": "stopped",
  "готово": "ready",
  "ошибка": "error",
  "выберите порт": "select a port",
  "✗ Сначала выберите COM-порт.": "✗ Select a COM port first.",
  "прошивка…": "uploading…",
  "⏹ Прошивка остановлена.": "⏹ Upload stopped.",
  "✓ Прошивка завершена.": "✓ Upload finished.",
  "✗ Ошибка компиляции — исправьте код (подробности выше).":
    "✗ Compilation failed — fix the code (details above).",
  "✗ Ошибка загрузки. Проверьте порт/кабель (на части плат держите BOOT).":
    "✗ Upload failed. Check the port/cable (on some boards hold BOOT).",
  "загружено": "uploaded",
  "ошибка компиляции": "compilation error",
  "Запись прошивки… %s": "Flashing firmware… %s",
  "→ Компиляция и прошивка на %s…": "→ Compile and upload to %s…",

  /* ---- проекты, файлы, примеры ---- */
  "открыто новое окно": "new window opened",
  "не удалось открыть окно": "failed to open window",
  "не удалось создать проект": "failed to create project",
  "создан проект ": "project created ",
  "не удалось открыть": "failed to open",
  "открыт ": "opened ",
  "не удалось сохранить": "failed to save",
  "сохранено как ": "saved as ",
  "<div class=\"recent-empty\">пока нет</div>": "<div class=\"recent-empty\">none yet</div>",
  "файл не найден": "file not found",
  "не удалось открыть пример": "failed to open example",
  "открыт пример: ": "example opened: ",
  " (копия)": " (copy)",
  "<div class=\"ex-empty\">примеры не найдены</div>": "<div class=\"ex-empty\">no examples found</div>",
  "Открыть копию примера: ": "Open a copy of the example: ",

  /* ---- serial ---- */
  "не удалось открыть порт": "failed to open port",
  "Отключить": "Disconnect",
  "Подключить": "Connect",
  "значение ": "value ",

  /* ---- библиотеки ---- */
  "<div class='lib-item desc'>загрузка…</div>": "<div class='lib-item desc'>loading…</div>",
  "<div class='lib-item desc'>пусто</div>": "<div class='lib-item desc'>empty</div>",
  "<div class='lib-item desc'>ничего не найдено</div>": "<div class='lib-item desc'>nothing found</div>",
  "пользователь": "user",
  "платформа": "platform",
  "<label class=\"lib-ac\" title=\"Добавлять методы этой библиотеки в подсказки кода\">":
    "<label class=\"lib-ac\" title=\"Add methods of this library to code suggestions\">",
  "<span>подсказки</span>": "<span>suggestions</span>",
  "<div class='lib-item desc'>поиск…</div>": "<div class='lib-item desc'>searching…</div>",
  "<div class=\"lib-item desc\">Не удалось получить каталог библиотек.<br>":
    "<div class=\"lib-item desc\">Failed to fetch the library catalog.<br>",
  "Чтобы искать и устанавливать библиотеки, нужен <b>интернет</b> ":
    "Searching and installing libraries requires an <b>internet connection</b> ",
  "(каталог скачивается один раз, дальше работает из кэша).":
    "(the catalog is downloaded once, then works from cache).",
  "<button class=\"mini-btn\" data-a=\"install\">установить</button>":
    "<button class=\"mini-btn\" data-a=\"install\">install</button>",
  "<button class=\"mini-btn danger\" data-a=\"remove\">удалить</button>":
    "<button class=\"mini-btn danger\" data-a=\"remove\">remove</button>",
  "библиотека удалена: ": "library removed: ",
  "библиотека обновлена: ": "library updated: ",
  "библиотека установлена: ": "library installed: ",
  "<span class=\"lib-chip\">%s прим.</span>": "<span class=\"lib-chip\">%s ex.</span>",
  "<a class=\"lib-web\" href=\"%s\" target=\"_blank\" rel=\"noopener\">сайт ↗</a>":
    "<a class=\"lib-web\" href=\"%s\" target=\"_blank\" rel=\"noopener\">site ↗</a>",
  "<button class=\"mini-btn\" data-a=\"update\" title=\"Установлена v%s, доступна v%s\">обновить</button>":
    "<button class=\"mini-btn\" data-a=\"update\" title=\"v%s installed, v%s available\">update</button>",
  " <span class=\"lib-chip\">установлена v%s</span>":
    " <span class=\"lib-chip\">v%s installed</span>",

  /* ---- консоль, поиск/замена, форматирование ---- */
  "✓ Скопировано": "✓ Copied",
  "⎘ Копировать": "⎘ Copy",
  "заменено: ": "replaced: ",
  "⚠ Формат: ": "⚠ Format: ",
  " (строка ": " (line ",
  ") — сначала исправьте": ") — fix it first",
  "код отформатирован ✓": "code formatted ✓",

  /* ---- симулятор ---- */
  "не загрузился ": "failed to load ",
  "Симулятор не запущен:": "Simulator not started:",
  "Ошибка: ": "Error: ",
  "▶ выполняется": "▶ running",
  "✓ завершено": "✓ finished",
  "с · сцена: ": "s · scene: ",
  " · столкновение": " · collision",
  "пусто": "empty",
  "линия": "line",
  "препятствия": "obstacles",
  "Свернуть симулятор": "Collapse simulator",
  "Симулятор (2D)": "Simulator (2D)",
  "Ошибка разбора: ": "Parse error: ",
  "Предыдущий модуль": "Previous module",
  "Следующий модуль": "Next module",
  "Убрать этаж": "Remove floor",
  "Без модуля": "No module",
  "этаж ": "floor ",
  "Только платформа UniBase: моторы N20 150 RPM, энкодеры, дисплей, батарея.":
    "UniBase platform only: N20 150 RPM motors, encoders, display, battery.",
  "<span class=\"warn\">⚠ Порт занят дважды: %s и %s. На реальном роботе так не подключить.</span> ":
    "<span class=\"warn\">⚠ Pin used twice: %s and %s. You cannot wire that on the real robot.</span> ",

  /* ---- статическая разметка (applyI18n) ---- */
  "Файл ▾": "File ▾",
  "Новый проект": "New project",
  "Открыть проект…": "Open project…",
  "Примеры": "Examples",
  "загрузка…": "loading…",
  "Сохранить как…": "Save as…",
  "Последние проекты": "Recent projects",
  "Открыть в новом окне": "Open in new window",
  "порты…": "ports…",
  "<option value=''>нет портов</option>": "<option value=''>no ports</option>",
  "Проверить": "Verify",
  "Загрузить": "Upload",
  "Стоп": "Stop",
  "Тема:": "Theme:",
  "Светлая (Arduino)": "Light (Arduino)",
  "Размер:": "Size:",
  "Язык:": "Language:",
  "Заменить": "Replace",
  "Все": "All",
  "Консоль": "Console",
  "Библиотеки": "Libraries",
  "Документация": "Documentation",
  "✕ Очистить": "✕ Clear",
  "Готово к работе. Нажмите «Проверить» или «Загрузить».":
    "Ready. Press “Verify” or “Upload”.",
  "Текст": "Text",
  "График": "Plot",
  "Время": "Time",
  "График чисел из Serial.": "Plot of numbers from Serial.",
  "Плата должна печатать числа, например:": "The board should print numbers, e.g.:",
  "— одна линия,": "— one line,",
  "или": "or",
  "— несколько линий.": "— several lines.",
  "Установленные": "Installed",
  "Установить библиотеку": "Install library",
  "обновить": "refresh",
  "Искать": "Search",
  "Введите название и нажмите «Искать». Для поиска и установки нужен интернет; после установки примеры библиотеки появятся в меню «Файл → Примеры».":
    "Enter a name and press “Search”. Searching and installing require an internet connection; once installed, the library examples appear in the “File → Examples” menu.",
  "Документация UNI": "UNI documentation",
  "↗ В браузере": "↗ In browser",
  "▶ Старт": "▶ Start",
  "Сцена": "Scene",
  "Пусто": "Empty",
  "Линия": "Line",
  "Препятствия": "Obstacles",
  "Скорость": "Speed",
  "Конструктор": "Builder",
  "+ этаж": "+ floor",
  "Нажмите «Старт» — скетч из редактора поедет в симуляторе.":
    "Press “Start” — the sketch from the editor will run in the simulator.",

  /* ---- атрибуты title/placeholder (applyI18n) ---- */
  "COM-порт": "COM port",
  "Обновить порты": "Refresh ports",
  "Только компиляция": "Compile only",
  "Скомпилировать и прошить": "Compile and upload",
  "Остановить компиляцию/прошивку": "Stop compilation/upload",
  "Уменьшить": "Decrease",
  "Увеличить": "Increase",
  "Форматировать код: отступы и пробелы": "Format code: indentation and spaces",
  "Поиск (Ctrl+F)": "Search (Ctrl+F)",
  "Назад (Shift+Enter)": "Previous (Shift+Enter)",
  "Вперёд (Enter)": "Next (Enter)",
  "Замена (Ctrl+H)": "Replace (Ctrl+H)",
  "Очистить (Esc)": "Clear (Esc)",
  "Заменить на…": "Replace with…",
  "Заменить текущее совпадение (Enter)": "Replace current match (Enter)",
  "Заменить все совпадения": "Replace all matches",
  "Потяните, чтобы изменить ширину панели (двойной клик — сброс)":
    "Drag to resize the panel (double-click — reset)",
  "Прокрутить вниз": "Scroll down",
  "Очистить": "Clear",
  "Текстовый вывод порта": "Port text output",
  "График чисел из Serial (как плоттер Arduino)":
    "Plot of numbers from Serial (like the Arduino plotter)",
  "Добавлять метку времени в начале каждой строки":
    "Add a timestamp at the start of each line",
  "отправить в порт + Enter": "send to port + Enter",
  "фильтр установленных…": "filter installed…",
  "поиск библиотеки (напр. Adafruit GFX)": "library search (e.g. Adafruit GFX)",
  "Открыть в браузере": "Open in browser",
  "Потяните, чтобы изменить ширину симулятора (двойной клик — сброс)":
    "Drag to resize the simulator (double-click — reset)",
  "Запустить скетч в симуляторе": "Run the sketch in the simulator",
  "Сброс": "Reset",
  "Сцена: где едет робот": "Scene: where the robot drives",
  "Скорость симуляции": "Simulation speed",
  "Свернуть": "Collapse",
  "Добавить этаж (модуль UniDev)": "Add a floor (UniDev module)",
  "Проверка среды…": "Checking environment…",
};

/* ------------------------------------------------------------------------- */
function norm(s) { return String(s).replace(/\s+/g, " ").trim(); }

const EN_NORM = {};
Object.keys(EN).forEach((k) => { EN_NORM[norm(k)] = EN[k]; });

let lang = "ru";
try { lang = localStorage.getItem("uni-ide-lang") || "ru"; } catch (_) { }

// cookie — для server.py (сообщения API на языке интерфейса)
try {
  document.cookie = "uni_lang=" + lang + ";path=/;max-age=31536000;SameSite=Lax";
} catch (_) { }

function lookup(s) {
  if (EN[s] !== undefined) return EN[s];
  const n = EN_NORM[norm(s)];
  return n !== undefined ? n : s;
}

/* t("строка") или t("строка с %s", значение, ...) — подстановка %s по порядку. */
function t(s) {
  let out = lang === "en" ? lookup(s) : String(s);
  if (arguments.length > 1) {
    let i = 1;
    out = out.replace(/%s/g, () => String(arguments[i++] ?? ""));
  }
  return out;
}

const CYR = /[А-Яа-яЁё]/;

function translateTextNodes(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const v = node.nodeValue;
    if (!CYR.test(v)) continue;
    const tr = lookup(v);
    if (tr === v) continue;
    const lead = v.match(/^\s*/)[0];
    const trail = v.match(/\s*$/)[0];
    node.nodeValue = lead + tr + trail;
  }
}

function translateAttrs(root) {
  root.querySelectorAll("[title],[placeholder],[aria-label]").forEach((el) => {
    for (const a of ["title", "placeholder", "aria-label"]) {
      const v = el.getAttribute(a);
      if (v && CYR.test(v)) {
        const tr = lookup(v);
        if (tr !== v) el.setAttribute(a, tr);
      }
    }
  });
}

function applyI18n(root) {
  if (lang !== "en") return;
  document.documentElement.lang = "en";
  translateTextNodes(root);
  translateAttrs(root);
}

function setLang(l) {
  try { localStorage.setItem("uni-ide-lang", l); } catch (_) { }
  try {
    document.cookie = "uni_lang=" + l + ";path=/;max-age=31536000;SameSite=Lax";
  } catch (_) { }
  location.reload();
}

window.t = t;
window.UNI_I18N = { EN, t, applyI18n, setLang, getLang: () => lang };
})();
