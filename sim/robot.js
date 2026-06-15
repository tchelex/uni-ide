/* ==========================================================================
 *  sim/robot.js — модель дифференциального привода UniBase.
 *
 *  Числа из реальной библиотеки (UniBase.h):
 *    колея 106 мм, колесо Ø45 мм, 690 тиков/об, макс 1500 тиков/с
 *    → макс. линейная скорость ≈ 307 мм/с при «мощности» 100.
 *
 *  Система координат: x вправо, y ВНИЗ (как на холсте), угол th — от оси +x,
 *  по часовой стрелке (положительный угол = поворот направо, как в UNI).
 * ========================================================================== */
(function (root) {
  "use strict";

  const TRACK_MM = 106;                 // расстояние между колёсами (L)
  const WHEEL_D = 45;                    // диаметр колеса, мм
  const TICKS_REV = 690;
  const MAX_TPS = 1500;
  const DIST_PER_TICK = (Math.PI * WHEEL_D) / TICKS_REV;     // ≈0.2049 мм/тик
  const MAX_SPEED = MAX_TPS * DIST_PER_TICK;                 // ≈307 мм/с

  function Robot() { this.reset(0, 0, 0); }

  Robot.TRACK_MM = TRACK_MM;
  Robot.WHEEL_D = WHEEL_D;
  Robot.MAX_SPEED = MAX_SPEED;

  Robot.prototype.reset = function (x, y, thDeg) {
    this.x = x || 0; this.y = y || 0;
    this.th = (thDeg || 0) * Math.PI / 180;   // рад
    this.vL = 0; this.vR = 0;                  // целевые скорости колёс, мм/с
    this.dist = 0;                             // путь с момента resetDistance, мм
    this.ang = 0;                              // изменение курса с resetAngle, град
    this.lTicks = 0; this.rTicks = 0;          // «энкодеры»
    this.collided = false;
    this.task = null;                          // активная команда движения
  };

  // power -100..100 → мм/с
  Robot.prototype.powerToSpeed = function (p) {
    return Math.max(-100, Math.min(100, p)) / 100 * MAX_SPEED;
  };
  Robot.prototype.setMotors = function (pl, pr) {
    this.vL = this.powerToSpeed(pl); this.vR = this.powerToSpeed(pr);
  };
  Robot.prototype.setSpeed = function (sL, sR) { this.vL = sL; this.vR = sR; };
  Robot.prototype.stop = function () { this.vL = 0; this.vR = 0; };

  // интеграция на dt секунд (модель «уницикл»)
  Robot.prototype.step = function (dt) {
    const v = (this.vL + this.vR) / 2;                 // мм/с
    const omega = (this.vR - this.vL) / TRACK_MM;      // рад/с (CW+ при y вниз)
    const ds = v * dt;
    const dth = omega * dt;
    // тики энкодеров
    this.lTicks += (this.vL * dt) / DIST_PER_TICK;
    this.rTicks += (this.vR * dt) / DIST_PER_TICK;
    // одометрия
    this.dist += Math.abs(ds);
    this.ang += dth * 180 / Math.PI;
    // поза
    this.x += ds * Math.cos(this.th);
    this.y += ds * Math.sin(this.th);
    this.th += dth;
    // авто-остановка по завершении команды движения (moveDist/rotate/async)
    if (this.task && this.task.done(this)) { this.stop(); this.task = null; }
  };

  Robot.prototype.setTask = function (doneFn) { this.task = { done: doneFn }; };
  Robot.prototype.isMoving = function () { return this.task ? 1 : 0; };

  Robot.prototype.headingDeg = function () { return this.th * 180 / Math.PI; };
  Robot.prototype.forward = function () { return { x: Math.cos(this.th), y: Math.sin(this.th) }; };

  // мировые координаты точки, заданной в локальной системе робота
  // (lx — вперёд, ly — вправо)
  Robot.prototype.localToWorld = function (lx, ly) {
    const c = Math.cos(this.th), s = Math.sin(this.th);
    return { x: this.x + lx * c - ly * s, y: this.y + lx * s + ly * c };
  };

  root.SimRobot = Robot;
})(typeof window !== "undefined" ? window : globalThis);
