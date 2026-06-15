/* Headless-тест интерпретатора (Node).  Запуск:  node sim/_test.js
 * Проверяет, что реальные примеры UNI парсятся и исполняются кооперативно. */
import "./cpp.js";
import "./interp.js";
const { SimCpp } = globalThis;

// -------- стенд: виртуальные часы + стаб UNI, записывающий команды --------
function makeHost() {
  const log = [];
  const st = { clock: 0, movingUntil: 0 };
  const dur = (mm, power, k) => Math.max(1, Math.round(Math.abs(mm) / (Math.abs(power || 1) * k) * 1000));

  function blockMove(name, durMs, rec) {
    return function* () { log.push(rec); yield { sleep: durMs }; };
  }
  const robot = {
    begin: () => log.push(["begin"]),
    displayPrint: (a, b) => log.push(b === undefined ? ["disp", a] : ["disp", a, b]),
    displayClear: () => log.push(["dispClear"]),
    blinkLED: (x) => log.push(["blink", x]),
    motors: (l, r) => log.push(["motors", l, r]),
    moveDist: function* (p, mm) { log.push(["moveDist", p, mm]); yield { sleep: dur(mm, p, 3) }; },
    moveTime: function* (p, ms) { log.push(["moveTime", p, ms]); yield { sleep: Math.abs(ms) }; },
    rotate: function* (p, a) { log.push(["rotate", p, a]); yield { sleep: dur(a, p, 2) }; },
    moveArcRadius: function* (p, r, a) { log.push(["arcR", p, r, a]); yield { sleep: 300 }; },
    moveArcDist: function* (p, a, mm) { log.push(["arcD", p, a, mm]); yield { sleep: 300 }; },
    moveDistAsync: (p, mm) => { log.push(["moveDistAsync", p, mm]); st.movingUntil = st.clock + dur(mm, p, 3); },
    rotateAsync: (p, a) => { log.push(["rotateAsync", p, a]); st.movingUntil = st.clock + dur(a, p, 2); },
    isMoving: () => (st.clock < st.movingUntil ? 1 : 0),
    waitMove: function* (timeout) {
      log.push(["waitMove", timeout || 0]);
      yield { wait: () => st.clock >= st.movingUntil };
      return 1;
    },
    getDistance: () => 123,
    stop: (t) => { st.movingUntil = st.clock; log.push(["stop", t]); },
  };
  robot.setPosition = (x, y, a) => log.push(["setPos", x, y, a]);
  robot.moveTo = function* (p, x, y) { log.push(["moveTo", p, x, y]); yield { sleep: 200 }; };
  robot.rotateTo = function* (p, a) { log.push(["rotateTo", p, a]); yield { sleep: 100 }; };
  robot.resetDistance = () => log.push(["resetDist"]);
  robot.holdPosition = () => log.push(["hold"]);
  const countScalars = (a) => Array.isArray(a) ? a.reduce((s, e) => s + countScalars(e), 0) : 1;
  const module = { ultraSonic: () => 999, lineSensor: () => 2000, getButtonState: () => 0 };
  const env = {
    delay: function* (ms) { yield { sleep: ms }; },
    millis: () => st.clock,
    sizeof: countScalars,
    HARD: 1, SOFT: 0, P1: 12, P2: 13, P3: 14, P4: 15, P5: 17, P6: 16, P7: 32, P8: 23,
    Serial: { begin: () => { }, print: (x) => log.push(["Sprint", x]), println: (x) => log.push(["Sprintln", x]) },
  };
  return { st, log, classes: { UniBase: () => robot, UniDev: () => module }, env };
}

// -------- драйвер-«насос»: прокручивает виртуальное время --------
function pump(gen, st, label) {
  let steps = 0;
  let r = gen.next();
  while (!r.done) {
    if (++steps > 200000) throw new Error(label + ": слишком много шагов (возможно, зависание)");
    const req = r.value || {};
    if (req.sleep != null) st.clock += req.sleep;
    else if (typeof req.wait === "function") {
      let guard = 0;
      while (!req.wait()) { st.clock += 10; if (++guard > 100000) throw new Error(label + ": wait не завершился"); }
    }
    // {breathe} — просто продолжаем
    r = gen.next();
  }
}

// -------- сами тесты --------
function run(name, source, check) {
  const h = makeHost();
  let ast;
  try { ast = SimCpp.parse(source); }
  catch (e) { console.log("FAIL  " + name + "  — ошибка парсинга: " + e.message); return false; }
  const m = SimCpp.createMachine(ast, { classes: h.classes, env: h.env });
  try {
    pump(m.runSetup(), h.st, name + ":setup");
  } catch (e) { console.log("FAIL  " + name + "  — ошибка исполнения: " + e.message); return false; }
  const res = check(h.log, h.st);
  console.log((res === true ? "PASS  " : "FAIL  ") + name + (res === true ? "" : "  — " + res));
  return res === true;
}

const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);
let ok = true;

