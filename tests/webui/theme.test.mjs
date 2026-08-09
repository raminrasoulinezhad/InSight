// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Noncommercial use permitted. Commercial use requires a separate license;
// contact the author. Provided "as is", without warranty of any kind.

// Themes and the settings dialog.
//
// The load-bearing test here is "every theme declares every variable": a theme
// missing one silently inherits the Dark value, so a light theme grows a single
// unreadable dark patch that nobody notices until a user reports it.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml, themeBlocks, usedVars } from "./harness.mjs";

const html = readIndexHtml();
const blocks = themeBlocks(html);
const { ctx, lex } = loadUi();
const { applyTheme, themeCard, themePage, notifyPage, renderAlarms, alarmSection } = ctx;
const { STATE, THEMES, DEFAULT_THEME, themeIds, isTheme } = lex;

const ids = () => Array.from(themeIds());

/* ---- the palette contract ------------------------------------------------ */

test("the promised themes all exist", () => {
  assert.deepEqual(ids(), ["dark", "light", "terminal", "newsprint", "midnight", "canadian"]);
});

test("there is at least one dark and one light theme", () => {
  assert.ok(ids().includes("dark"));
  assert.ok(ids().includes("light"));
});

test("every theme in the list has a stylesheet block", () => {
  for (const id of ids()) {
    assert.ok(blocks[id], `no [data-theme="${id}"] block — the app would paint with defaults`);
  }
});

test("every stylesheet block is offered in the picker", () => {
  for (const id of Object.keys(blocks)) {
    assert.ok(ids().includes(id), `[data-theme="${id}"] exists but is unreachable from the UI`);
  }
});

test("every theme declares the complete variable set", () => {
  const expected = Object.keys(blocks[DEFAULT_THEME]).sort();
  assert.ok(expected.length > 10, "sanity: the default theme should declare a full palette");
  for (const [id, vars] of Object.entries(blocks)) {
    const missing = expected.filter((v) => !(v in vars));
    assert.deepEqual(missing, [], `theme "${id}" is missing ${missing.join(", ")}`);
  }
});

test("every variable the stylesheet reads is declared by every theme", () => {
  // Catches the reverse drift: a new var(--x) added to a rule but never defined.
  for (const [id, vars] of Object.entries(blocks)) {
    const undeclared = usedVars(html).filter((v) => !(v in vars));
    assert.deepEqual(undeclared, [], `theme "${id}" never defines ${undeclared.join(", ")}`);
  }
});

