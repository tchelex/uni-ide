/* ==========================================================================
 *  sim/interp.js — tree-walking исполнитель AST из cpp.js (генераторы).
 *
 *  Машина исполняет глобальные объявления, затем драйвер вызывает runSetup()
 *  один раз и runLoop() по кругу. delay()/блокирующие host-методы делают yield
 *  запросов драйверу: {sleep:ms} | {wait:fn} | {breathe:true}.
 *
 *  Использование:
 *    const ast = SimCpp.parse(source);
 *    const m = SimCpp.createMachine(ast, { classes, env });
 *    for (const req of m.runSetup()) handle(req);   // драйвер прокручивает время
 * ========================================================================== */
(function (root) {
  "use strict";
  const { CppError } = root.SimCpp;

  const isGen = (g) => g && typeof g.next === "function" && typeof g.throw === "function";

  // целочисленное деление как в C++, если оба операнда целые
  function divide(a, b) {
    if (b === 0) throw CppError("деление на ноль");
    if (Number.isInteger(a) && Number.isInteger(b)) return Math.trunc(a / b);
    return a / b;
  }

  function Env(parent) { this.vars = new Map(); this.parent = parent || null; }
  Env.prototype.has = function (n) { return this.vars.has(n) || (this.parent && this.parent.has(n)); };
  Env.prototype.get = function (n) {
    let e = this; while (e) { if (e.vars.has(n)) return e.vars.get(n); e = e.parent; }
    throw CppError("'" + n + "' не объявлено");
  };
  Env.prototype.set = function (n, v) {
    let e = this; while (e) { if (e.vars.has(n)) { e.vars.set(n, v); return; } e = e.parent; }
    throw CppError("'" + n + "' не объявлено");
  };
  Env.prototype.def = function (n, v) { this.vars.set(n, v); };

  function createMachine(ast, opts) {
    opts = opts || {};
    const global = new Env(null);
    const classes = opts.classes || {};
    const funcs = new Map();
    let ops = 0;                       // счётчик операций (бюджет против зависания)
    const BUDGET = 4000;

    // seed: константы, builtin-функции, host-объекты (Serial и т.п.)
    const seed = opts.env || {};
    for (const k in seed) global.def(k, seed[k]);
    // #define-константы
    const defs = ast.defines || {};
    for (const k in defs) {
      if (global.has(k)) continue;
      const num = Number(defs[k]);
      global.def(k, Number.isNaN(num) ? defs[k] : num);
    }

    // регистрируем функции
    for (const d of ast.decls) if (d.type === "Func") funcs.set(d.name, d);

    function* budget() { if (++ops >= BUDGET) { ops = 0; yield { breathe: true }; } }

    // ---- выполнить глобальные объявления (конструкторы объектов и т.п.)
    function* initGlobals() {
      for (const d of ast.decls) if (d.type === "VarDecl") yield* execVarDecl(d, global);
    }

    function* execVarDecl(node, env) {
      for (const dec of node.decls) {
        let val = 0;
        if (dec.ctorArgs) {                       // UniBase robot("UNI")
          const argv = [];
          for (const a of dec.ctorArgs) argv.push(yield* evalExpr(a, env));
          const cls = classes[dec.ctype.split(" ").pop()];
          val = cls ? cls.apply(null, argv) : {};
        } else if (dec.isArray) {
          if (dec.init) val = yield* evalExpr(dec.init, env);          // {…} (в т.ч. вложенные)
          else { const sz = dec.dims[0] ? (yield* evalExpr(dec.dims[0], env)) : 0; val = new Array(sz).fill(0); }
        } else if (dec.init) {
          val = yield* evalExpr(dec.init, env);
        } else if (classes[dec.ctype.split(" ").pop()]) {
          val = classes[dec.ctype.split(" ").pop()]();   // UniDev module;
        }
        env.def(dec.name, val);
      }
    }

    // ---- statements → сигнал {k, v}
    const NORMAL = { k: "normal" };
    function* execBlock(node, env) {
      const local = new Env(env);
      for (const s of node.body) { const sig = yield* execStmt(s, local); if (sig.k !== "normal") return sig; }
      return NORMAL;
    }
    function* execStmt(node, env) {
      switch (node.type) {
        case "Block": return yield* execBlock(node, env);
        case "Empty": return NORMAL;
        case "VarDecl": yield* execVarDecl(node, env); return NORMAL;
        case "ExprStmt": yield* evalExpr(node.expr, env); yield* budget(); return NORMAL;
        case "If": {
          if (truthy(yield* evalExpr(node.cond, env))) return yield* execStmt(node.then, env);
          else if (node.els) return yield* execStmt(node.els, env);
          return NORMAL;
        }
        case "While": {
          while (truthy(yield* evalExpr(node.cond, env))) {
            const sig = yield* execStmt(node.body, env);
            if (sig.k === "break") break;
            if (sig.k === "return") return sig;
            yield* budget();
          }
          return NORMAL;
        }
        case "DoWhile": {
          do {
            const sig = yield* execStmt(node.body, env);
            if (sig.k === "break") break;
            if (sig.k === "return") return sig;
            yield* budget();
          } while (truthy(yield* evalExpr(node.cond, env)));
          return NORMAL;
        }
        case "For": {
          const local = new Env(env);
          if (node.init) yield* execStmt(node.init, local);
          while (node.cond ? truthy(yield* evalExpr(node.cond, local)) : true) {
            const sig = yield* execStmt(node.body, local);
            if (sig.k === "break") break;
            if (sig.k === "return") return sig;
            if (node.upd) yield* evalExpr(node.upd, local);
            yield* budget();
          }
          return NORMAL;
        }
        case "Return": return { k: "return", v: node.value ? (yield* evalExpr(node.value, env)) : undefined };
        case "Break": return { k: "break" };
        case "Continue": return { k: "continue" };
        default: throw CppError("неизвестный оператор " + node.type, node.line);
      }
    }

    // ---- expressions → значение (генератор)
    function* evalExpr(node, env) {
      switch (node.type) {
        case "Num": return node.value;
        case "Str": return node.value;
        case "ArrayLit": { const arr = []; for (const it of node.items) arr.push(yield* evalExpr(it, env)); return arr; }
        case "Cast": {
          const v = yield* evalExpr(node.arg, env); const t = node.ctype;
          if (/bool/.test(t)) return truthy(v) ? 1 : 0;
          if (/float|double/.test(t)) return Number(v);
          if (/int|long|short|byte|char|word|size/.test(t)) return Math.trunc(Number(v));
          return v;
        }
        case "Ident": return env.get(node.name);
        case "Member": {
          const obj = yield* evalExpr(node.obj, env);
          if (obj == null) throw CppError("обращение к члену '" + node.name + "' у пустого значения", node.line);
          return obj[node.name];
        }
        case "Index": {
          const arr = yield* evalExpr(node.obj, env);
          const i = yield* evalExpr(node.index, env);
          return arr ? arr[i] : undefined;
        }
        case "Assign": return yield* evalAssign(node, env);
        case "Update": return yield* evalUpdate(node, env);
        case "Unary": {
          const v = yield* evalExpr(node.arg, env);
          if (node.op === "!") return truthy(v) ? 0 : 1;
          if (node.op === "-") return -v;
          if (node.op === "+") return +v;
          if (node.op === "~") return ~v;
          return v;
        }
        case "Binary": return yield* evalBinary(node, env);
        case "Ternary": return truthy(yield* evalExpr(node.cond, env)) ? (yield* evalExpr(node.a, env)) : (yield* evalExpr(node.b, env));
        case "Call": return yield* evalCall(node, env);
        default: throw CppError("неизвестное выражение " + node.type, node.line);
      }
    }

    function* evalBinary(node, env) {
      const op = node.op;
      const a = yield* evalExpr(node.left, env);
      if (op === "&&") return truthy(a) ? (truthy(yield* evalExpr(node.right, env)) ? 1 : 0) : 0;
      if (op === "||") return truthy(a) ? 1 : (truthy(yield* evalExpr(node.right, env)) ? 1 : 0);
      const b = yield* evalExpr(node.right, env);
      switch (op) {
        case "+": return (typeof a === "string" || typeof b === "string") ? ("" + a + b) : a + b;
        case "-": return a - b; case "*": return a * b;
        case "/": return divide(a, b); case "%": return a % b;
        case "==": return a == b ? 1 : 0; case "!=": return a != b ? 1 : 0;
        case "<": return a < b ? 1 : 0; case ">": return a > b ? 1 : 0;
        case "<=": return a <= b ? 1 : 0; case ">=": return a >= b ? 1 : 0;
        case "&": return a & b; case "|": return a | b; case "^": return a ^ b;
        case "<<": return a << b; case ">>": return a >> b;
        default: throw CppError("оператор " + op + " не поддержан");
      }
    }

    // lvalue → {get, set}
    function* lvalue(node, env) {
      if (node.type === "Ident") return { get: () => env.get(node.name), set: (v) => env.set(node.name, v) };
      if (node.type === "Member") { const o = yield* evalExpr(node.obj, env); return { get: () => o[node.name], set: (v) => { o[node.name] = v; } }; }
      if (node.type === "Index") { const o = yield* evalExpr(node.obj, env); const i = yield* evalExpr(node.index, env); return { get: () => o[i], set: (v) => { o[i] = v; } }; }
      throw CppError("нельзя присвоить этому выражению", node.line);
    }
    function* evalAssign(node, env) {
      const ref = yield* lvalue(node.target, env);
      const rhs = yield* evalExpr(node.value, env);
      let val = rhs;
      if (node.op !== "=") {
        const cur = ref.get();
        switch (node.op) {
          case "+=": val = (typeof cur === "string" || typeof rhs === "string") ? ("" + cur + rhs) : cur + rhs; break;
          case "-=": val = cur - rhs; break; case "*=": val = cur * rhs; break;
          case "/=": val = divide(cur, rhs); break; case "%=": val = cur % rhs; break;
          case "&=": val = cur & rhs; break; case "|=": val = cur | rhs; break; case "^=": val = cur ^ rhs; break;
          case "<<=": val = cur << rhs; break; case ">>=": val = cur >> rhs; break;
        }
      }
      ref.set(val); return val;
    }
    function* evalUpdate(node, env) {
      const ref = yield* lvalue(node.arg, env);
      const old = ref.get(); const nv = node.op === "++" ? old + 1 : old - 1;
      ref.set(nv); return node.prefix ? nv : old;
    }

    function* evalCall(node, env) {
      const argv = [];
      for (const a of node.args) argv.push(yield* evalExpr(a, env));
      // host-метод: obj.method(...)
      if (node.callee.type === "Member") {
        const obj = yield* evalExpr(node.callee.obj, env);
        const fn = obj == null ? undefined : obj[node.callee.name];
        if (typeof fn !== "function") throw CppError("метод '" + node.callee.name + "' не найден", node.line);
        const res = fn.apply(obj, argv);
        return isGen(res) ? (yield* res) : res;
      }
      // обычный вызов: пользовательская функция или builtin
      if (node.callee.type === "Ident") {
        const name = node.callee.name;
        if (funcs.has(name)) return yield* callUser(funcs.get(name), argv);
        if (global.has(name)) {
          const fn = global.get(name);
          if (typeof fn === "function") { const res = fn.apply(null, argv); return isGen(res) ? (yield* res) : res; }
        }
        throw CppError("функция '" + name + "' не найдена", node.line);
      }
      // вызов результата выражения
      const fn = yield* evalExpr(node.callee, env);
      if (typeof fn !== "function") throw CppError("вызов не-функции", node.line);
      const res = fn.apply(null, argv);
      return isGen(res) ? (yield* res) : res;
    }

    function* callUser(fn, argv) {
      const local = new Env(global);
      fn.params.forEach((p, i) => local.def(p, argv[i]));
      const sig = yield* execBlock(fn.body, local);
      return sig.k === "return" ? sig.v : undefined;
    }

    function truthy(v) { return !(v === 0 || v === false || v === null || v === undefined || v === ""); }

    // ---- публичный интерфейс
    let inited = false;
    return {
      ast,
      *runSetup() {
        if (!inited) { yield* initGlobals(); inited = true; }
        if (funcs.has("setup")) yield* callUser(funcs.get("setup"), []);
      },
      *runLoop() { if (funcs.has("loop")) yield* callUser(funcs.get("loop"), []); },
      hasLoop() { return funcs.has("loop"); },
      global,
    };
  }

  root.SimCpp.createMachine = createMachine;
})(typeof window !== "undefined" ? window : globalThis);
