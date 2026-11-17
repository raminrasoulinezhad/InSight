// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// The Chart tab's symbol list: MarketBeat exchange codes become TradingView
// symbols, and gold/silver/platinum/oil stay pinned above the watchlist.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml } from "./harness.mjs";

const { ctx } = loadUi();
const { tvExchange, tvSymbol, chartEntries, chartWidgetUrl, chartRowsHtml } = ctx;

test("Toronto and NYSE American use TradingView's exchange names", () => {
  assert.equal(tvExchange("TSE"), "TSX");
  assert.equal(tvExchange("tsxv"), "TSXV");
  assert.equal(tvExchange("NYSEAMERICAN"), "AMEX");
  assert.equal(tvExchange("CSE"), "CSE");
  assert.equal(tvSymbol("TSE", "abx"), "TSX:ABX");
  assert.equal(tvSymbol("", "ABX"), "");
});

test("markets stay first and a Toronto name is listed under TSX", () => {
  const rows = chartEntries([
    { issuer_name: "Zeta Mine", exchange: "TSXV", ticker: "ZED" },
    { issuer_name: "Alpha Gold", exchange: "TSE", ticker: "AAA" },
    { issuer_name: "Alpha Gold", exchange: "TSE", ticker: "AAA" },
  ]);
  assert.deepEqual(Array.from(rows.slice(0, 4).map(r => r.symbol)), [
    "TVC:GOLD", "TVC:SILVER", "TVC:PLATINUM", "TVC:USOIL",
  ]);
  assert.deepEqual(Array.from(rows.slice(4).map(r => r.symbol)), ["TSX:AAA", "TSXV:ZED"]);
});

test("the widget url is the public chart, themed, with the symbol list", () => {
  const url = chartWidgetUrl("TSX:AAA", "light", ["TVC:GOLD", "TSX:AAA"]);
  assert.ok(url.startsWith("https://s.tradingview.com/widgetembed/?"));
  assert.match(url, /symbol=TSX%3AAAA/);
  assert.match(url, /theme=light/);
  assert.match(url, /watchlist=TVC%3AGOLD%1FTSX%3AAAA/);
  assert.doesNotMatch(url, /goldprice/);
});

test("the side list filters by name or ticker and marks the open symbol", () => {
  const entries = chartEntries([
    { issuer_name: "Alpha Gold", exchange: "TSE", ticker: "AAA" },
  ]);
  const html = chartRowsHtml(entries, "TSX:AAA", "aaa");
  assert.match(html, /Alpha Gold/);
  assert.match(html, /class="chart-row active"/);
  assert.doesNotMatch(html, /TVC:GOLD/);
  assert.match(chartRowsHtml(entries, "TVC:GOLD", "no-such"), /Nothing matches/);
});

test("the page has a Chart tab and keeps TradingView's credit", () => {
  const html = readIndexHtml();
  assert.match(html, /data-view="chart"/);
  assert.match(html, /Chart by TradingView/);
});
