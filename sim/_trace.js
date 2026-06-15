/* Трассировщик скетча (Node) — видно, что и когда «делает» робот.
 *   node sim/_trace.js <путь к .ino>
 * По умолчанию — пример BasicMovement.  Графики ещё нет (этап 2),
 * но команды движения/датчиков и виртуальное время уже видны. */
import "./cpp.js";
import "./interp.js";
import fs from "fs";
const { SimCpp } = globalThis;

const file = process.argv[2] ||
  "arduino-user/libraries/UNI/examples/BasicMovement/BasicMovement.ino";
const CAP_MS = 12000;   // сколько виртуального времени трассируем
const MAX_LOOPS = 500;

const st = { clock: 0, movingUntil: 0 };
const fmt = (ms) => (ms / 1000).toFixed(2) + "с";
const out = [];
function rec(text) { out.push("[t=" + fmt(st.clock).padStart(7) + "]  " + text); }
const dur = (mm, p, k) => Math.max(1, Math.round(Math.abs(mm) / (Math.abs(p || 1) * k) * 1000));

const robot = {
  begin: () => rec("begin()"),
  displayPrint: (a, b) => rec("экран: " + a + (b === undefined ? "" : " = " + b)),
  displayClear: () => rec("экран: очистка"),
  blinkLED: (x) => rec("LED мигание " + x + " мс"),
  motors: (l, r) => rec("motors(" + l + ", " + r + ")"),
  motorsSync: (l, r) => rec("motorsSync(" + l + ", " + r + ")"),
  moveDist: function* (p, mm) { rec("moveDist(" + p + ", " + mm + " мм)"); yield { sleep: dur(mm, p, 3) }; },
  moveTime: function* (p, ms) { rec("moveTime(" + p + ", " + ms + " мс)"); yield { sleep: Math.abs(ms) }; },
  rotate: function* (p, a) { rec("rotate(" + p + ", " + a + "°)"); yield { sleep: dur(a, p, 2) }; },
  rotateTo: function* (p, a) { rec("rotateTo(" + p + ", " + a + "°)"); yield { sleep: 400 }; },
  moveTo: function* (p, x, y) { rec("moveTo(" + p + ", " + x + ", " + y + ")"); yield { sleep: 600 }; },
  moveArcRadius: function* (p, r, a) { rec("moveArcRadius(" + p + ", R" + r + ", " + a + "°)"); yield { sleep: 500 }; },
  moveArcDist: function* (p, a, mm) { rec("moveArcDist(" + p + ", " + a + ", " + mm + ")"); yield { sleep: 500 }; },
  moveDistAsync: (p, mm) => { rec("moveDistAsync(" + p + ", " + mm + ") [фон]"); st.movingUntil = st.clock + dur(mm, p, 3); },
  rotateAsync: (p, a) => { rec("rotateAsync(" + p + ", " + a + ") [фон]"); st.movingUntil = st.clock + dur(a, p, 2); },
  isMoving: () => (st.clock < st.movingUntil ? 1 : 0),
  waitMove: function* () { rec("waitMove…"); yield { wait: () => st.clock >= st.movingUntil }; return 1; },
  holdPosition: () => rec("hold"),
  resetDistance: () => rec("сброс дистанции"),
  resetAngle: () => rec("сброс угла"),
  getDistance: () => Math.round((st.clock) / 10),
  getAngle: () => 0,
  setPosition: (x, y, a) => rec("setPosition(" + x + ", " + y + ", " + a + ")"),
  setTuning: () => { },
  stop: () => { st.movingUntil = st.clock; rec("stop()"); },
};
const countScalars = (a) => Array.isArray(a) ? a.reduce((s, e) => s + countScalars(e), 0) : 1;
const module = {
  ultraSonic: () => 800, lineSensor: () => 2000, digitalSensor: () => 0, analogSensor: () => 0,
  getButtonState: () => 0, waitButton: () => { }, servo: (port, a) => rec("servo(" + port + ", " + a + "°)"),
  pixel: () => { }, pixelsAll: () => { }, pixelsClear: () => { }, pixelsShow: () => { },
  pixelsBrightness: () => { }, setTrafficLight: () => { },
};
const env = {
  delay: function* (ms) { yield { sleep: ms }; },
  delayMicroseconds: function* () { },
  millis: () => st.clock, micros: () => st.clock * 1000,
  sizeof: countScalars, map: (x, a, b, c, d) => Math.trunc((x - a) * (d - c) / (b - a) + c),
  constrain: (x, a, b) => Math.max(a, Math.min(b, x)), min: Math.min, max: Math.max, abs: Math.abs,
  random: (a, b) => b === undefined ? Math.floor(Math.random() * a) : a + Math.floor(Math.random() * (b - a)),
  HARD: 1, SOFT: 0, HIGH: 1, LOW: 0, OUTPUT: 1, INPUT: 0,
  P1: 12, P2: 13, P3: 14, P4: 15, P5: 17, P6: 16, P7: 32, P8: 23,
  Serial: { begin: () => { }, print: (x) => rec("Serial: " + x), println: (x) => rec("Serial: " + x) },
  pinMode: () => { }, digitalWrite: () => { }, digitalRead: () => 0, analogRead: () => 0, analogWrite: () => { },
};

function pump(gen) {
  let r = gen.next(), steps = 0;
  while (!r.done) {
    if (++steps > 1e6) { rec("⚠ слишком много операций — прервано"); return; }
    const q = r.value || {};
    if (q.sleep != null) st.clock += q.sleep;
    else if (typeof q.wait === "function") { let g = 0; while (!q.wait()) { st.clock += 10; if (++g > 1e5) break; } }
    if (st.clock > CAP_MS) { rec("… (лимит трассировки " + fmt(CAP_MS) + ")"); return "stop"; }
    r = gen.next();
  }
}

console.log("=== Трассировка: " + file + " ===\n");
let ast;
try { ast = SimCpp.parse(fs.readFileSync(file, "utf8")); }
catch (e) { console.log("Ошибка разбора: " + e.message); process.exit(1); }
const m = SimCpp.createMachine(ast, { classes: { UniBase: () => robot, UniDev: () => module }, env });
try {
  pump(m.runSetup());
  let n = 0;
  while (m.hasLoop() && st.clock <= CAP_MS && n++ < MAX_LOOPS) { if (pump(m.runLoop()) === "stop") break; }
} catch (e) { rec("Ошибка исполнения: " + e.message); }
console.log(out.join("\n") || "(команд не было)");
console.log("\nВсего событий: " + out.length + ", виртуальное время: " + fmt(st.clock));
