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
const { ctx, lex, system } = loadUi();
const { applyThemeCfg, pickTheme, setFollowSystem, resolveTheme, themeCard, themePage,
        notifyPage, renderAlarms, alarmSection } = ctx;
const { STATE, THEMES, DEFAULT_THEME, themeIds, isTheme, themeMode } = lex;

// Most tests care about a single painted theme, not the auto machinery.
const applyTheme = (id, opts) => applyThemeCfg({auto: false, theme: id}, opts);

const ids = () => Array.from(themeIds());

/* ---- the palette contract ------------------------------------------------ */

test("the promised themes all exist", () => {
  assert.deepEqual(ids().sort(), [
    "canadian", "caramel", "chic", "dark", "lemon",
    "light", "midnight", "newsprint", "sage", "terminal",
  ].sort());
});

test("there is at least one dark and one light theme", () => {
  assert.ok(ids().includes("dark"));
  assert.ok(ids().includes("light"));
});

test("every theme declares which shelf it belongs on", () => {
  for (const t of Array.from(THEMES)) {
    assert.ok(["dark", "light"].includes(t.mode), `${t.id} has mode "${t.mode}"`);
  }
});

test("a theme's declared mode matches how bright it actually is", () => {
  // Guards against a palette being filed on the wrong shelf — the grouping is
  // only useful if it tells the truth.
  const luminance = (hex) => {
    const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
    const n = parseInt(m[1], 16);
    return (0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  };
  for (const t of Array.from(THEMES)) {
    const lum = luminance(blocks[t.id]["--bg"]);
    if (t.mode === "dark") assert.ok(lum < 0.4, `"${t.id}" is filed dark but its bg is bright (${lum.toFixed(2)})`);
    else assert.ok(lum > 0.6, `"${t.id}" is filed light but its bg is dark (${lum.toFixed(2)})`);
  }
});

test("both shelves are populated", () => {
  const byMode = (m) => Array.from(THEMES).filter((t) => t.mode === m);
  assert.ok(byMode("dark").length >= 2);
  assert.ok(byMode("light").length >= 2);
});

test("the picker renders a labelled shelf per mode", () => {
  const page = themePage();
  assert.equal((page.match(/class="shelf-h"/g) || []).length, 2);
  for (const id of ids()) assert.ok(page.includes(`data-theme-id="${id}"`), `${id} missing`);
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

test("no theme lets the accent collide with buy or sell", () => {
  // The real hazard once themes get colourful: a red accent reading as "sell",
  // or a green accent reading as "buy". Chrome and signal must stay separable.
  for (const [id, vars] of Object.entries(blocks)) {
    assert.notEqual(vars["--accent"], vars["--buy"], `accent doubles as "buy" in "${id}"`);
    assert.notEqual(vars["--accent"], vars["--sell"], `accent doubles as "sell" in "${id}"`);
  }
});

test("Canadian keeps sell distinguishable from the red accent", () => {
  const c = blocks.canadian;
  assert.notEqual(c["--sell"], c["--accent"]);
  assert.notEqual(c["--buy"], c["--sell"]);
});

test("every colour variable is a valid CSS colour", () => {
  // A typo like "#c align-items" or a mistyped hex silently kills one rule and
  // leaves the rest of the theme looking almost right.
  const ok = /^(#[0-9a-f]{3}|#[0-9a-f]{6}|#[0-9a-f]{8}|rgba?\([\d\s.,%]+\))$/i;
  for (const [id, vars] of Object.entries(blocks)) {
    for (const [name, value] of Object.entries(vars)) {
      if (name === "--font") continue;
      assert.match(value, ok, `${id} ${name} is not a colour: ${JSON.stringify(value)}`);
    }
  }
});

test("every theme sets a font stack", () => {
  for (const [id, vars] of Object.entries(blocks)) {
    assert.ok(vars["--font"] && vars["--font"].length > 5, `"${id}" has no --font`);
  }
});

test("buy and sell never collide in any theme", () => {
  for (const [id, vars] of Object.entries(blocks)) {
    assert.notEqual(vars["--buy"], vars["--sell"], `buy and sell are identical in "${id}"`);
  }
});

/* ---- legibility ---------------------------------------------------------- */

// WCAG relative luminance / contrast ratio. Colourful themes are exactly where
// a palette stops being readable — a green "buy" on green paper looks fine to
// the person who picked it and fails for everyone else.
function contrast(a, b) {
  const chan = (h) => {
    const n = parseInt(h.trim().slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const lin = (c) => (c / 255 <= 0.03928 ? c / 255 / 12.92 : ((c / 255 + 0.055) / 1.055) ** 2.4);
  const lum = (h) => {
    const [r, g, bl] = chan(h);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(bl);
  };
  const [l1, l2] = [lum(a), lum(b)];
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
}

test("body text clears WCAG AAA against its background", () => {
  for (const [id, v] of Object.entries(blocks)) {
    const r = contrast(v["--text"], v["--bg"]);
    assert.ok(r >= 7, `"${id}" body text is ${r.toFixed(2)}:1 (want >= 7)`);
  }
});

test("secondary text clears WCAG AA", () => {
  for (const [id, v] of Object.entries(blocks)) {
    const r = contrast(v["--muted"], v["--bg"]);
    assert.ok(r >= 4.5, `"${id}" muted text is ${r.toFixed(2)}:1 (want >= 4.5)`);
  }
});

test("buy and sell stay legible in every theme", () => {
  // These two carry the meaning of the whole app; if they wash out, the page
  // is decorative.
  for (const [id, v] of Object.entries(blocks)) {
    for (const key of ["--buy", "--sell"]) {
      const r = contrast(v[key], v["--bg"]);
      assert.ok(r >= 4.5, `"${id}" ${key} is ${r.toFixed(2)}:1 on its background (want >= 4.5)`);
    }
  }
});

test("accents are readable on the background and under their own ink", () => {
  for (const [id, v] of Object.entries(blocks)) {
    const onBg = contrast(v["--accent"], v["--bg"]);
    assert.ok(onBg >= 4.5, `"${id}" accent is ${onBg.toFixed(2)}:1 on its background`);
    // --accent-ink is the label ON a filled accent button, so it contrasts with
    // the accent, not the page.
    const onAccent = contrast(v["--accent-ink"], v["--accent"]);
    assert.ok(onAccent >= 4.5, `"${id}" button label is ${onAccent.toFixed(2)}:1 on its accent`);
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

test("the whole preference is mirrored to localStorage for a flash-free reload", () => {
  applyTheme("midnight", { persist: false });
  const cached = JSON.parse(ctx.localStorage.getItem("insight.theme.cfg"));
  // The preference, not the resolved theme: with "match my system" on, the OS
  // may have changed since the app was last open, so the boot script has to be
  // able to resolve it again rather than replay a stale answer.
  assert.equal(cached.theme, "midnight");
  assert.equal(cached.auto, false);
  assert.ok("auto_dark" in cached && "auto_light" in cached);
});

test("the head boot script resolves the cached preference before first paint", () => {
  const boot = html.match(/<script id="theme-boot">([\s\S]*?)<\/script>/);
  assert.ok(boot, "without this the page flashes the default theme on every load");
  assert.match(boot[1], /localStorage\.getItem\("insight\.theme\.cfg"\)/);
  assert.match(boot[1], /prefers-color-scheme: dark/, "auto must be resolved, not cached");
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

/* ---- following the system ------------------------------------------------ */

function manual(theme) {
  system.dark = false;
  applyThemeCfg({ auto: false, theme, auto_dark: "dark", auto_light: "light" }, { persist: false });
}

function following({ dark, light }) {
  applyThemeCfg({ auto: true, auto_dark: dark, auto_light: light }, { persist: false });
}

const painted = () => ctx.document.documentElement.getAttribute("data-theme");

test("with auto off the chosen theme is used whatever the OS says", () => {
  manual("caramel");
  system.dark = true;
  assert.equal(resolveTheme(), "caramel");
  system.dark = false;
  assert.equal(resolveTheme(), "caramel");
});

test("with auto on a dark system gets the dark pick", () => {
  following({ dark: "midnight", light: "lemon" });
  system.dark = true;
  applyThemeCfg({}, { persist: false });
  assert.equal(painted(), "midnight");
});

test("with auto on a light system gets the light pick", () => {
  following({ dark: "midnight", light: "lemon" });
  system.dark = false;
  applyThemeCfg({}, { persist: false });
  assert.equal(painted(), "lemon");
});

test("flipping the OS repaints a following app", () => {
  following({ dark: "chic", light: "sage" });
  system.dark = false;
  applyThemeCfg({}, { persist: false });
  assert.equal(painted(), "sage");
  system.dark = true;
  system.emit(); // the OS switched while the app was open
  assert.equal(painted(), "chic");
});

test("a missed change event is caught up on the next resync", () => {
  // Not every environment delivers the media change event, and a scheduled OS
  // switch usually happens while the app is in the background — so returning to
  // the tab re-checks rather than trusting the event.
  following({ dark: "chic", light: "sage" });
  system.dark = true;
  applyThemeCfg({}, { persist: false });
  assert.equal(painted(), "chic");
  system.dark = false; // flipped with no event delivered
  assert.equal(painted(), "chic", "still stale, as expected");
  ctx.resyncSystemTheme();
  assert.equal(painted(), "sage");
});

test("a resync leaves a manually-themed app alone", () => {
  manual("terminal");
  system.dark = false;
  ctx.resyncSystemTheme();
  assert.equal(painted(), "terminal");
});

test("flipping the OS leaves a manually-themed app alone", () => {
  manual("terminal");
  system.dark = true;
  system.emit();
  assert.equal(painted(), "terminal", "a hand-picked theme must not be overridden");
});

test("turning auto off restores the hand-picked theme", () => {
  manual("newsprint");
  following({ dark: "dark", light: "light" });
  system.dark = true;
  applyThemeCfg({}, { persist: false });
  assert.equal(painted(), "dark");
  setFollowSystem(false);
  assert.equal(painted(), "newsprint", "the manual choice was remembered, not overwritten");
});

test("clicking a card while following sets that shelf's pick, not the theme", () => {
  manual("canadian");
  following({ dark: "dark", light: "light" });
  system.dark = true;
  applyThemeCfg({}, { persist: false });

  pickTheme("caramel"); // a dark theme
  assert.equal(STATE.themeCfg.auto_dark, "caramel");
  assert.equal(STATE.themeCfg.auto_light, "light", "the light shelf is untouched");
  assert.equal(STATE.themeCfg.theme, "canadian", "the manual choice is preserved");
  assert.equal(painted(), "caramel");

  pickTheme("sage"); // a light theme, while the OS is dark
  assert.equal(STATE.themeCfg.auto_light, "sage");
  assert.equal(painted(), "caramel", "still dark outside, so the dark pick keeps painting");
});

test("clicking a card while not following sets the theme", () => {
  manual("dark");
  pickTheme("lemon");
  assert.equal(STATE.themeCfg.theme, "lemon");
  assert.equal(painted(), "lemon");
});

test("each auto pick is constrained to its own shelf", () => {
  for (const t of Array.from(THEMES)) {
    manual("dark");
    following({ dark: "dark", light: "light" });
    pickTheme(t.id);
    const cfg = STATE.themeCfg;
    if (themeMode(t.id) === "dark") assert.equal(cfg.auto_dark, t.id);
    else assert.equal(cfg.auto_light, t.id);
    // a light theme must never land in auto_dark, or the app brightens at night
    assert.equal(themeMode(cfg.auto_dark), "dark");
    assert.equal(themeMode(cfg.auto_light), "light");
  }
});

test("the picker offers the follow-system toggle", () => {
  manual("dark");
  assert.match(themePage(), /id="follow-system"/);
  assert.ok(!/id="follow-system"[^>]*checked/.test(themePage()));
  following({ dark: "dark", light: "light" });
  assert.match(themePage(), /id="follow-system"[^>]*checked/);
});

test("while following, each shelf marks its own pick", () => {
  following({ dark: "chic", light: "sage" });
  system.dark = true;
  applyThemeCfg({}, { persist: false });
  const page = themePage();
  assert.equal((page.match(/theme-card active/g) || []).length, 2, "one tick per shelf");
  assert.match(page, /class="theme-card active" data-theme-id="chic"/);
  assert.match(page, /class="theme-card active" data-theme-id="sage"/);
  assert.match(page, /● now/, "the one currently painting should be called out");
});

test("with auto off exactly one card is marked", () => {
  manual("caramel");
  const page = themePage();
  assert.equal((page.match(/theme-card active/g) || []).length, 1);
  assert.ok(!page.includes("● now"));
});

test("a missing matchMedia does not break theme resolution", () => {
  // Older/embedded browsers: fall back to the light pick rather than throwing.
  const saved = ctx.matchMedia;
  ctx.matchMedia = undefined;
  try {
    following({ dark: "chic", light: "sage" });
    assert.equal(resolveTheme(), "sage");
  } finally {
    ctx.matchMedia = saved;
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
