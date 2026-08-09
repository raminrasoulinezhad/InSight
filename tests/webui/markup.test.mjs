// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Noncommercial use permitted. Commercial use requires a separate license;
// contact the author. Provided "as is", without warranty of any kind.

// Markup-level invariants that the rendering tests can't see, most importantly
// the places where the same fact is stated twice and could drift apart.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml } from "./harness.mjs";

const html = readIndexHtml();
const { lex } = loadUi();
const { STATE } = lex;

function rangeOptions() {
  const select = html.match(/<select id="range"[\s\S]*?<\/select>/)[0];
  return [...select.matchAll(/<option value="([^"]+)"([^>]*)>([^<]+)<\/option>/g)].map((m) => ({
    value: m[1],
    selected: /\bselected\b/.test(m[2]),
    label: m[3],
  }));
}

test("exactly one range option is preselected", () => {
  assert.equal(rangeOptions().filter((o) => o.selected).length, 1);
});

test("the default range is 2 weeks", () => {
  const chosen = rangeOptions().find((o) => o.selected);
  assert.equal(chosen.value, "d14");
  assert.equal(chosen.label, "Last 2 weeks");
});

test("the preselected <option> and STATE.range agree", () => {
  // These are two independent sources of truth for the same fact: the markup
  // decides what the box shows, STATE.range decides what gets fetched. If they
  // drift, the app loads a different window than the one displayed.
  assert.equal(rangeOptions().find((o) => o.selected).value, STATE.range);
});

test("every range option maps to a query the loader understands", () => {
  for (const o of rangeOptions()) {
    assert.match(o.value, /^(d\d+|\d+)$/, `option "${o.value}" is neither a day nor a month token`);
  }
});

test("the tabs read Companies / Insiders / Alarms", () => {
  const tabs = [...html.matchAll(/<button data-view="(\w+)"[^>]*>([^<]+)<\/button>/g)].map((m) => [
    m[1],
    m[2],
  ]);
  assert.deepEqual(tabs, [
    ["companies", "Companies"],
    ["insiders", "Insiders"],
    ["alarms", "Alarms"],
  ]);
});

test("no user-visible 'People' label survives the rename", () => {
  const tabRow = html.match(/<div class="tabs" id="tabs">[\s\S]*?<\/div>/)[0];
  assert.ok(!/>People</.test(tabRow));
});

test("the Back button is in the tab row but is not a tab", () => {
  const tabRow = html.match(/<div class="tabs" id="tabs">[\s\S]*?<\/div>/)[0];
  const back = tabRow.match(/<button id="back"[^>]*>/)[0];
  assert.ok(!/data-view/.test(back), "a data-view here would make Back behave as a tab");
});

test("the Back button starts disabled", () => {
  assert.match(html, /<button id="back"[^>]*\bdisabled\b/);
});

test("tab wiring is scoped to [data-view] so Back is not swept up", () => {
  const selectors = [...html.matchAll(/querySelectorAll\("#tabs button([^"]*)"\)/g)].map((m) => m[1]);
  assert.ok(selectors.length > 0, "expected the tab wiring to be present");
  for (const s of selectors) {
    assert.equal(s, "[data-view]", `unscoped "#tabs button" selector would capture the Back button`);
  }
});

test("the page is self-contained — no external scripts, styles or fonts", () => {
  // A local-first app must render with no network at all.
  assert.ok(!/<script[^>]+src=/.test(html), "no external <script src>");
  assert.ok(!/<link[^>]+stylesheet/.test(html), "no external stylesheet");
  assert.ok(!/https?:\/\/(?!localhost)[^"' )]+\.(js|css|woff)/.test(html), "no remote assets");
});

test("the dead axis-label style is gone with the labels", () => {
  assert.ok(!/\.svg-chart text\s*\{/.test(html), "unused CSS for removed <text> elements");
});
