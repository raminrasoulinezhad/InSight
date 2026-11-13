// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// Fetch all shows four stages at once. A single bar can only name the one it
// is on, which is what made the other stages look stuck.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml } from "./harness.mjs";

const { ctx, el } = loadUi();
const { phaseStatusText, renderPhases } = ctx;

test("a running stage names its place and the company", () => {
  assert.equal(
    phaseStatusText({ state: "running", done: 2, total: 10, label: "Gold Co", detail: "" }),
    "3 of 10 Gold Co",
  );
});

test("a stage that has not counted yet says what it is doing", () => {
  assert.equal(
    phaseStatusText({ state: "running", done: 0, total: 0, label: "", detail: "opening the browser" }),
    "opening the browser",
  );
  assert.equal(
    phaseStatusText({ state: "waiting", done: 0, total: 0, label: "", detail: "" }),
    "waiting",
  );
});

test("a finished stage says how it ended", () => {
  assert.equal(
    phaseStatusText({ state: "done", done: 4, total: 4, label: "", detail: "1 fetched, 3 already up to date" }),
    "1 fetched, 3 already up to date",
  );
  assert.equal(
    phaseStatusText({ state: "skipped", done: 0, total: 0, label: "", detail: "no SEC contact configured" }),
    "no SEC contact configured",
  );
  assert.equal(
    phaseStatusText({ state: "failed", done: 1, total: 4, label: "", detail: "SEDI's bot check is down" }),
    "SEDI's bot check is down",
  );
});

test("a bot check replaces the company count", () => {
  const text = phaseStatusText(
    { state: "running", done: 1, total: 4, label: "ABC", detail: "SEDI needs you" },
    true,
  );
  assert.equal(text, "SEDI needs you");
});

test("rendering the four stages shows each of them", () => {
  const shown = renderPhases([
    { name: "MarketBeat", state: "running", done: 2, total: 10, label: "Gold Co", detail: "" },
    { name: "SEDI", state: "running", done: 0, total: 0, label: "", detail: "opening the browser" },
    { name: "SEC EDGAR", state: "waiting", done: 0, total: 0, label: "", detail: "" },
    { name: "Followed insiders", state: "waiting", done: 0, total: 0, label: "", detail: "" },
  ], false);
  assert.equal(shown, true);
  const html = el("phases").innerHTML;
  assert.equal(el("phases").classList.contains("hidden"), false);
  for (const name of ["MarketBeat", "SEDI", "SEC EDGAR", "Followed insiders"]) {
    assert.ok(html.includes(name), name);
  }
  assert.ok(html.includes("3 of 10 Gold Co"));
  assert.ok(html.includes("opening the browser"));
  assert.ok(html.includes("waiting"));
});

test("an empty list hides the rows", () => {
  renderPhases([], false);
  assert.equal(el("phases").classList.contains("hidden"), true);
  assert.equal(el("phases").innerHTML, "");
});

test("the page ships a place for the four rows", () => {
  assert.match(readIndexHtml(), /id="phases"/);
});

test("a reload joins a fetch that is already running", () => {
  assert.match(readIndexHtml(), /function resumeRefresh/);
  assert.match(readIndexHtml(), /resumeRefresh\(\)/);
});
