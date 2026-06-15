/* ==========================================================================
 *  sim/cpp.js — интерпретатор подмножества Arduino C++ для 2D-симулятора UNI.
 *
 *  Не компилятор: лексер + парсер + tree-walking исполнитель на ГЕНЕРАТОРАХ.
 *  Кооперативная модель — delay()/блокирующие движения делают yield, драйвер
 *  симулятора прокручивает виртуальные часы и физику и возобновляет исполнение.
 *  Тот же .ino исполняется в симуляторе и заливается на плату.
 *
 *  Поддержано (по реальным примерам UNI):
 *    - #include (игнор), #define <имя> <значение>
 *    - глобальные объявления: UniBase robot("UNI"); UniDev module; int x = 0;
 *    - функции setup()/loop() и пользовательские
 *    - if/else, for, while, do-while, return, break, continue, блоки
 *    - выражения: арифметика, сравнения, логика, тернар, ++/--, присваивания,
 *      вызовы, доступ к членам (robot.moveDist(...), od.x), индексация массивов
 *
 *  Сигнал yield драйверу: {sleep: ms} | {wait: ()=>bool} | {breathe: true}
 *  Возврат генераторов исполнения — «сигнал управления»: {k:'normal'|'break'|
 *  'continue'|'return', v}.
 * ========================================================================== */
