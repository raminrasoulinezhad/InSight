// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Noncommercial use permitted. Commercial use requires a separate license;
// contact the author. Provided "as is", without warranty of any kind.

// Loads the REAL insight/webui/index.html into a Node vm so the UI tests
// exercise shipped code, not a copy of it. The page is one self-contained file
// with no build step (a deliberate project constraint), so there is nothing to
// import — instead the <script> body is extracted and evaluated against a DOM
// stub just rich enough for the top-level wiring to run.
//
// The stub is intentionally dumb: it records listeners and returns inert
// elements. Tests here cover the *pure* logic (string handling, SVG geometry,
// the back-stack state machine); anything that genuinely needs layout or real
// events is verified against a live browser instead.

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
export const INDEX_HTML = join(HERE, "..", "..", "insight", "webui", "index.html");

export function readIndexHtml() {
  return readFileSync(INDEX_HTML, "utf8");
}

/** The page's inline script, minus the markup around it. */
export function extractScript(html = readIndexHtml()) {
  const m = html.match(/<script>([\s\S]*)<\/script>/);
  if (!m) throw new Error("no <script> block found in index.html");
  return m[1];
}

// `function` declarations land on the vm context automatically; top-level
// let/const bindings stay in the script's lexical scope and are invisible from
// outside. These are reached through live getters appended to the script, so a
// test sees the same objects the page mutates.
const LEXICAL = [
  "STATE",
  "BULLET",
  "NAV_MAX",
  "personKey",
  "companyKey",
  "noteKey",
  "stripBullet",
  "noteLines",
  "navSnapshot",
  "sameNav",
  "sameButQuery",
  "fmtShares",
  "fmtMoney",
  "fmtPrice",
  "dayLbl",
];

const exposeEpilogue = () =>
  "\n;globalThis.__lex = {" +
  LEXICAL.map(
    (n) => `get ${n}(){ try { return ${n} } catch (e) { return undefined } }`,
  ).join(",") +
  "};\n";

function makeElement(tag = "div") {
  const el = {
    tagName: tag.toUpperCase(),
    value: "",
    textContent: "",
    innerHTML: "",
    placeholder: "",
    disabled: false,
    dataset: {},
    style: {},
    listeners: {},
    _classes: new Set(),
    classList: {
      add: (...c) => c.forEach((x) => el._classes.add(x)),
      remove: (...c) => c.forEach((x) => el._classes.delete(x)),
      contains: (c) => el._classes.has(c),
      toggle: (c, on) => (on ? el._classes.add(c) : el._classes.delete(c)),
    },
    addEventListener: (type, fn) => {
      (el.listeners[type] ||= []).push(fn);
    },
    removeEventListener: () => {},
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    setSelectionRange: () => {},
    focus: () => {},
    click: () => (el.listeners.click || []).forEach((fn) => fn({ target: el })),
    getBoundingClientRect: () => ({ width: 0, height: 0 }),
    appendChild: () => {},
    remove: () => {},
  };
  return el;
}

/**
 * Evaluate the page script and hand back its globals.
 *
 * Returns the vm context, so a test can reach any top-level function or read
 * STATE directly. `fetchLog` records every request the boot sequence made.
 */
export function loadUi({ fetchResponses = {} } = {}) {
  const elements = new Map();
  const getEl = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  const fetchLog = [];

  const document = {
    getElementById: getEl,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: () => {},
    createElement: (tag) => makeElement(tag),
    body: makeElement("body"),
    activeElement: null,
  };

  const context = {
    document,
    window: { location: { reload: () => {} } },
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    // Deliberately NOT injecting Array/Object/Promise/…: the vm has its own
    // intrinsics, and injecting the outer realm's would make some values
    // cross-realm and others not. Tests normalize vm-returned arrays with
    // Array.from() instead.
    CSS: { escape: (s) => String(s).replace(/["\\]/g, "\\$&") },
    KeyboardEvent: class {},
    Event: class {},
    fetch: (url, opts) => {
      fetchLog.push({ url, opts });
      const body = fetchResponses[String(url).split("?")[0]] ?? {};
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    },
    alert: () => {},
    confirm: () => true,
  };
  context.globalThis = context;

  vm.createContext(context);
  vm.runInContext(extractScript() + exposeEpilogue(), context, {
    filename: "index.html<script>",
  });
  return { ctx: context, lex: context.__lex, elements, fetchLog, el: getEl };
}
