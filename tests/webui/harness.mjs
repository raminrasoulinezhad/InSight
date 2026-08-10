// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

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
  "THEMES",
  "DEFAULT_THEME",
  "themeIds",
  "isTheme",
  "themeMode",
  "themesFor",
  "systemPrefersDark",
];

/** Every `[data-theme="x"] { … }` block in the stylesheet, as {id: {var: value}}. */
export function themeBlocks(html = readIndexHtml()) {
  const out = {};
  const re = /(:root, )?\[data-theme="([a-z]+)"\]\s*\{([^}]*)\}/g;
  for (const m of html.matchAll(re)) {
    const vars = {};
    for (const v of m[3].matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) vars[v[1]] = v[2].trim();
    out[m[2]] = vars;
  }
  return out;
}

/** Every `var(--x)` the stylesheet actually reads. */
export function usedVars(html = readIndexHtml()) {
  const style = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  return [...new Set([...style.matchAll(/var\((--[a-z0-9-]+)\)/g)].map((m) => m[1]))].sort();
}

const exposeEpilogue = () =>
  "\n;globalThis.__lex = {" +
  LEXICAL.map(
    (n) => `get ${n}(){ try { return ${n} } catch (e) { return undefined } }`,
  ).join(",") +
  "};\n";

export function makeElement(tag = "div") {
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
    attributes: {},
    setAttribute: (k, v) => {
      el.attributes[k] = String(v);
    },
    getAttribute: (k) => (k in el.attributes ? el.attributes[k] : null),
    removeAttribute: (k) => {
      delete el.attributes[k];
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

  // documentElement is where the theme attribute is set, so it records writes
  // rather than discarding them.
  const root = makeElement("html");
  root._attrs = {};
  root.setAttribute = (k, v) => {
    root._attrs[k] = v;
  };
  root.getAttribute = (k) => (k in root._attrs ? root._attrs[k] : null);

  // `queries` lets a test answer a specific document.querySelector(...) with a
  // node of its own, so code that reaches into the DOM can still be exercised.
  const queries = new Map();
  const document = {
    getElementById: getEl,
    documentElement: root,
    querySelector: (sel) => queries.get(sel) ?? null,
    querySelectorAll: (sel) => queries.get(sel) ?? [],
    addEventListener: () => {},
    createElement: (tag) => makeElement(tag),
    body: makeElement("body"),
    activeElement: null,
  };

  const store = new Map();
  const localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  };

  // Stands in for the OS light/dark setting. `system.dark` is writable and
  // `system.emit()` fires the page's change listener, so a test can simulate
  // someone flipping their desktop to dark mode while the app is open.
  const listeners = [];
  const system = {
    dark: false,
    emit() {
      listeners.forEach((fn) => fn({ matches: system.dark }));
    },
  };
  const matchMedia = (query) => ({
    media: query,
    get matches() {
      return /dark/.test(query) ? system.dark : !system.dark;
    },
    addEventListener: (_type, fn) => listeners.push(fn),
    removeEventListener: () => {},
  });

  const context = {
    document,
    localStorage,
    matchMedia,
    window: { location: { reload: () => {} }, matchMedia },
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
  return { ctx: context, lex: context.__lex, elements, fetchLog, el: getEl, system, queries };
}
