# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Scraper parsing + cache/delisted helpers (insight.marketbeat), no network."""

from insight.marketbeat import (
    MarketBeatScraper,
    _cache_path,
    _delete_cache,
    _extract_tickers,
    _load_delisted,
    _no_insider_page_kind,
    _read_cache,
    _save_delisted,
    _write_cache,
)
from insight.models import InsiderTransaction


class TestRowToRecord:
    def test_valid_row(self):
        cells = ["3/24/2026", "John Smith|Director", "Buy", "1,000", "C$10.00", "C$10,000.00", "x"]
        r = MarketBeatScraper._row_to_record(cells, "Barrick", "TSE", "ABX", "http://u", "now")
        assert r.insider_name == "John Smith"
        assert r.insider_role == "Director"
        assert r.transaction_type == "Buy"
        assert r.shares == 1000
        assert r.avg_price == 10.0
        assert r.total_value == 10000.0
        assert r.currency == "CAD"
        assert r.exchange == "TSE"
        assert r.ticker == "ABX"

    def test_header_row_returns_none(self):
        cells = ["Date", "Name", "Type", "Shares", "Price", "Total", "x"]
        assert MarketBeatScraper._row_to_record(cells, "I", "TSE", "ABX", "u", "n") is None

    def test_short_row_returns_none(self):
        assert (
            MarketBeatScraper._row_to_record(["3/1/2026", "A|B"], "I", "TSE", "X", "u", "n") is None
        )

    def test_name_without_role(self):
        cells = ["3/1/2026", "Solo Name", "Sell", "5", "$1.00", "$5.00", ""]
        r = MarketBeatScraper._row_to_record(cells, "I", "TSE", "X", "u", "n")
        assert r.insider_name == "Solo Name"
        assert r.insider_role == ""


class TestIssuerFromTitle:
    def test_extracts_before_paren(self):
        title = "Franco-Nevada (FNV) Insider Trading Activity 2026"
        assert MarketBeatScraper._issuer_from_title(title) == "Franco-Nevada"

    def test_no_paren(self):
        assert MarketBeatScraper._issuer_from_title("No parens here") == ""


class TestPickInsiderTable:
    def test_picks_insider_header(self):
        tables = [
            {"head": ["Foo", "Bar"], "rows": [["a", "b"]]},
            {"head": ["Insider Name", "Shares", "Type"], "rows": [["r1"], ["r2"]]},
        ]
        assert MarketBeatScraper._pick_insider_table(tables) == [["r1"], ["r2"]]

    def test_fallback_to_wide_table(self):
        tables = [{"head": ["a", "b"], "rows": [["1", "2", "3", "4", "5", "6"]]}]
        assert MarketBeatScraper._pick_insider_table(tables) == [["1", "2", "3", "4", "5", "6"]]

    def test_none_match(self):
        assert MarketBeatScraper._pick_insider_table([{"head": ["a"], "rows": []}]) == []


class TestExtractTickers:
    HTML = """
      <a href="/stocks/TSE/ABX/">Barrick</a>
      <a href="/stocks/TSE/FNV/insider-trades/">Franco</a>
      <a href="/stocks/TSE/ABX/">dup</a>
      <a href="/stocks/NASDAQ/AAPL/">Apple</a>
    """

    def test_only_requested_exchange_sorted_unique(self):
        assert _extract_tickers(self.HTML, "TSE") == ["ABX", "FNV"]

    def test_other_exchange(self):
        assert _extract_tickers(self.HTML, "NASDAQ") == ["AAPL"]

    def test_empty_html(self):
        assert _extract_tickers("", "TSE") == []


class TestNoInsiderPageKind:
    def test_delisted_profile_kept(self):
        assert (
            _no_insider_page_kind("https://www.marketbeat.com/stocks/TSE/IPL/", "TSE") == "delisted"
        )

    def test_delisted_dashed_ticker(self):
        assert (
            _no_insider_page_kind("https://www.marketbeat.com/stocks/TSE/CUF-UN/", "TSE")
            == "delisted"
        )

    def test_unknown_redirects_to_exchange_list(self):
        assert _no_insider_page_kind("https://www.marketbeat.com/stocks/TSE/", "TSE") == "nodata"

    def test_empty_url(self):
        assert _no_insider_page_kind("", "TSE") == "nodata"


class TestUrlFor:
    def test_builds_insider_trades_url(self):
        assert (
            MarketBeatScraper.url_for("tse", "abx")
            == "https://www.marketbeat.com/stocks/TSE/ABX/insider-trades/"
        )


class TestCacheRoundTrip:
    def test_write_read(self, tmp_path):
        recs = [
            InsiderTransaction(
                issuer_name="Barrick", exchange="TSE", ticker="ABX", insider_name="Jane"
            )
        ]
        _write_cache(tmp_path, "TSE:ABX", recs)
        assert _cache_path(tmp_path, "TSE:ABX").name == "TSE_ABX.json"
        entry = _read_cache(tmp_path, "TSE:ABX")
        assert entry["key"] == "TSE:ABX"
        assert len(entry["records"]) == 1
        assert entry["records"][0]["insider_name"] == "Jane"

    def test_read_missing(self, tmp_path):
        assert _read_cache(tmp_path, "TSE:NOPE") is None

    def test_delete(self, tmp_path):
        _write_cache(tmp_path, "TSE:ABX", [])
        assert _cache_path(tmp_path, "TSE:ABX").exists()
        _delete_cache(tmp_path, "TSE:ABX")
        assert not _cache_path(tmp_path, "TSE:ABX").exists()
        _delete_cache(tmp_path, "TSE:ABX")  # idempotent, no error


class TestDelistedFile:
    def test_round_trip_uppercases(self, tmp_path):
        p = tmp_path / "delisted.json"
        _save_delisted(p, {"tse:ipl", "TSE:SJR"})
        assert _load_delisted(p) == {"TSE:IPL", "TSE:SJR"}

    def test_missing_file(self, tmp_path):
        assert _load_delisted(tmp_path / "nope.json") == set()

    def test_corrupt_file(self, tmp_path):
        p = tmp_path / "delisted.json"
        p.write_text("not json")
        assert _load_delisted(p) == set()

    def test_none_path_is_noop(self):
        _save_delisted(None, {"TSE:ABX"})  # must not raise
        assert _load_delisted(None) == set()