test("no theme leaves a colour hardcoded outside the palette", () => {
  // A literal colour in a rule can't be re-themed, which is exactly how a light
  // theme ends up with a dark patch. The theme blocks themselves are exempt.
  const style = html.match(/<style>([\s\S]*?)<\/style>/)[1];
  const withoutThemes = style.replace(/(:root, )?\[data-theme="[a-z]+"\]\s*\{[^}]*\}/g, "");
  const literals = [...withoutThemes.matchAll(/#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)/g)]
    .map((m) => m[0])
    .filter((c) => !/^rgba?\(0,\s*0,\s*0,\s*\.55\)$/.test(c)); // modal scrim, intentionally fixed
  assert.deepEqual(literals, [], `hardcoded colours outside the themes: ${literals.join(", ")}`);
});

test("each theme is visually distinct from the others", () => {
  const seen = new Map();
  for (const [id, vars] of Object.entries(blocks)) {
    const sig = `${vars["--bg"]}|${vars["--accent"]}`;
    assert.ok(!seen.has(sig), `"${id}" and "${seen.get(sig)}" are the same background+accent`);
    seen.set(sig, id);
  }
});

test("Canadian keeps sell distinguishable from the red accent", () => {
  // A red-accented theme is the one place where "sell" and "a link" could read
  // as the same colour.
  const c = blocks.canadian;
  assert.notEqual(c["--sell"], c["--accent"]);
  assert.notEqual(c["--buy"], c["--sell"]);
});

test("buy and sell never collide in any theme", () => {
  for (const [id, vars] of Object.entries(blocks)) {
    assert.notEqual(vars["--buy"], vars["--sell"], `buy and sell are identical in "${id}"`);
  }
});

test("text is never the same colour as the background", () => {
  for (const [id, vars] of Object.entries(blocks)) {
    assert.notEqual(vars["--text"], vars["--bg"], `text is invisible in "${id}"`);
    assert.notEqual(vars["--muted"], vars["--bg"], `muted text is invisible in "${id}"`);
  }
});

/* ---- applying a theme ---------------------------------------------------- */

test("applying a theme sets the root attribute and STATE", () => {
  applyTheme("terminal", { persist: false });
  assert.equal(ctx.document.documentElement.getAttribute("data-theme"), "terminal");
  assert.equal(STATE.theme, "terminal");
});

test("an unknown theme falls back to the default rather than blanking the app", () => {
  applyTheme("chartreuse", { persist: false });
  assert.equal(ctx.document.documentElement.getAttribute("data-theme"), DEFAULT_THEME);
  assert.equal(STATE.theme, DEFAULT_THEME);
});

test("the choice is mirrored to localStorage for a flash-free reload", () => {
  applyTheme("midnight", { persist: false });
  assert.equal(ctx.localStorage.getItem("insight.theme"), "midnight");
});

test("the head boot script applies the cached theme before first paint", () => {
  const boot = html.match(/<script id="theme-boot">([\s\S]*?)<\/script>/);
  assert.ok(boot, "without this the page flashes the default theme on every load");
  assert.match(boot[1], /localStorage\.getItem\("insight\.theme"\)/);
  assert.match(boot[1], /setAttribute\("data-theme"/);
  assert.ok(
    html.indexOf('id="theme-boot"') < html.indexOf("<body"),
    "the boot script must run before the body renders",
  );
});

test("isTheme accepts exactly the offered ids", () => {
  for (const id of ids()) assert.ok(isTheme(id));
  assert.ok(!isTheme("nope"));
  assert.ok(!isTheme(""));
  assert.ok(!isTheme(undefined));
});

test("every theme has a name and a description for the picker", () => {
  for (const t of Array.from(THEMES)) {
    assert.ok(t.name && t.name.length > 0, `${t.id} has no name`);
    assert.ok(t.desc && t.desc.length > 10, `${t.id} has no useful description`);
  }
});

/* ---- the picker ---------------------------------------------------------- */

test("the picker offers one card per theme and marks the active one", () => {
  applyTheme("newsprint", { persist: false });
  const page = themePage();
  for (const id of ids()) assert.ok(page.includes(`data-theme-id="${id}"`), `${id} card missing`);
  assert.match(page, /class="theme-card active" data-theme-id="newsprint"/);
  assert.equal((page.match(/theme-card active/g) || []).length, 1, "exactly one card is active");
});

test("a swatch previews its own palette, not the active theme's", () => {
  applyTheme("dark", { persist: false });
  const card = themeCard({ id: "canadian", name: "Canadian", desc: "White and flag red." });
  assert.match(card, /<span class="swatch" data-theme="canadian"/);
});

test("card text is escaped", () => {
  const card = themeCard({ id: "x", name: '<img src=x>', desc: 'a "quoted" theme' });
  assert.ok(!card.includes("<img src=x>"));
  assert.match(card, /&lt;img src=x&gt;/);
});

/* ---- settings dialog layout ---------------------------------------------- */

test("the dialog has an Appearance page and a Notifications page", () => {
  const nav = html.match(/<nav class="settings-nav"[\s\S]*?<\/nav>/)[0];
  const pages = [...nav.matchAll(/data-page="(\w+)"[^>]*>([^<]+)</g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(pages, [
    ["theme", "Appearance"],
    ["notify", "Notifications"],
  ]);
});

test("the settings dialog starts hidden", () => {
  assert.match(html, /<div class="modal hidden" id="settings">/);
});

test("there is a settings button to open it", () => {
  assert.match(html, /id="settings-open"/);
});

/* ---- the notification setup moved, intact -------------------------------- */

test("the Notifications page carries every field collectNotifySettings reads", () => {
  // This is the contract between the moved form and the unchanged collector:
  // a renamed id here would save empty settings instead of failing loudly.
  const page = notifyPage();
  for (const id of ["e-en", "e-host", "e-port", "e-user", "e-pass", "e-from", "e-to",
                    "y-en", "y-server", "y-topic"]) {
    assert.ok(page.includes(`id="${id}"`), `field ${id} is missing from the settings page`);
  }
  assert.ok(page.includes('id="notify-save"') && page.includes('id="notify-test"'));
});

test("the password field is masked", () => {
  assert.match(notifyPage(), /<input type="password" id="e-pass"/);
});

test("stored notification settings are shown, escaped", () => {
  STATE.notify = { email: { enabled: true, username: 'a"b@x.com' }, ntfy: {}, alarms: [] };
  const page = notifyPage();
  assert.match(page, /id="e-en" checked/);
  assert.match(page, /a&quot;b@x\.com/);
});

/* ---- the Alarms tab keeps only the list ---------------------------------- */

function alarmsFeed(alarms, channels = {}) {
  STATE.notify = { email: channels.email || {}, ntfy: channels.ntfy || {}, alarms };
  STATE.view = "alarms";
  renderAlarms();
  return ctx.document.getElementById("feed").innerHTML;
}

test("alarms are split into Companies and Insiders", () => {
  const feed = alarmsFeed([
    { id: "1", type: "company", label: "Athabasca Oil" },
    { id: "2", type: "person", label: "Eric Sprott" },
    { id: "3", type: "company", label: "Cameco" },
  ]);
  assert.match(feed, /🏢 Companies \(2\)/);
  assert.match(feed, /👤 Insiders \(1\)/);
  assert.ok(
    feed.indexOf("Companies (2)") < feed.indexOf("Insiders (1)"),
    "companies section comes first",
  );
});

test("each group says so separately when it is empty", () => {
  const feed = alarmsFeed([{ id: "1", type: "person", label: "Eric Sprott" }]);
  assert.match(feed, /🏢 Companies \(0\)/);
  assert.match(feed, /No company alarms/);
  assert.ok(!feed.includes("No insider alarms"));
});

test("the notification form no longer appears on the Alarms tab", () => {
  const feed = alarmsFeed([{ id: "1", type: "company", label: "Athabasca Oil" }]);
  for (const id of ["e-host", "e-pass", "y-topic", "notify-save", "notify-test"]) {
    assert.ok(!feed.includes(`id="${id}"`), `${id} should live in Settings, not the Alarms tab`);
  }
});

test("the Alarms tab points at where delivery is configured", () => {
  const feed = alarmsFeed([{ id: "1", type: "company", label: "X" }]);
  assert.match(feed, /Notifications/);
});

test("alarms with no delivery channel enabled are called out", () => {
  const off = alarmsFeed([{ id: "1", type: "company", label: "X" }]);
  assert.match(off, /No delivery channel is on/);
  const on = alarmsFeed([{ id: "1", type: "company", label: "X" }], { email: { enabled: true } });
  assert.ok(!on.includes("No delivery channel is on"));
});

test("no warning when there are no alarms to deliver", () => {
  assert.ok(!alarmsFeed([]).includes("No delivery channel is on"));
});

test("alarm labels are escaped", () => {
  const feed = alarmsFeed([{ id: "1", type: "company", label: '<script>x</script>' }]);
  assert.ok(!feed.includes("<script>x</script>"));
  assert.match(feed, /&lt;script&gt;/);
});

test("each alarm row keeps its delete button", () => {
  const html2 = alarmSection("T", [{ id: "abc", type: "company", label: "X" }], "none");
  assert.match(html2, /class="del-co del-alarm" data-id="abc"/);
});
