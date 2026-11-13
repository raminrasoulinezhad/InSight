// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// The "Not notified" list: names on the Companies or Insiders page that have
// no alarm, so they can be reviewed without scrolling every card.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml } from "./harness.mjs";

const { ctx, lex } = loadUi();
const { unalarmedCompanies, unalarmedInsiders, quietListHtml } = ctx;
const { companyKey, personKey } = lex;

const companies = [
  { issuer_name: "Beta Mines", exchange: "TSE", ticker: "BBB", _hay: "beta mines tse:bbb" },
  { issuer_name: "Alpha Oil", exchange: "TSE", ticker: "AAA", _hay: "alpha oil tse:aaa" },
  { issuer_name: "Gamma Gold", exchange: "TSXV", ticker: "GGG", _hay: "gamma gold tsxv:ggg" },
];

test("companies with an alarm are left out, and the rest are alphabetical", () => {
  const alarms = new Set([companyKey("TSE", "BBB")]);
  const names = unalarmedCompanies(companies, alarms, "").map(c => c.issuer_name);
  assert.deepEqual([...names], ["Alpha Oil", "Gamma Gold"]);
});

test("the company search still narrows the list", () => {
  const names = unalarmedCompanies(companies, new Set(), "ggg").map(c => c.ticker);
  assert.deepEqual([...names], ["GGG"]);
});

test("insiders with an alarm are left out", () => {
  const people = [
    { insider_name: "Zed Smith", _hay: "zed smith" },
    { insider_name: "Jane Doe", _hay: "jane doe" },
  ];
  const alarms = new Set([personKey("Jane Doe")]);
  const names = unalarmedInsiders(people, alarms, "").map(p => p.insider_name);
  assert.deepEqual([...names], ["Zed Smith"]);
});

test("the list names who is not notified and offers the bell", () => {
  const html = quietListHtml("company", [companies[1]]);
  assert.match(html, /1 company is not set to notify you/);
  assert.match(html, /Alpha Oil/);
  assert.match(html, /TSE:AAA/);
  assert.match(html, /class="bell"/);
  assert.doesNotMatch(html, /Beta Mines/);
});

test("the button is on the page the companies and insiders views share", () => {
  const html = readIndexHtml();
  assert.match(html, /id="unalarmed"/);
  assert.match(html, /Not notified/);
});
