ДРАЙВЕР USB-UART ДЛЯ ПЛАТЫ ESP32 (папка drivers)

На платах ESP32 Dev Module для связи по USB стоит чип CH340/CH341.

drivers/CH341SER/   — INF-пакет драйвера (Win10/11). Установщик UNI IDE
                      ставит его ТИХО через командную строку:
                        pnputil /add-driver CH341SER.INF /install
                      (галочка на последнем шаге установки; появится
                      только окно UAC — подтвердить права администратора).

drivers/CH341SER.EXE — запасной вариант: официальный GUI-инсталлятор WCH,
                      если по какой-то причине pnputil недоступен.
                      Источник: https://www.wch-ic.com/downloads/CH341SER_EXE.html

Если на вашей плате другой чип (CP2102 / CP210x, Silicon Labs):
  скачайте «CP210x Universal Windows Driver» и поставьте отдельно —
  https://www.silabs.com/developer-tools/usb-to-uart-bridge-vcp-drivers

Драйвер ставится с правами администратора и только один раз на каждом ПК.
