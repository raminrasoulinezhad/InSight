// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: Apache-2.0
// Licensed under the Apache License, Version 2.0. You may obtain a copy at
// http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
// WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

// The scrape progress bar. Its whole job is to distinguish "working" from
// "hung" during a SEDI fetch, which is minutes of a browser window doing
// nothing this page can see. The interesting cases are all the ones where the
// count is not yet known — those must read as busy, never as 0% or as finished.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi, readIndexHtml } from "./harness.mjs";

const { ctx, el } = loadUi();
const { progressPercent, setProgress } = ctx;

const bar = () => el("progress");
const fill = () => el("progress-fill");

test("a known count maps to a percentage", () => {
  assert.equal(progressPercent(0, 4), 0);
  assert.equal(progressPercent(1, 4), 25);
  assert.equal(progressPercent(4, 4), 100);
});

test("an unknown count has no percentage", () => {
  // null is the signal for "indeterminate". Returning 0 here is the bug this
  // guards: a bar pinned at 0% for the minute it takes to open a browser is
  // indistinguishable from a crash.
  assert.equal(progressPercent(0, 0), null);
  assert.equal(progressPercent(3, 0), null);
});

test("a percentage never leaves 0–100", () => {
  // done can outrun total if a caller double-counts; the bar must not overflow
  // its track or go negative.
  assert.equal(progressPercent(9, 4), 100);
  assert.equal(progressPercent(-2, 4), 0);
});

test("showing an unknown count animates instead of sitting still", () => {
  setProgress(true, 0, 0);
  assert.equal(bar().classList.contains("hidden"), false);
  assert.equal(bar().classList.contains("indeterminate"), true);
  assert.equal(bar().getAttribute("aria-valuenow"), null, "no false precision");
});

test("showing a known count fills to that width", () => {
  setProgress(true, 1, 4);
  assert.equal(bar().classList.contains("indeterminate"), false);
  assert.equal(fill().style.width, "25%");
  assert.equal(bar().getAttribute("aria-valuenow"), "25");
});

test("a count arriving mid-run drops the animation", () => {
  // The real sequence: indeterminate while the browser opens, then determinate
  // once scrape_many knows how many companies it will fetch.
  setProgress(true, 0, 0);
  setProgress(true, 2, 8);
  assert.equal(bar().classList.contains("indeterminate"), false);
  assert.equal(fill().style.width, "25%");
});

test("hiding clears the bar so the next run cannot inherit it", () => {
  setProgress(true, 3, 4);
  setProgress(false);
  assert.equal(bar().classList.contains("hidden"), true);
  assert.equal(bar().classList.contains("indeterminate"), false);
  assert.equal(fill().style.width, "0%", "a stale 75% would reappear at the next start");
  assert.equal(bar().getAttribute("aria-valuenow"), null);
});

test("the shipped markup starts hidden and is announced", () => {
  const html = readIndexHtml();
  const div = html.match(/<div id="progress"[^>]*>/)[0];
  assert.match(div, /class="progress hidden"/, "it must not flash on a plain page load");
  assert.match(div, /role="progressbar"/);
  assert.match(div, /aria-label=/, "a bare bar tells a screen reader nothing");
});
