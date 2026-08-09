// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// Keyboard handling for the settings dialog.
//
// A modal that lets Tab wander onto the page behind it is worse than no modal
// for anyone not using a mouse: you end up operating a feed you cannot see.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, makeElement, readIndexHtml } from "./harness.mjs";

const { ctx, lex, queries } = loadUi();
const { trapFocus, focusablesIn, openSettings, closeSettings } = ctx;
const { STATE } = lex;

/** A stand-in dialog card holding `n` focusable children. */
function fakeCard(n) {
  const items = Array.from({ length: n }, (_, i) => {
    const el = makeElement("button");
    el.name = `item${i}`;
    el.offsetParent = {}; // visible
    el.focus = () => {
      ctx.document.activeElement = el;
    };
    return el;
  });
  const card = makeElement("div");
  card.querySelectorAll = () => items;
  card.contains = (n2) => items.includes(n2);
  queries.set("#settings .modal-card", card);
  return items;
}

const tab = (shift = false) => {
  let prevented = false;
  trapFocus({ key: "Tab", shiftKey: shift, preventDefault: () => (prevented = true) });
  return prevented;
};

test("Tab past the last control wraps to the first", () => {
  const items = fakeCard(3);
  ctx.document.activeElement = items[2];
  assert.equal(tab(), true, "the default Tab must be prevented");
  assert.equal(ctx.document.activeElement, items[0]);
});

test("Shift+Tab from the first control wraps to the last", () => {
  const items = fakeCard(3);
  ctx.document.activeElement = items[0];
  assert.equal(tab(true), true);
  assert.equal(ctx.document.activeElement, items[2]);
});

test("Tab in the middle is left to the browser", () => {
  const items = fakeCard(3);
  ctx.document.activeElement = items[1];
  assert.equal(tab(), false, "normal movement inside the dialog is not our business");
  assert.equal(ctx.document.activeElement, items[1]);
});

test("focus that has escaped the dialog is pulled back", () => {
  const items = fakeCard(3);
  ctx.document.activeElement = makeElement("button"); // something on the page behind
  assert.equal(tab(true), true);
  assert.equal(ctx.document.activeElement, items[2]);
});

test("keys other than Tab pass through untouched", () => {
  fakeCard(3);
  let prevented = false;
  trapFocus({ key: "a", preventDefault: () => (prevented = true) });
  assert.equal(prevented, false);
});

test("a dialog with nothing focusable does not throw", () => {
  fakeCard(0);
  assert.doesNotThrow(() => tab());
});

test("hidden controls are not part of the cycle", () => {
  // A collapsed section's buttons would otherwise be tab stops that go nowhere.
  const visible = makeElement("button");
  visible.offsetParent = {};
  const hidden = makeElement("button");
  hidden.offsetParent = null;
  const card = makeElement("div");
  card.querySelectorAll = () => [visible, hidden];
  assert.deepEqual(Array.from(focusablesIn(card)), [visible]);
});

test("closing returns focus to whatever opened the dialog", () => {
  fakeCard(2);
  const opener = makeElement("button");
  opener.focus = () => {
    ctx.document.activeElement = opener;
  };
  ctx.document.activeElement = opener;
  openSettings("theme");
  assert.equal(STATE.settingsOpener, opener, "the opener is remembered");
  ctx.document.activeElement = makeElement("button"); // focus moved inside
  closeSettings();
  assert.equal(ctx.document.activeElement, opener, "focus came back");
  assert.equal(STATE.settingsOpener, null, "and the reference is released");
});

test("the dialog is marked up as a modal for assistive tech", () => {
  const html = readIndexHtml();
  const card = html.match(/<div class="modal-card"[^>]*>/)[0];
  assert.match(card, /role="dialog"/);
  assert.match(card, /aria-modal="true"/);
  assert.match(card, /aria-labelledby="settings-title"/);
  assert.match(html, /id="settings-title"/);
});
