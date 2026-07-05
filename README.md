# UNI IDE — простая IDE для программирования UniBase

Редактор кода Arduino C++ с подсветкой, менеджером библиотек, компиляцией,
прошивкой и serial-монитором. Загрузка зашита **только** на плату
**UniBase (ESP32 Dev Module)** (`FQBN = esp32:esp32:esp32`).

Это локальное приложение: маленький Python-сервер оборачивает `arduino-cli`,
а фронтенд открывается в браузере. Браузер не умеет компилировать C++, поэтому
компиляция и прошивка идут через `arduino-cli`, который внутри сам вызывает
`esptool` (тот самый пайплайн `bootloader.bin / partitions.bin / firmware.bin`).

## ⬇️ Скачать (Windows)

Готовый установщик — на странице релизов:

### [→ Скачать последнюю версию UNI IDE](https://github.com/tchelex/uni-ide/releases/latest)

Ставится **без прав администратора**; на последнем шаге можно установить драйвер
CH340 для платы. Памятка ученику — [student-README.txt](student-README.txt).
Сборка из исходников описана ниже.

```
uni-ide/
├── server.py             # backend (Flask + arduino-cli)
├── index.html            # интерфейс (CodeMirror)
├── build.py              # сборка exe + установщика (Windows)
├── prepare_bundle.py     # разовое «запекание» офлайн-тулчейна ESP32
├── installer/UNI-IDE.iss # скрипт установщика (Inno Setup)
├── requirements.txt
└── Uni_Sketches/         # создаётся автоматически — проекты учеников
    └── uni_sketch1/uni_sketch1.ino
```

## 1. Установка arduino-cli

**Windows (PowerShell):**
```powershell
winget install ArduinoSA.CLI
```
или скачать с https://arduino.github.io/arduino-cli/latest/installation/ и
добавить в `PATH`.

**macOS:**
```bash
brew install arduino-cli
```

**Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh
```

Проверка: `arduino-cli version`

## 2. Установка ядра ESP32 (один раз)

```bash
arduino-cli config init
arduino-cli config add board_manager.additional_urls https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32
```

## 3. Зависимости Python

```bash
pip install -r requirements.txt
```

## 4. Запуск

```bash
python server.py
```
Откройте в браузере **http://127.0.0.1:5000**

В нижней строке статуса загорятся индикаторы `arduino-cli` и `esp32 core` —
если они красные, вернитесь к шагам 1–2.

## Как пользоваться

1. Подключите плату по USB → нажмите `↻`, выберите COM-порт в выпадающем списке.
2. Пишите код в редакторе (он сохраняется автоматически).
3. **✓ Проверить** — только компиляция (`Ctrl/Cmd + R`).
4. **▶ Загрузить** — компиляция и прошивка на выбранный порт (`Ctrl/Cmd + Enter`).
5. Вкладка **Serial** (справа) — serial-монитор: кнопка «Подключить»,
   выбор скорости, отправка строк в порт.
6. Вкладка **Библиотеки** — поиск и установка по имени (`arduino-cli lib`).

## Тесты

Базовые функции серверного API (проекты, сохранение, недавние, примеры,
прогрев среды, опрос портов) покрыты тестами — без реального arduino-cli
и без сети, выполняются пару секунд:

```bash
python -m unittest discover -s tests -v
```

Запускайте перед сборкой релиза.

## Частые проблемы

- **Порт не появляется** — нет драйвера USB-UART (CP2102 / CH340) или плохой кабель.
- **`Failed uploading` / `Connecting...`** — на части плат нужно удерживать кнопку
  **BOOT** в момент начала прошивки.
- **Долгая первая компиляция** — это нормально, ESP32-ядро прогревает кэш.
- **arduino-cli не в PATH** — задайте путь явно:
  `ARDUINO_CLI=/полный/путь/arduino-cli python server.py`

---

# Офлайн-бандл для учеников (Windows, без интернета)

Сценарий: каждый ученик на своём ноутбуке с Windows, интернета на занятии нет,
нужен строго Arduino C++. Тогда весь тулчейн зашивается в один кликабельный
бандл. Готовите его **вы один раз с интернетом**, дальше раздаёте ученикам
флешкой/файлом — они просто запускают `UNI-IDE.exe`.

### Что делаете вы (один раз, с интернетом)

1. Запекаете тулчейн в переносимую папку:
   ```
   python prepare_bundle.py
   ```
   Скачает `arduino-cli.exe` и установит ядро `esp32:esp32` в `./arduino-data`
   (несколько сотен МБ — это нормально, ужать нельзя). Нужные библиотеки можно
   добавить в список `LIBS` внутри скрипта.
2. Ставите **Inno Setup 6** (один раз на машине сборки):
   https://jrsoftware.org/isdl.php  или  `winget install JRSoftware.InnoSetup`
3. Собираете установщик одной командой:
   ```
   python build.py
   ```
   `build.py` соберёт `UNI-IDE.exe` (PyInstaller), положит рядом тулчейн и
   ресурсы и упакует всё в установщик:
   ```
   installer-out\UNI-IDE-Setup-1.0.0.exe
   ```
   Нужен только бандл без установщика — `python build.py --no-installer`
   (результат в папке `dist\UNI-IDE`).
4. Раздаёте ученикам **один файл** `UNI-IDE-Setup-x.x.x.exe`.

### Что делает ученик

Запускает `UNI-IDE-Setup-x.x.x.exe` — установка идёт **без прав
администратора** (программа ставится в папку пользователя). На последнем шаге
галочкой можно установить драйвер CH340. Дальше запускает **UNI IDE** ярлыком
с рабочего стола или из меню «Пуск» — откроется окно среды. Интернет не нужен.
Памятка для ученика — `student-README.txt`.

### Честные ограничения этого пути

- **Размер.** Ядро ESP32 + компилятор ≈ 0.5–1 ГБ. Это разовая раздача.
- **Драйвер CH340.** На «чистом» ноутбуке Windows его может не быть, и плата
  не появится в списке портов. Установщик предлагает поставить его галочкой
  (права администратора нужны только на сам драйвер).
- **Сборка под конкретную ОС.** Windows-установщик собирается на Windows.
  Для macOS нужна отдельная сборка на Mac (запланирована).

> Сборку выполняйте на Windows (PyInstaller и Inno Setup работают под текущую
> ОС). Весь конвейер: `python prepare_bundle.py` → `python build.py`.

---

