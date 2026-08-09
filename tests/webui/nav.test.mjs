// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Noncommercial use permitted. Commercial use requires a separate license;
// contact the author. Provided "as is", without warranty of any kind.

// The back-stack and the cross-links between the Companies and Insiders tabs.
// The stack is a small state machine with two easy-to-break rules — typing must
// collapse to one step, and the cap must drop the OLDEST entry — so both are
// pinned here.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi } from "./harness.mjs";

const { ctx, lex } = loadUi();
const { pushNav, renderTxnTable, renderPersonCompany } = ctx;
const { STATE, NAV_MAX, navSnapshot, sameNav } = lex;

function reset(view = "companies") {
  STATE.nav.length = 0;
  STATE.navKind = null;
  STATE.view = view;
  STATE.q = "";
  STATE.qnorm = "";
  STATE.filter = "all";
  STATE.range = "d14";
}

test("the stack starts empty", () => {
  reset();
  assert.equal(STATE.nav.length, 0);
});

test("a snapshot captures exactly the navigable state", () => {
  reset();
  STATE.q = "ath";
  assert.deepEqual(
    { ...navSnapshot() },
    { view: "companies", q: "ath", filter: "all", range: "d14" },
  );
});

test("pushNav records where you are leaving from", () => {
  reset();
  STATE.q = "before";
  pushNav("jump");
  STATE.q = "after";
  assert.equal(STATE.nav.length, 1);
  assert.equal(STATE.nav[0].q, "before");
});

test("a burst of typing collapses into a single step", () => {
  reset();
  for (const q of ["a", "at", "ath", "atha", "athab"]) {
    pushNav("search");
    STATE.q = q;
  }
  assert.equal(STATE.nav.length, 1, "one step per burst, not one per keystroke");
  assert.equal(STATE.nav[0].q, "", "and it returns to the pre-typing query");
});

test("a different control between bursts starts a new step", () => {
  reset();
  pushNav("search");
  STATE.q = "ath";
  pushNav("filter");
  STATE.filter = "buy";
  pushNav("search");
  STATE.q = "athab";
  assert.equal(STATE.nav.length, 3);
});

test("typing after switching tabs is its own step", () => {
  reset();
  pushNav("tab");
  STATE.view = "people";
  pushNav("search");
  STATE.q = "sprott";
  assert.equal(STATE.nav.length, 2);
});

test("a no-op move is not recorded", () => {
  reset();
  pushNav("tab");
  pushNav("tab");
  pushNav("filter");
  assert.equal(STATE.nav.length, 1, "state never changed, so there is nowhere to go back to");
});

test("the stack caps at NAV_MAX", () => {
  reset();
  for (let i = 0; i < NAV_MAX * 3; i++) {
    STATE.navKind = null; // each is a distinct action
    pushNav("jump");
    STATE.q = "q" + i;
  }
  assert.equal(STATE.nav.length, NAV_MAX);
});

test("the cap drops the OLDEST entry, keeping recent history", () => {
  reset();
  for (let i = 0; i < NAV_MAX + 5; i++) {
    STATE.navKind = null;
    pushNav("jump");
    STATE.q = "q" + i;
  }
  assert.equal(STATE.nav[STATE.nav.length - 1].q, "q" + (NAV_MAX + 3), "newest step must survive");
  assert.equal(STATE.nav[0].q, "q4", "oldest steps are the ones discarded");
});

test("range and filter changes are restorable steps", () => {
  reset();
  pushNav("range");
  STATE.range = "24";
  pushNav("filter");
  STATE.filter = "sell";
  assert.deepEqual({ ...STATE.nav[0] }, { view: "companies", q: "", filter: "all", range: "d14" });
  assert.deepEqual({ ...STATE.nav[1] }, { view: "companies", q: "", filter: "all", range: "24" });
});

test("sameNav compares every navigable field", () => {
  const base = { view: "companies", q: "a", filter: "all", range: "d14" };
  assert.ok(sameNav(base, { ...base }));
  for (const k of ["view", "q", "filter", "range"]) {
    assert.ok(!sameNav(base, { ...base, [k]: "different" }), `${k} must be part of identity`);
  }
});

/* ---- cross-links --------------------------------------------------------- */

const txnRow = (over = {}) => ({
  date: "2026-06-01",
  insider_name: "Jane Doe",
  insider_role: "CEO",
  side: "buy",
  type: "Buy",
  shares: 100,
  total_value: 1000,
  ...over,
});

test("an insider name in a company's table is a link to that insider", () => {
  const html = renderTxnTable([txnRow()]);
  assert.match(html, /class="xnav" data-person="Jane Doe"/);
});

test("an issuer buyback is NOT linked — the Insiders tab excludes buybacks", () => {
  const html = renderTxnTable([
    txnRow({ insider_name: "Athabasca Oil Corporation", is_issuer_buyback: true }),
  ]);
  assert.ok(!html.includes("xnav"), "a buyback link could never resolve");
  assert.match(html, /Athabasca Oil Corporation/);
  assert.match(html, /t-badge buyback/);
});

test("institutions are still linked — only buybacks are excluded", () => {
  const html = renderTxnTable([txnRow({ entity_type: "institution" })]);
  assert.match(html, /class="xnav" data-person="Jane Doe"/);
});

test("a company on an insider's card links back with its exchange and ticker", () => {
  const html = renderPersonCompany({
    key: "TSE:ATH",
    issuer_name: "Athabasca Oil",
    exchange: "TSE",
    ticker: "ATH",
    buy_count: 1,
    sell_count: 0,
    txn_count: 1,
    buy_value: 1000,
    sell_value: 0,
    buy_shares: 100,
    sell_shares: 0,
    net_value: 1000,
  });
  assert.match(html, /class="xnav xnav-co"/);
  assert.match(html, /data-exch="TSE"/);
  assert.match(html, /data-ticker="ATH"/);
  assert.match(html, /data-name="Athabasca Oil"/);
});

test("the company link contains no block elements (invalid inside a button)", () => {
  const html = renderPersonCompany({
    key: "TSE:ATH",
    issuer_name: "Athabasca Oil",
    exchange: "TSE",
    ticker: "ATH",
    buy_count: 0,
    sell_count: 0,
    txn_count: 0,
    buy_value: 0,
    sell_value: 0,
    buy_shares: 0,
    sell_shares: 0,
    net_value: 0,
  });
  const button = html.match(/<button class="xnav xnav-co"[\s\S]*?<\/button>/)[0];
  assert.ok(!/<div/.test(button), "a <div> inside a <button> is invalid HTML");
  assert.match(button, /<span class="nm">/);
});

test("a quote in an insider name cannot break out of the link attribute", () => {
  const html = renderTxnTable([txnRow({ insider_name: 'Jane " onclick="x' })]);
  assert.ok(!html.includes('onclick="x"'), "injected handler must not survive");
  assert.match(html, /data-person="Jane &quot; onclick=&quot;x"/);
});
