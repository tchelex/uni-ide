/* ==========================================================================
 *  sim/render.js — отрисовка арены и робота (Canvas2D).
 *  Мир в мм (y вниз) → экран с авто-вписыванием и отступами.
 * ========================================================================== */
(function (root) {
  "use strict";
  const R = root.SimRobot;

  function view(canvas, world) {
    const pad = 16;
    const sx = (canvas.width - pad * 2) / world.w;
    const sy = (canvas.height - pad * 2) / world.h;
    const s = Math.min(sx, sy);
    const ox = (canvas.width - world.w * s) / 2;
    const oy = (canvas.height - world.h * s) / 2;
    return { s, ox, oy, X: (x) => ox + x * s, Y: (y) => oy + y * s };
  }

  function draw(ctx, canvas, world, robot, opts) {
    opts = opts || {};
    const v = view(canvas, world);
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // фон вне арены
    ctx.fillStyle = "#0f1620"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    // пол арены
    ctx.fillStyle = "#f3efe6";
    ctx.fillRect(v.X(0), v.Y(0), world.w * v.s, world.h * v.s);

    // сетка 200 мм
    ctx.strokeStyle = "rgba(0,0,0,0.06)"; ctx.lineWidth = 1;
    for (let gx = 0; gx <= world.w; gx += 200) { ctx.beginPath(); ctx.moveTo(v.X(gx), v.Y(0)); ctx.lineTo(v.X(gx), v.Y(world.h)); ctx.stroke(); }
    for (let gy = 0; gy <= world.h; gy += 200) { ctx.beginPath(); ctx.moveTo(v.X(0), v.Y(gy)); ctx.lineTo(v.X(world.w), v.Y(gy)); ctx.stroke(); }

    // линия на полу
    if (world.line) {
      ctx.strokeStyle = "#1a1a1a"; ctx.lineWidth = (world.line.width || 30) * v.s;
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.beginPath();
      world.line.pts.forEach((p, i) => i ? ctx.lineTo(v.X(p.x), v.Y(p.y)) : ctx.moveTo(v.X(p.x), v.Y(p.y)));
      if (world.line.closed) ctx.closePath();
      ctx.stroke();
    }

    // препятствия
    ctx.fillStyle = "#9aa4ad"; ctx.strokeStyle = "#5b6671"; ctx.lineWidth = 1.5;
    for (const o of world.obstacles) {
      ctx.beginPath();
      if (o.type === "rect") ctx.rect(v.X(o.x), v.Y(o.y), o.w * v.s, o.h * v.s);
      else if (o.type === "circle") ctx.arc(v.X(o.x), v.Y(o.y), o.r * v.s, 0, 7);
      ctx.fill(); ctx.stroke();
    }

    // рамка арены
    ctx.strokeStyle = "#3a4650"; ctx.lineWidth = 2;
    ctx.strokeRect(v.X(0), v.Y(0), world.w * v.s, world.h * v.s);

    // лучи датчиков (если переданы)
    if (opts.rays) for (const r of opts.rays) {
      ctx.strokeStyle = "rgba(0,135,138,0.5)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(v.X(r.x0), v.Y(r.y0)); ctx.lineTo(v.X(r.x1), v.Y(r.y1)); ctx.stroke();
      ctx.fillStyle = "#00878a"; ctx.beginPath(); ctx.arc(v.X(r.x1), v.Y(r.y1), 3, 0, 7); ctx.fill();
    }

    drawRobot(ctx, v, robot);

    // след (трасса центра)
    if (opts.trail && opts.trail.length > 1) {
      ctx.strokeStyle = "rgba(0,135,138,0.45)"; ctx.lineWidth = 1.5;
      ctx.beginPath(); opts.trail.forEach((p, i) => i ? ctx.lineTo(v.X(p.x), v.Y(p.y)) : ctx.moveTo(v.X(p.x), v.Y(p.y))); ctx.stroke();
    }
  }

  function drawRobot(ctx, v, robot) {
    const X = v.X, Y = v.Y, s = v.s;
    ctx.save();
    ctx.translate(X(robot.x), Y(robot.y));
    ctx.rotate(robot.th);                 // локальная: +x вперёд, +y вправо
    const mm = (px) => px * s;
    // колёса
    ctx.fillStyle = "#222";
    rr(ctx, mm(-22), mm(R.TRACK_MM / 2 - 9), mm(44), mm(18), mm(4));      // правое
    rr(ctx, mm(-22), mm(-R.TRACK_MM / 2 - 9), mm(44), mm(18), mm(4));     // левое
    // опоры (ролики)
    ctx.fillStyle = "#888";
    dot(ctx, mm(62), 0, mm(7)); dot(ctx, mm(-62), 0, mm(7));
    // корпус
    ctx.fillStyle = robot.collided ? "#c0392b" : "#00878a";
    ctx.strokeStyle = "#00585a"; ctx.lineWidth = 1.5;
    rr(ctx, mm(-72), mm(-60), mm(150), mm(120), mm(14), true);
    // нос (направление)
    ctx.fillStyle = "#fff";
    ctx.beginPath(); ctx.moveTo(mm(78), 0); ctx.lineTo(mm(50), mm(-22)); ctx.lineTo(mm(50), mm(22)); ctx.closePath(); ctx.fill();
    ctx.restore();
  }

  function rr(ctx, x, y, w, h, r, stroke) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath(); ctx.fill(); if (stroke) ctx.stroke();
  }
  function dot(ctx, x, y, r) { ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); }

  root.SimRender = { draw, view };
})(typeof window !== "undefined" ? window : globalThis);
