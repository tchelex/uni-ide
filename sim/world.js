/* ==========================================================================
 *  sim/world.js — арена симулятора: границы, линия на полу, препятствия.
 *  Датчики: rayDistance() (ультразвук) и floorAt() (датчик линии).
 *  Все координаты в миллиметрах, y вниз.
 * ========================================================================== */
(function (root) {
  "use strict";

  function seg(x1, y1, x2, y2) { return { x1, y1, x2, y2 }; }

  function World(opts) {
    opts = opts || {};
    this.w = opts.w || 2000;
    this.h = opts.h || 1400;
    this.start = opts.start || { x: 300, y: 700, th: 0 };
    this.obstacles = opts.obstacles || [];   // {type:'rect',x,y,w,h} | {type:'circle',x,y,r}
    this.line = opts.line || null;            // {pts:[{x,y}...], width, closed}
    this.name = opts.name || "arena";
    this._buildWalls();
  }

  World.prototype._buildWalls = function () {
    const w = this.w, h = this.h;
    this.walls = [seg(0, 0, w, 0), seg(w, 0, w, h), seg(w, h, 0, h), seg(0, h, 0, 0)];
    for (const o of this.obstacles) {
      if (o.type === "rect") {
        const { x, y, w: ow, h: oh } = o;
        this.walls.push(seg(x, y, x + ow, y), seg(x + ow, y, x + ow, y + oh),
          seg(x + ow, y + oh, x, y + oh), seg(x, y + oh, x, y));
      }
    }
  };

  // -------- датчик расстояния: луч из (x,y) в направлении ang (рад) --------
  // Возвращает мм до ближайшего препятствия/стены в пределах maxMM, иначе 0
  // (как ultraSonic: 0 = эха нет / открыто).
  World.prototype.rayDistance = function (x, y, ang, maxMM) {
    maxMM = maxMM || 2000;
    const dx = Math.cos(ang), dy = Math.sin(ang);
    let best = Infinity;
    for (const s of this.walls) {
      const t = raySeg(x, y, dx, dy, s);
      if (t != null && t < best) best = t;
    }
    for (const o of this.obstacles) {
      if (o.type === "circle") { const t = rayCircle(x, y, dx, dy, o.x, o.y, o.r); if (t != null && t < best) best = t; }
    }
    if (best === Infinity || best > maxMM) return 0;
    return Math.round(best);
  };

  // -------- датчик линии: «яркость» пола под точкой (0..4095) --------
  // На линии — тёмно (~300), на полу — светло (~3200).
  World.prototype.floorAt = function (x, y) {
    if (!this.line) return 3200;
    const half = (this.line.width || 30) / 2;
    const d = distToPolyline(x, y, this.line.pts, this.line.closed);
    return d <= half ? 300 : 3200;
  };

  // -------- столкновение: робот радиусом r пересёкся со стеной/кругом? --------
  World.prototype.hitsAt = function (x, y, r) {
    if (x - r < 0 || y - r < 0 || x + r > this.w || y + r > this.h) return true;
    for (const o of this.obstacles) {
      if (o.type === "circle") { if (Math.hypot(x - o.x, y - o.y) < r + o.r) return true; }
      else if (o.type === "rect") {
        const cx = Math.max(o.x, Math.min(x, o.x + o.w));
        const cy = Math.max(o.y, Math.min(y, o.y + o.h));
        if (Math.hypot(x - cx, y - cy) < r) return true;
      }
    }
    return false;
  };

  // ---------------- геометрия ----------------
  function raySeg(px, py, dx, dy, s) {       // t>0 вдоль луча или null
    const ex = s.x2 - s.x1, ey = s.y2 - s.y1;
    const den = dx * ey - dy * ex;
    if (Math.abs(den) < 1e-9) return null;
    const t = ((s.x1 - px) * ey - (s.y1 - py) * ex) / den;     // вдоль луча
    const u = ((s.x1 - px) * dy - (s.y1 - py) * dx) / den;     // вдоль отрезка
    if (t > 1e-6 && u >= 0 && u <= 1) return t;
    return null;
  }
  function rayCircle(px, py, dx, dy, cx, cy, r) {
    const fx = px - cx, fy = py - cy;
    const b = 2 * (fx * dx + fy * dy), c = fx * fx + fy * fy - r * r;
    const disc = b * b - 4 * c;
    if (disc < 0) return null;
    const t = (-b - Math.sqrt(disc)) / 2;
    return t > 1e-6 ? t : null;
  }
  function distToPolyline(x, y, pts, closed) {
    let best = Infinity;
    const n = pts.length;
    for (let i = 0; i < n - 1; i++) best = Math.min(best, distToSeg(x, y, pts[i], pts[i + 1]));
    if (closed && n > 1) best = Math.min(best, distToSeg(x, y, pts[n - 1], pts[0]));
    return best;
  }
  function distToSeg(px, py, a, b) {
    const vx = b.x - a.x, vy = b.y - a.y;
    const wx = px - a.x, wy = py - a.y;
    const len2 = vx * vx + vy * vy || 1;
    let t = (wx * vx + wy * vy) / len2; t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (a.x + t * vx), py - (a.y + t * vy));
  }

  // ---------------- готовые сценарии ----------------
  World.scenario = function (name) {
    if (name === "line") {
      // овальная трасса-линия
      const pts = [], cx = 1000, cy = 700, rx = 700, ry = 450;
      for (let i = 0; i < 48; i++) { const a = i / 48 * 2 * Math.PI; pts.push({ x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) }); }
      return new World({ name: "line", start: { x: 1000, y: 700 + 450, th: 0 }, line: { pts, width: 34, closed: true } });
    }
    if (name === "obstacles") {
      return new World({
        name: "obstacles", start: { x: 250, y: 700, th: 0 },
        obstacles: [
          { type: "rect", x: 900, y: 300, w: 200, h: 200 },
          { type: "rect", x: 1300, y: 800, w: 250, h: 200 },
          { type: "circle", x: 700, y: 950, r: 130 },
        ],
      });
    }
    return new World({ name: "arena", start: { x: 1000, y: 700, th: 0 } });   // пустая
  };

  root.SimWorld = World;
})(typeof window !== "undefined" ? window : globalThis);