// 1) BasicMovement — квадрат 4×(moveDist+rotate) + назад + по времени
ok &= run("BasicMovement", `
#include <UNI.h>
UniBase robot("UNI");
void setup() {
  robot.begin();
  for (int i = 0; i < 4; i++) {
    robot.moveDist(50, 300);
    robot.rotate(50, 90);
  }
  robot.moveDist(-50, 200);
  robot.moveTime(40, 1000);
  robot.moveTime(-40, 1000);
}
void loop() {}
`, (log) => {
  const moves = log.filter(e => ["moveDist", "rotate", "moveTime"].includes(e[0]));
  if (moves.length !== 11) return "ожидалось 11 команд движения, получено " + moves.length;
  if (!eq(moves[0], ["moveDist", 50, 300])) return "первая команда неверна: " + JSON.stringify(moves[0]);
  if (!eq(moves[1], ["rotate", 50, 90])) return "вторая команда неверна";
  if (!eq(moves[8], ["moveDist", -50, 200])) return "движение назад неверно: " + JSON.stringify(moves[8]);
  if (!eq(moves[10], ["moveTime", -40, 1000])) return "последняя команда неверна";
  return true;
});

// 2) AsyncMovement — isMoving/waitMove/while + stop по таймеру
ok &= run("AsyncMovement", `
#include <UNI.h>
UniBase robot("UNI");
void setup() {
  robot.begin();
  robot.moveDistAsync(50, 800);
  while (robot.isMoving()) {
    robot.displayPrint("Dist", robot.getDistance());
    delay(100);
  }
  robot.rotateAsync(50, 180);
  robot.waitMove();
  robot.moveDistAsync(30, 2000);
  delay(1500);
  robot.stop(1);
}
void loop() {}
`, (log) => {
  const names = log.map(e => e[0]);
  if (!names.includes("moveDistAsync")) return "нет moveDistAsync";
  if (!names.includes("waitMove")) return "нет waitMove";
  if (!names.includes("stop")) return "нет stop";
  // while(isMoving) должен был хоть раз вывести Dist на экран
  if (!log.some(e => e[0] === "disp" && e[1] === "Dist")) return "цикл while(isMoving) не отработал";
  // stop должен идти после waitMove (порядок команд сохранён)
  if (names.indexOf("stop") < names.indexOf("waitMove")) return "порядок команд нарушен";
  return true;
});

// 3) Выражения и управление: for/if/арифметика/массив
ok &= run("Expressions", `
int data[3] = {10, 20, 30};
int sum = 0;
UniBase robot;
void setup() {
  for (int i = 0; i < 3; i++) sum += data[i];
  int half = sum / 2;          // целочисленное деление: 60/2 = 30
  if (half == 30 && sum > 50) robot.motors(half, -half);
  else robot.motors(0, 0);
}
void loop() {}
`, (log) => {
  if (!log.some(e => eq(e, ["motors", 30, -30]))) return "ожидался motors(30,-30), лог: " + JSON.stringify(log);
  return true;
});

// 4) DriveToPoint — 2D-массив + sizeof + индексация + moveTo по точкам
ok &= run("DriveToPoint", `
#include <UNI.h>
UniBase robot("UNI");
const float route[][2] = {
  {400, 0}, {400, 400}, {0, 400}, {0, 0},
};
const int routeLen = sizeof(route) / sizeof(route[0]);
void setup() {
  robot.setPosition(0, 0, 0);
  for (int i = 0; i < routeLen; i++) {
    robot.moveTo(50, route[i][0], route[i][1]);
  }
}
void loop() {}
`, (log) => {
  const moves = log.filter(e => e[0] === "moveTo");
  if (moves.length !== 4) return "routeLen/sizeof неверно: ожидалось 4 moveTo, получено " + moves.length;
  if (!eq(moves[0], ["moveTo", 50, 400, 0])) return "первая точка неверна: " + JSON.stringify(moves[0]);
  if (!eq(moves[2], ["moveTo", 50, 0, 400])) return "третья точка неверна: " + JSON.stringify(moves[2]);
  return true;
});

// 5) smoke-тест парсера по ВСЕМ примерам UNI
import fs from "fs";
import path from "path";
const exDir = path.resolve("arduino-user/libraries/UNI/examples");
if (fs.existsSync(exDir)) {
  console.log("\n--- парсинг всех примеров UNI ---");
  let allParse = true;
  for (const d of fs.readdirSync(exDir)) {
    const ino = path.join(exDir, d, d + ".ino");
    if (!fs.existsSync(ino)) continue;
    try { SimCpp.parse(fs.readFileSync(ino, "utf8")); console.log("  ok    " + d); }
    catch (e) { allParse = false; console.log("  FAIL  " + d + "  — " + e.message); }
  }
  ok &= allParse;
}

console.log(ok ? "\n=== ВСЕ ТЕСТЫ ПРОЙДЕНЫ ===" : "\n=== ЕСТЬ ПРОВАЛЫ ===");
process.exit(ok ? 0 : 1);
