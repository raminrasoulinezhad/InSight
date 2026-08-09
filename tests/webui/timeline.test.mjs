// Copyright (c) 2026 Seyedramin Rasoulinezhad
// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
// Noncommercial use permitted. Commercial use requires a separate license;
// contact the author. Provided "as is", without warranty of any kind.

// The per-company timeline strip: dots on one axis, sized by share count.
// Geometry is asserted numerically because the visual bug it guards against —
// a dot clipped at the edge, or every dot collapsing to the same size — is easy
// to reintroduce and hard to notice by eye.

import { strict as assert } from "node:assert";
import test from "node:test";

import { loadUi } from "./harness.mjs";

const { ctx } = loadUi();
const { svgTimeline, renderCharts } = ctx;

const H = 20;
const MID = H / 2;
const W = 600;

const txn = (date, shares, side = "buy") => ({ date, shares, side });

function circles(svg) {
  return [...svg.matchAll(/<circle class="tl-(\w+)" cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"\/>/g)].map(
    (m) => ({ side: m[1], cx: +m[2], cy: +m[3], r: +m[4] }),
  );
}

test("uses a 600x20 viewBox — one thin strip", () => {
  const svg = svgTimeline([txn("2026-06-01", 100)]);
  assert.match(svg, /viewBox="0 0 600 20"/);
});

test("carries no text labels at all", () => {
  const svg = svgTimeline([txn("2026-06-01", 100), txn("2026-06-10", 900, "sell")]);
  assert.ok(!svg.includes("<text"), "no axis or date labels belong on the strip");
});

test("emits no stems — dots only", () => {
  const svg = svgTimeline([txn("2026-06-01", 100), txn("2026-06-10", 900)]);
  assert.equal((svg.match(/<line/g) || []).length, 1, "only the axis line");
  assert.match(svg, /<line class="axis"/);
});

test("the axis spans the full width", () => {
  const svg = svgTimeline([txn("2026-06-01", 100)]);
  assert.match(svg, /<line class="axis" x1="0" y1="10" x2="600" y2="10"\/>/);
});

test("every dot sits centred on the axis", () => {
  const svg = svgTimeline([
    txn("2026-06-01", 100),
    txn("2026-06-05", 5000, "sell"),
    txn("2026-06-09", 250),
  ]);
  const cs = circles(svg);
  assert.equal(cs.length, 3);
  assert.ok(cs.every((c) => c.cy === MID), "a dot off the axis means the strip grew a second row");
});

test("no dot is clipped by the viewBox, horizontally or vertically", () => {
  const svg = svgTimeline([
    txn("2026-06-01", 1_000_000),
    txn("2026-06-30", 1_000_000, "sell"),
    txn("2026-06-15", 1),
  ]);
  for (const c of circles(svg)) {
    assert.ok(c.cx - c.r >= 0, `left clip at cx=${c.cx} r=${c.r}`);
    assert.ok(c.cx + c.r <= W, `right clip at cx=${c.cx} r=${c.r}`);
    assert.ok(c.cy - c.r >= 0 && c.cy + c.r <= H, `vertical clip at r=${c.r}`);
  }
});

test("buys and sells get distinct classes", () => {
  const cs = circles(svgTimeline([txn("2026-06-01", 10), txn("2026-06-02", 10, "sell")]));
  assert.deepEqual(
    cs.map((c) => c.side),
    ["buy", "sell"],
  );
});

test("radius grows with share count", () => {
  const cs = circles(
    svgTimeline([txn("2026-06-01", 1), txn("2026-06-02", 500), txn("2026-06-03", 1000)]),
  );
  assert.ok(cs[0].r < cs[1].r && cs[1].r < cs[2].r, `expected increasing radii, got ${cs.map((c) => c.r)}`);
});

test("the largest trade gets the biggest dot and the scale is bounded", () => {
  const cs = circles(svgTimeline([txn("2026-06-01", 5), txn("2026-06-02", 10_000_000)]));
  assert.ok(cs[1].r <= 6.01, "radius must stay within the strip");
  assert.ok(cs[0].r >= 1.5, "a small trade must still be visible");
});

test("area scales with shares, so one whale does not erase everything else", () => {
  // A trade at 1% of the max: linear-radius scaling would render it at
  // 1.6 + 0.01*4.4 ≈ 1.64 — indistinguishable from the 1.6 floor. sqrt scaling
  // gives 1.6 + 0.1*4.4 ≈ 2.0, a visibly larger dot.
  const FLOOR = 1.6;
  const cs = circles(svgTimeline([txn("2026-06-01", 100), txn("2026-06-02", 10_000)]));
  assert.ok(
    cs[0].r >= FLOOR * 1.25,
    `1%-of-max trade collapsed to r=${cs[0].r}; sqrt scaling should lift it clear of the ${FLOOR} floor`,
  );
});

test("dots are ordered across the strip by date", () => {
  const cs = circles(
    svgTimeline([txn("2026-06-01", 10), txn("2026-06-15", 10), txn("2026-06-30", 10)]),
  );
  assert.ok(cs[0].cx < cs[1].cx && cs[1].cx < cs[2].cx);
});

test("a single date does not divide by zero", () => {
  const cs = circles(svgTimeline([txn("2026-06-01", 10), txn("2026-06-01", 20, "sell")]));
  assert.equal(cs.length, 2);
  for (const c of cs) assert.ok(Number.isFinite(c.cx) && Number.isFinite(c.r));
});

test("zero-share trades do not produce NaN geometry", () => {
  const cs = circles(svgTimeline([txn("2026-06-01", 0), txn("2026-06-02", 0)]));
  for (const c of cs) assert.ok(Number.isFinite(c.cx) && Number.isFinite(c.r));
});

test("renderCharts says so plainly when there is nothing to plot", () => {
  const html = renderCharts([{ side: "buy", date: null, shares: 0 }]);
  assert.match(html, /class="empty"/);
  assert.ok(!html.includes("<svg"));
});

test("renderCharts skips rows that are not dated buys or sells", () => {
  const html = renderCharts([
    { side: "buy", date: "2026-06-01", shares: 100 },
    { side: "other", date: "2026-06-02", shares: 100 },
    { side: "sell", date: null, shares: 100 },
    { side: "sell", date: "2026-06-03", shares: 0 },
  ]);
  assert.equal(circles(html).length, 1);
});

test("the chart header no longer claims an above/below layout", () => {
  const html = renderCharts([{ side: "buy", date: "2026-06-01", shares: 100 }]);
  assert.ok(!/above|below/i.test(html), "stems are gone; the caption must not describe them");
  assert.match(html, /dot size = shares/);
});
