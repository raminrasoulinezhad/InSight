// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// The "find this insider's companies on SEDI" dialog. Its job is to answer one
// question — which of these am I not already tracking? — so the rules that
// matter are which rows offer an Add button and which explain why they cannot.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi } from "./harness.mjs";

const { ctx, lex, el } = loadUi();
const { renderInsiderFind, sediFindBtn, openInsiderFind, closeInsiderFind } = ctx;
// an arrow-function const, so it is lexically scoped rather than on the context
const { insiderFindOpen } = lex;

const body = () => el("insider-find-body").innerHTML;

const company = (over = {}) => ({
  issuer_name: "West Red Lake Gold Mines Ltd.",
  exchange: "TSXV",
  ticker: "WRLG",
  txn_count: 3,
  latest_date: "2026-06-05",
  on_watchlist: false,
  resolved: true,
  ...over,
});

test("an untracked company offers an Add button carrying its identity", () => {
  renderInsiderFind({ name: "Sprott", companies: [company()] });
  assert.match(body(), /class="add-btn"/);
  assert.match(body(), /data-add-exch="TSXV"/);
  assert.match(body(), /data-add-ticker="WRLG"/);
  assert.match(body(), /data-add-name="West Red Lake Gold Mines Ltd\."/);
});

test("a company already on the watchlist offers nothing to add", () => {
  renderInsiderFind({ name: "Sprott", companies: [company({ on_watchlist: true })] });
  assert.doesNotMatch(body(), /class="add-btn"/);
  assert.match(body(), /on watchlist/);
});

test("an unresolved issuer is shown, not hidden", () => {
  // These are exactly the obscure venture names the search exists to surface.
  // Dropping the row because we could not find a ticker would defeat the point.
  renderInsiderFind({
    name: "Sprott",
    companies: [company({ resolved: false, ticker: "", exchange: "", issuer_name: "Tiny Co" })],
  });
  assert.match(body(), /Tiny Co/);
  assert.match(body(), /no ticker/);
  assert.doesNotMatch(body(), /class="add-btn"/, "nothing to add without a ticker");
});

test("the lead line counts only what can actually be added", () => {
  renderInsiderFind({
    name: "Sprott",
    companies: [
      company({ ticker: "AAA", on_watchlist: true }),
      company({ ticker: "BBB" }),
      company({ ticker: "", resolved: false }),
    ],
  });
  assert.match(body(), /<b>1<\/b> of these 3 are not on your watchlist/);
});

test("all-tracked says so instead of showing a count of zero", () => {
  renderInsiderFind({ name: "Sprott", companies: [company({ on_watchlist: true })] });
  assert.match(body(), /All 1 are already on your watchlist/);
});

test("no results explains how to search differently", () => {
  renderInsiderFind({ name: "Nosuchperson", companies: [] });
  assert.match(body(), /No SEDI filings found/);
  assert.match(body(), /starts with/, "the fix is a shorter name; say so");
});

test("it says adding does not fetch the history", () => {
  // Adding only touches the watchlist; without a refresh the new company is an
  // empty card, which looks broken if it was not explained.
  renderInsiderFind({ name: "Sprott", companies: [company()] });
  assert.match(body(), /Refresh data/);
});

test("a hostile issuer name cannot inject markup", () => {
  renderInsiderFind({
    name: "Sprott",
    companies: [company({ issuer_name: '<img src=x onerror=alert(1)>"' })],
  });
  assert.doesNotMatch(body(), /<img/);
  assert.match(body(), /&lt;img/);
});

test("only individuals are offered the SEDI lookup", () => {
  // The search drives SEDI's "insider family name" field, which is the wrong
  // one for a fund — those file under "insider company name" and would return
  // nothing, so offering the button there is a dead end.
  assert.match(sediFindBtn({ insider_name: "Eric Sprott" }), /data-find-insider="Eric Sprott"/);
  assert.equal(sediFindBtn({ insider_name: "Eric Sprott", entity_type: "institution" }), "");
  assert.equal(sediFindBtn({ insider_name: "Unknown" }), "");
  assert.equal(sediFindBtn({ insider_name: "" }), "");
});

test("opening and closing the dialog returns focus where it came from", () => {
  const opener = { focus() { opener.focused = true; }, focused: false };
  ctx.document.activeElement = opener;
  openInsiderFind("Eric Sprott");
  assert.equal(insiderFindOpen(), true);
  assert.match(el("insider-find-title").textContent, /Eric Sprott/);

  ctx.document.activeElement = { focus() {} };  // focus moved inside the dialog
  closeInsiderFind();
  assert.equal(insiderFindOpen(), false);
  assert.equal(opener.focused, true, "focus must not be dumped at the top of the page");
});