(function (root) {
  "use strict";

  // -------------------------------------------------------------- ошибки
  function CppError(msg, line) {
    const e = new Error(line ? `Строка ${line}: ${msg}` : msg);
    e.name = "CppError"; e.line = line || 0; e.cpp = true;
    return e;
  }

  // -------------------------------------------------------------- препроцессор
  // Убираем комментарии, собираем #define-константы, вырезаем директивы.
  function preprocess(src) {
    // строковые/символьные литералы беречь от вырезания комментариев
    let out = "", i = 0, n = src.length;
    while (i < n) {
      const c = src[i], c2 = src[i + 1];
      if (c === "/" && c2 === "/") { while (i < n && src[i] !== "\n") i++; }
      else if (c === "/" && c2 === "*") {
        i += 2;
        while (i < n && !(src[i] === "*" && src[i + 1] === "/")) { out += (src[i] === "\n") ? "\n" : " "; i++; }   // сохраняем переводы строк → точные номера строк
        i += 2; out += " ";
      } else if (c === '"' || c === "'") {
        const q = c; out += c; i++;
        while (i < n && src[i] !== q) { if (src[i] === "\\") { out += src[i] + (src[i + 1] || ""); i += 2; } else { out += src[i++]; } }
        out += q; i++;
      } else { out += c; i++; }
    }
    // собрать #define <NAME> <value...> и убрать все #-директивы построчно
    const defines = {};
    const lines = out.split("\n");
    for (let k = 0; k < lines.length; k++) {
      const m = /^\s*#\s*define\s+([A-Za-z_]\w*)\s+(.+?)\s*$/.exec(lines[k]);
      if (m) defines[m[1]] = m[2].trim();
      if (/^\s*#/.test(lines[k])) lines[k] = "";   // вырезаем директиву из кода
    }
    return { code: lines.join("\n"), defines };
  }

  // -------------------------------------------------------------- лексер
  const KW = new Set(["if", "else", "for", "while", "do", "return", "break",
    "continue", "true", "false"]);
  const TYPE_KW = new Set(["void", "int", "float", "double", "bool", "boolean",
    "char", "long", "short", "unsigned", "signed", "byte", "word", "String",
    "const", "static", "uint8_t", "int8_t", "uint16_t", "int16_t", "uint32_t",
    "int32_t", "uint64_t", "size_t", "volatile"]);

  function lex(code) {
    const toks = []; let i = 0, line = 1; const n = code.length;
    const push = (t, v) => toks.push({ t, v, line });
    const isIdStart = (c) => /[A-Za-z_]/.test(c);
    const isId = (c) => /[A-Za-z0-9_]/.test(c);
    const three = ["<<=", ">>=", "&&=", "||="];
    const two = ["==", "!=", "<=", ">=", "&&", "||", "++", "--", "+=", "-=",
      "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "->"];
    while (i < n) {
      let c = code[i];
      if (c === "\n") { line++; i++; continue; }
      if (c === " " || c === "\t" || c === "\r") { i++; continue; }
      if (isIdStart(c)) {
        let s = ""; while (i < n && isId(code[i])) s += code[i++];
        if (KW.has(s) || TYPE_KW.has(s)) push("kw", s); else push("id", s);
        continue;
      }
      if (/[0-9]/.test(c) || (c === "." && /[0-9]/.test(code[i + 1]))) {
        let s = "";
        if (c === "0" && (code[i + 1] === "x" || code[i + 1] === "X")) {
          s = "0x"; i += 2; while (i < n && /[0-9a-fA-F]/.test(code[i])) s += code[i++];
          push("num", parseInt(s, 16));
        } else {
          while (i < n && /[0-9.]/.test(code[i])) s += code[i++];
          if (i < n && (code[i] === "e" || code[i] === "E")) { s += code[i++]; if (code[i] === "+" || code[i] === "-") s += code[i++]; while (i < n && /[0-9]/.test(code[i])) s += code[i++]; }
          while (i < n && /[fFlLuU]/.test(code[i])) i++;   // суффиксы
          push("num", parseFloat(s));
        }
        continue;
      }
      if (c === '"') {
        let s = ""; i++;
        while (i < n && code[i] !== '"') { if (code[i] === "\\") { s += unesc(code[i + 1]); i += 2; } else s += code[i++]; }
        i++; push("str", s); continue;
      }
      if (c === "'") {
        let v = 0; i++;
        if (code[i] === "\\") { v = unesc(code[i + 1]).charCodeAt(0); i += 2; } else { v = code[i].charCodeAt(0); i++; }
        i++; push("num", v); continue;
      }
      const t3 = code.substr(i, 3);
      if (three.includes(t3)) { push("op", t3); i += 3; continue; }
      const t2 = code.substr(i, 2);
      if (two.includes(t2)) { push("op", t2); i += 2; continue; }
      if ("+-*/%=<>!&|^~?:.,;(){}[]".includes(c)) { push("op", c); i++; continue; }
      throw CppError("неизвестный символ '" + c + "'", line);
    }
    push("eof", null);
    return toks;
    function unesc(ch) {
      return ({ n: "\n", t: "\t", r: "\r", "0": "\0", "\\": "\\", '"': '"', "'": "'" })[ch] || ch;
    }
  }

  // -------------------------------------------------------------- парсер
  function parse(src) {
    const pre = preprocess(src);
    const toks = lex(pre.code);
    let p = 0;
    const peek = (k = 0) => toks[p + k];
    const next = () => toks[p++];
    const isOp = (v) => peek().t === "op" && peek().v === v;
    const isKw = (v) => peek().t === "kw" && peek().v === v;
    function eat(v) {
      const t = peek();
      if ((t.t === "op" || t.t === "kw") && t.v === v) return next();
      throw CppError(`ожидалось '${v}', получено '${t.v}'`, t.line);
    }
    // ---- объявление? (эвристика «тип имя …»)
    function looksLikeDecl() {
      const t = peek();
      if (t.t === "kw" && TYPE_KW.has(t.v)) return true;
      // ИМЯ ИМЯ → объявление объекта (UniBase robot)
      if (t.t === "id" && peek(1).t === "id") return true;
      return false;
    }
    function parseTypeTokens() {
      const parts = [];
      while (peek().t === "kw" && TYPE_KW.has(peek().v)) parts.push(next().v);
      // имя класса/типа (UniBase, UniDev, OdometryData …)
      if (peek().t === "id" && peek(1).t === "id") parts.push(next().v);
      // указатели/ссылки — проглатываем
      while (isOp("*") || isOp("&")) next();
      return parts.join(" ");
    }

    function parseProgram() {
      const decls = [];
      while (peek().t !== "eof") decls.push(parseTopLevel());
      return { type: "Program", decls, defines: pre.defines };
    }

    // после '(' (текущий токен) ищем парную ')' и смотрим, что за ней:
    // '{' → это определение функции; иначе '(' были аргументами конструктора.
    function parenFollowedByBrace() {
      let depth = 0;
      for (let j = 0; ; j++) {
        const t = peek(j);
        if (t.t === "eof") return false;
        if (t.t === "op" && t.v === "(") depth++;
        else if (t.t === "op" && t.v === ")") { if (--depth === 0) { const nx = peek(j + 1); return nx.t === "op" && nx.v === "{"; } }
      }
    }

    function parseTopLevel() {
      const startLine = peek().line;
      const type = parseTypeTokens();          // тип возвращаемого/переменной
      const name = eat_id();
      if (isOp("(") && parenFollowedByBrace()) {   // определение функции
        const params = parseParams();
        const body = parseBlock();
        return { type: "Func", name, params, body, line: startLine };
      }
      // глобальное объявление переменной/объекта (в т.ч. UniBase robot("UNI");)
      return finishVarDecl(type, name, startLine, true);
    }

    function eat_id() {
      const t = peek();
      if (t.t === "id") return next().v;
      throw CppError(`ожидался идентификатор, получено '${t.v}'`, t.line);
    }

    function parseParams() {
      eat("(");
      const params = [];
      if (!isOp(")")) {
        do {
          parseTypeTokens();
          if (peek().t === "id") params.push(next().v); else params.push("_");
          // дефолтные значения опускаем
          if (isOp("=")) { next(); parseAssign(); }
        } while (isOp(",") && next());
      }
      eat(")");
      return params;
    }

    function finishVarDecl(type, firstName, line, global) {
      const decls = [];
      let name = firstName;
      for (;;) {
        let init = null, ctorArgs = null;
        const dims = [];
        while (isOp("[")) { next(); dims.push(isOp("]") ? null : parseAssign()); eat("]"); }   // [], [N], [][2]…
        if (isOp("(")) { ctorArgs = parseArgs(); }       // конструктор объекта
        else if (isOp("=")) { next(); init = isOp("{") ? parseBraceInit() : parseAssign(); }
        decls.push({ name, init, ctorArgs, isArray: dims.length > 0, dims, ctype: type });
        if (isOp(",")) { next(); name = eat_id(); continue; }
        break;
      }
      eat(";");
      return { type: "VarDecl", decls, line, global: !!global };
    }
    function parseBraceInit() {                  // поддержка вложенных {…} и хвостовой запятой
      eat("{"); const items = [];
      while (!isOp("}")) {
        items.push(isOp("{") ? parseBraceInit() : parseAssign());
        if (isOp(",")) next(); else break;
      }
      eat("}");
      return { type: "ArrayLit", items };
    }

    // ---- statements
    function parseBlock() {
      const l = peek().line; eat("{"); const body = [];
      while (!isOp("}") && peek().t !== "eof") body.push(parseStmt());
      eat("}");
      return { type: "Block", body, line: l };
    }
    function parseStmt() {
      const t = peek();
      if (isOp("{")) return parseBlock();
      if (isOp(";")) { next(); return { type: "Empty" }; }
      if (isKw("if")) return parseIf();
      if (isKw("for")) return parseFor();
      if (isKw("while")) return parseWhile();
      if (isKw("do")) return parseDoWhile();
      if (isKw("return")) { next(); let v = null; if (!isOp(";")) v = parseExpr(); eat(";"); return { type: "Return", value: v, line: t.line }; }
      if (isKw("break")) { next(); eat(";"); return { type: "Break" }; }
      if (isKw("continue")) { next(); eat(";"); return { type: "Continue" }; }
      if (looksLikeDecl()) { const ty = parseTypeTokens(); const nm = eat_id(); return finishVarDecl(ty, nm, t.line, false); }
      const e = parseExpr(); eat(";"); return { type: "ExprStmt", expr: e, line: t.line };
    }
    function parseIf() {
      const l = peek().line; eat("if"); eat("("); const cond = parseExpr(); eat(")");
      const then = parseStmt(); let els = null;
      if (isKw("else")) { next(); els = parseStmt(); }
      return { type: "If", cond, then, els, line: l };
    }
    function parseWhile() {
      const l = peek().line; eat("while"); eat("("); const cond = parseExpr(); eat(")");
      return { type: "While", cond, body: parseStmt(), line: l };
    }
    function parseDoWhile() {
      const l = peek().line; eat("do"); const body = parseStmt();
      eat("while"); eat("("); const cond = parseExpr(); eat(")"); eat(";");
      return { type: "DoWhile", cond, body, line: l };
    }
    function parseFor() {
      const l = peek().line; eat("for"); eat("(");
      let init = null;
      if (!isOp(";")) {
        if (looksLikeDecl()) { const ty = parseTypeTokens(); const nm = eat_id(); init = finishVarDecl(ty, nm, l, false); }
        else { init = { type: "ExprStmt", expr: parseExpr() }; eat(";"); }
      } else eat(";");
      let cond = null; if (!isOp(";")) cond = parseExpr(); eat(";");
      let upd = null; if (!isOp(")")) upd = parseExpr(); eat(")");
      return { type: "For", init, cond, upd, body: parseStmt(), line: l };
    }

    // ---- expressions (Pratt)
    function parseExpr() {           // с запятой-оператором не заморачиваемся
      return parseAssign();
    }
    function parseAssign() {
      const left = parseTernary();
      const t = peek();
      if (t.t === "op" && ["=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="].includes(t.v)) {
        next(); const right = parseAssign();
        return { type: "Assign", op: t.v, target: left, value: right, line: t.line };
      }
      return left;
    }
    function parseTernary() {
      let c = parseBinary(0);
      if (isOp("?")) { next(); const a = parseAssign(); eat(":"); const b = parseAssign(); return { type: "Ternary", cond: c, a, b }; }
      return c;
    }
    const BINOP = [
      ["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
      ["<", ">", "<=", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"],
    ];
    function parseBinary(lvl) {
      if (lvl >= BINOP.length) return parseUnary();
      let left = parseBinary(lvl + 1);
      while (peek().t === "op" && BINOP[lvl].includes(peek().v)) {
        const op = next().v; const right = parseBinary(lvl + 1);
        left = { type: "Binary", op, left, right };
      }
      return left;
    }
    function parseUnary() {
      const t = peek();
      // C-style приведение типа: (int)x, (float)y, (unsigned long)z
      if (isOp("(") && peek(1).t === "kw" && TYPE_KW.has(peek(1).v)) {
        next(); const ty = parseTypeTokens(); eat(")");
        return { type: "Cast", ctype: ty, arg: parseUnary() };
      }
      if (t.t === "op" && ["!", "-", "+", "~"].includes(t.v)) { next(); return { type: "Unary", op: t.v, arg: parseUnary() }; }
      if (t.t === "op" && (t.v === "++" || t.v === "--")) { next(); return { type: "Update", op: t.v, prefix: true, arg: parseUnary() }; }
      return parsePostfix();
    }
    function parsePostfix() {
      let e = parsePrimary();
      for (;;) {
        if (isOp(".") || isOp("->")) { next(); const name = eat_id(); e = { type: "Member", obj: e, name }; }
        else if (isOp("(")) { const args = parseArgs(); e = { type: "Call", callee: e, args, line: peek().line }; }
        else if (isOp("[")) { next(); const idx = parseExpr(); eat("]"); e = { type: "Index", obj: e, index: idx }; }
        else if (isOp("++") || isOp("--")) { const op = next().v; e = { type: "Update", op, prefix: false, arg: e }; }
        else break;
      }
      return e;
    }
    function parseArgs() {
      eat("("); const args = [];
      if (!isOp(")")) do { args.push(parseAssign()); } while (isOp(",") && next());
      eat(")");
      return args;
    }
    function parsePrimary() {
      const t = peek();
      if (t.t === "num") { next(); return { type: "Num", value: t.v }; }
      if (t.t === "str") { next(); return { type: "Str", value: t.v }; }
      if (isKw("true")) { next(); return { type: "Num", value: 1 }; }
      if (isKw("false")) { next(); return { type: "Num", value: 0 }; }
      if (t.t === "id") { next(); return { type: "Ident", name: t.v, line: t.line }; }
      if (isOp("(")) { next(); const e = parseExpr(); eat(")"); return e; }
      throw CppError(`неожиданный токен '${t.v}'`, t.line);
    }

    return parseProgram();
  }

  root.SimCpp = { parse, CppError, TYPE_KW };
})(typeof window !== "undefined" ? window : globalThis);
