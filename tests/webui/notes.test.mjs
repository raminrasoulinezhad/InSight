// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// Notes: bullet normalization, rendering, and escaping.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi } from "./harness.mjs";

const { ctx, lex } = loadUi();
const { bulletize, esc, renderNote, noteBtn } = ctx;
const { stripBullet, noteLines, noteKey, BULLET, STATE } = lex;

test("bulletize gives every non-blank line exactly one bullet", () => {
  assert.deepEqual(bulletize("one\ntwo").split("\n"), ["• one", "• two"]);
});

test("bulletize does not double-bullet an already-bulleted line", () => {
  assert.equal(bulletize("• already"), "• already");
});

test("bulletize accepts dash and star markers from pasted text", () => {
  assert.deepEqual(bulletize("- dash\n* star").split("\n"), ["• dash", "• star"]);
});

test("bulletize drops blank and whitespace-only lines", () => {
  assert.deepEqual(bulletize("a\n\n   \n\t\nb").split("\n"), ["• a", "• b"]);
});

test("bulletize strips leading indentation from pasted text", () => {
  assert.equal(bulletize("    indented"), "• indented");
});

test("bulletize of empty or whitespace input is empty (so the note clears)", () => {
  assert.equal(bulletize(""), "");
  assert.equal(bulletize("   \n  \n"), "");
  assert.equal(bulletize(BULLET), "");
});

test("bulletize is idempotent", () => {
  const once = bulletize("plain\n- dash\n  indented");
  assert.equal(bulletize(once), once);
});

test("bulletize keeps interior punctuation and dashes", () => {
  assert.equal(bulletize("buy-side view: up 3%"), "• buy-side view: up 3%");
});

test("bulletize preserves line order", () => {
  assert.deepEqual(bulletize("first\nsecond\nthird").split("\n"), [
    "• first",
    "• second",
    "• third",
  ]);
});

test("stripBullet removes only the marker, not the content", () => {
  assert.equal(stripBullet("• watch Q3"), "watch Q3");
  assert.equal(stripBullet("no marker"), "no marker");
});

test("noteLines yields clean display lines", () => {
  // Array.from: arrays built inside the vm carry that realm's prototype.
  assert.deepEqual(Array.from(noteLines("• a\n• b")), ["a", "b"]);
  assert.deepEqual(Array.from(noteLines("")), []);
  assert.deepEqual(Array.from(noteLines(null)), []);
});

test("noteKey matches the backend's EXCH:TICKER form", () => {
  assert.equal(noteKey("tse", "ath"), "TSE:ATH");
});

test("esc neutralizes HTML so a note cannot inject markup", () => {
  assert.equal(esc('<img src=x onerror="alert(1)">'), "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
  assert.equal(esc("a & b"), "a &amp; b");
});

test("a saved note renders as escaped list items", () => {
  STATE.notes["TSE:ATH"] = "• <b>bold</b>\n• second";
  STATE.notesOpen.clear();
  const html = renderNote({ exchange: "TSE", ticker: "ATH", issuer_name: "Athabasca" });
  assert.match(html, /<ul class="note-view">/);
  assert.match(html, /&lt;b&gt;bold&lt;\/b&gt;/);
  assert.ok(!html.includes("<b>bold</b>"), "raw markup must not survive");
  assert.equal((html.match(/<li>/g) || []).length, 2);
});

test("a company with no note renders no note bar at all", () => {
  STATE.notes = {};
  STATE.notesOpen.clear();
  assert.equal(renderNote({ exchange: "TSE", ticker: "XYZ", issuer_name: "X" }), "");
});

test("an open editor prefills a bullet and carries the draft", () => {
  STATE.notes = {};
  STATE.notesOpen = new Set(["TSE:ATH"]);
  STATE.noteDraft = {};
  const fresh = renderNote({ exchange: "TSE", ticker: "ATH", issuer_name: "Athabasca" });
  assert.match(fresh, /<textarea[^>]*class="note-ta"/);
  assert.match(fresh, />• <\/textarea>/);

  STATE.noteDraft["TSE:ATH"] = "• work in progress";
  const withDraft = renderNote({ exchange: "TSE", ticker: "ATH", issuer_name: "Athabasca" });
  assert.match(withDraft, /• work in progress<\/textarea>/);
});

test("the note button reflects whether a note exists", () => {
  STATE.notes = {};
  STATE.notesOpen.clear();
  assert.ok(!noteBtn({ exchange: "TSE", ticker: "ATH" }).includes("note-btn on"));
  STATE.notes["TSE:ATH"] = "• something";
  assert.ok(noteBtn({ exchange: "TSE", ticker: "ATH" }).includes("note-btn on"));
});

test("a quote in a company name cannot break out of the button attribute", () => {
  STATE.notes = {};
  const html = noteBtn({ exchange: 'TS"E', ticker: 'A"TH' });
  assert.ok(!/data-exch="[^"]*"[^ =]/.test(html), "attribute must stay well-formed");
  assert.match(html, /&quot;/);
});
