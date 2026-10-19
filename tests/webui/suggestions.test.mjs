// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// The Insiders tab's list of companies the followed insiders trade that are
// not on the watchlist. It exists to answer "what should I add?", so every row
// must say who trades it and carry a working Add button.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi } from "./harness.mjs";

const { ctx, lex } = loadUi();
const { suggestionsBox } = ctx;
const { STATE } = lex;

const row = (over = {}) => ({
  issuer_name: "Blackrock Silver Corp.",
  exchange: "TSXV",
  ticker: "BRC",
  insiders: ["Eric Sprott", "Frank Giustra"],
  txn_count: 29,
  latest_date: "2026-10-13",
  ...over,
});

test("nothing to suggest shows no box at all", () => {
  STATE.suggestions = [];
  assert.equal(suggestionsBox(), "");
});

test("each row names who trades it and can be added", () => {
  STATE.suggestions = [row()];
  const html = suggestionsBox();
  assert.match(html, /Eric Sprott, Frank Giustra/);
  assert.match(html, /data-add-exch="TSXV"/);
  assert.match(html, /data-add-ticker="BRC"/);
  assert.match(html, /1 company the insiders you follow trade/);
});

test("it says adding needs a Fetch all to fill the company in", () => {
  STATE.suggestions = [row()];
  assert.match(suggestionsBox(), /Fetch all/);
});

test("it stays closed or open across re-renders", () => {
  STATE.suggestions = [row(), row({ ticker: "XYZ" })];
  STATE.suggestOpen = false;
  assert.doesNotMatch(suggestionsBox(), /<details class="suggest-box" open/);
  STATE.suggestOpen = true;
  assert.match(suggestionsBox(), /<details class="suggest-box" open/);
});

test("a hostile name cannot inject markup", () => {
  STATE.suggestions = [row({ issuer_name: "<img src=x onerror=alert(1)>", insiders: ["<b>x</b>"] })];
  const html = suggestionsBox();
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<b>x<\/b>/);
});
