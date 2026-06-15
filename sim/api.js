/* ==========================================================================
 *  sim/api.js — биндинги UNI (UniBase robot / UniDev module + builtins)
 *  к модели робота и мира. Блокирующие движения — генераторы, делающие
 *  yield {sleep}/{wait}; драйвер прокручивает физику до завершения.
 *
 *  SimApi.build(robot, world, ctx) → { classes, env } для SimCpp.createMachine.
 *  ctx = { clock: ()=>ms, serial: (txt)=>{}, display: (txt)=>{} }
 * ========================================================================== */
(function (root) {
  "use strict";
  const Robot = root.SimRobot;
  const L = Robot.TRACK_MM;

  function build(robot, world, ctx) {
    const clk = ctx.clock || (() => 0);
    const serial = ctx.serial || (() => { });
    const display = ctx.display || (() => { });

    const untilTask = { wait: () => !robot.task };

    function armDist(power, mm) {
      const base = robot.dist, t = Math.abs(mm);
      robot.setMotors(power, power);
      robot.setTask(() => robot.dist - base >= t);
    }
    function armRotate(power, deg) {
      const p = Math.abs(power), dir = deg >= 0 ? 1 : -1, base = robot.ang, t = Math.abs(deg);
      robot.setMotors(-dir * p, dir * p);          // dir>0 → th растёт → поворот вправо
      robot.setTask(() => Math.abs(robot.ang - base) >= t);
    }
    function* rotateToRad(power, thTarget) {
      let d = thTarget - robot.th;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      armRotate(power, d * 180 / Math.PI);
      yield untilTask;
    }
    function armArcRadius(power, R, angleDeg) {
      const v = robot.powerToSpeed(Math.abs(power));
      const omega = (angleDeg >= 0 ? 1 : -1) * Math.abs(v) / Math.max(1, R);
      robot.setSpeed(v - omega * L / 2, v + omega * L / 2);
      const base = robot.ang, t = Math.abs(angleDeg);
      robot.setTask(() => Math.abs(robot.ang - base) >= t);
    }
    function armArcDist(power, angleDeg, mm) {
      const v = robot.powerToSpeed(Math.abs(power)), dist = Math.abs(mm);
      const time = dist / Math.max(1, Math.abs(v));
      const omega = (angleDeg * Math.PI / 180) / Math.max(0.001, time);
      robot.setSpeed(v - omega * L / 2, v + omega * L / 2);
      const base = robot.dist;
      robot.setTask(() => robot.dist - base >= dist);
    }

    const UniBase = function () {
      return {
        begin: () => { },
        setTuning: () => { }, getTuning: () => ({}), UniBaseControl: () => { },
        // прямое управление
        motors: (l, r) => { robot.setMotors(l, r); robot.task = null; },
        motorsSync: (l, r) => { robot.setMotors(l, r); robot.task = null; },
        motorLeft: (p) => { robot.vL = robot.powerToSpeed(p); },
        motorRight: (p) => { robot.vR = robot.powerToSpeed(p); },
        // блокирующие
        moveDist: function* (p, mm) { armDist(p, mm); yield untilTask; },
        moveTime: function* (p, ms) { robot.setMotors(p, p); yield { sleep: Math.abs(ms) }; robot.stop(); },
        rotate: function* (p, a) { armRotate(p, a); yield untilTask; },
        rotateTo: function* (p, a) { yield* rotateToRad(p, a * Math.PI / 180); },
        moveTo: function* (p, x, y) {
          yield* rotateToRad(p, Math.atan2(y - robot.y, x - robot.x));
          armDist(p, Math.hypot(x - robot.x, y - robot.y)); yield untilTask;
        },
        moveArcRadius: function* (p, r, a) { armArcRadius(p, r, a); yield untilTask; },
        moveArcDist: function* (p, a, mm) { armArcDist(p, a, mm); yield untilTask; },
        // асинхронные
        moveDistAsync: (p, mm) => armDist(p, mm),
        rotateAsync: (p, a) => armRotate(p, a),
        moveArcRadiusAsync: (p, r, a) => armArcRadius(p, r, a),
        moveArcDistAsync: (p, a, mm) => armArcDist(p, a, mm),
        rotateToAsync: (p, a) => { armRotate(p, shortestDeg(a)); },
        moveTimeAsync: (p, ms) => { const end = clk() + Math.abs(ms); robot.setMotors(p, p); robot.setTask(() => clk() >= end); },
        isMoving: () => robot.isMoving(),
        waitMove: function* (timeout) { yield { wait: () => !robot.task, maxMs: timeout || 0 }; return robot.task ? 0 : 1; },
        holdPosition: () => { robot.stop(); robot.task = null; },
        // стоп
        stop: () => { robot.stop(); robot.task = null; },
        stopLeft: () => { robot.vL = 0; }, stopRight: () => { robot.vR = 0; },
        // одометрия
        resetDistance: () => { robot.dist = 0; }, getDistance: () => robot.dist,
        resetAngle: () => { robot.ang = 0; }, getAngle: () => robot.ang,
        getOdometry: () => ({ x: robot.x, y: robot.y, angle: robot.headingDeg() }),
        setPosition: (x, y, a) => { robot.x = x; robot.y = y; robot.th = a * Math.PI / 180; },
        getAbsX: () => robot.x, getAbsY: () => robot.y, getAbsAngle: () => robot.headingDeg(),
        getLeftTicks: () => Math.round(robot.lTicks), getRightTicks: () => Math.round(robot.rTicks),
        printOdometry: () => serial(`x=${robot.x.toFixed(0)} y=${robot.y.toFixed(0)} a=${robot.headingDeg().toFixed(1)}\n`),
        // периферия
        blinkLED: () => { }, getBatteryPower: () => 100,
        displayPrint: (a, b) => display(b === undefined ? String(a) : a + ": " + b),
        displayClear: () => display(""),
      };
      function shortestDeg(a) { let d = a - robot.headingDeg(); while (d > 180) d -= 360; while (d < -180) d += 360; return d; }
    };

    const lineOff = { 12: -45, 13: 0, 14: 45, 15: 0 };   // боковое смещение датчика линии по порту
    const UniDev = function () {
      return {
        begin: () => { },
        ultraSonic: (trig) => {
          let lx = 75, ly = 0, da = 0;
          if (trig === 14) { lx = 0; ly = 60; da = Math.PI / 2; }   // боковой (P3) — вправо
          const p = robot.localToWorld(lx, ly);
          return world.rayDistance(p.x, p.y, robot.th + da, 2000);
        },
        lineSensor: (port) => { const off = (port in lineOff) ? lineOff[port] : 0; const p = robot.localToWorld(70, off); return world.floorAt(p.x, p.y); },
        digitalSensor: () => 0, analogSensor: () => 0, getPinMode: () => -1,
        getButtonState: () => 0, waitButton: function* () { yield { sleep: 0 }; },
        servo: () => { }, servoAttach: () => { }, servoDetach: () => { },
        pixel: () => { }, pixelsAll: () => { }, pixelsClear: () => { }, pixelsShow: () => { },
        pixelsBrightness: () => { }, pixelsRainbow: function* (s, d) { yield { sleep: d || 0 }; },
        pixelsRunning: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        pixelsBreathing: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        pixelsFill: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        pixelsSparkle: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        pixelsRotating: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        pixelsSpinner: function* (r, g, b, d) { yield { sleep: d || 0 }; },
        setTrafficLight: () => { }, trafficLightSequence: function* () { yield { sleep: 0 }; },
      };
    };

    const countScalars = (a) => Array.isArray(a) ? a.reduce((s, e) => s + countScalars(e), 0) : 1;
    const env = {
      delay: function* (ms) { yield { sleep: Math.max(0, ms) }; },
      delayMicroseconds: function* () { },
      millis: () => Math.round(clk()), micros: () => Math.round(clk() * 1000),
      sizeof: countScalars,
      map: (x, a, b, c, d) => (b - a) === 0 ? c : Math.trunc((x - a) * (d - c) / (b - a) + c),
      constrain: (x, a, b) => Math.max(a, Math.min(b, x)),
      min: (a, b) => Math.min(a, b), max: (a, b) => Math.max(a, b), abs: (x) => Math.abs(x),
      sqrt: Math.sqrt, pow: Math.pow, sin: Math.sin, cos: Math.cos, tan: Math.tan,
      floor: Math.floor, ceil: Math.ceil, round: Math.round,
      random: (a, b) => b === undefined ? Math.floor(Math.random() * a) : a + Math.floor(Math.random() * (b - a)),
      randomSeed: () => { },
      pinMode: () => { }, digitalWrite: () => { }, digitalRead: () => 0, analogRead: () => 0, analogWrite: () => { },
      Serial: { begin: () => { }, print: (x) => serial(String(x)), println: (x) => serial(String(x) + "\n"), printf: () => { } },
      // константы платы/UNI
      HIGH: 1, LOW: 0, INPUT: 0, OUTPUT: 1, INPUT_PULLUP: 2, INPUT_PULLDOWN: 3,
      HARD: 1, SOFT: 0, LED_BUILTIN: 2, PI: Math.PI,
      P1: 12, P2: 13, P3: 14, P4: 15, P5: 17, P6: 16, P7: 32, P8: 23,
      TRAFFIC_OFF: 0, TRAFFIC_RED: 1, TRAFFIC_YELLOW: 2, TRAFFIC_GREEN: 3,
    };

    return { classes: { UniBase, UniDev }, env };
  }

  root.SimApi = { build };
})(typeof window !== "undefined" ? window : globalThis);
