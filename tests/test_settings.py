# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""App preferences: validation, round-trip, and staying in step with the UI."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from insight import settings

INDEX_HTML = Path(__file__).parent.parent / "insight" / "webui" / "index.html"


class TestLoad:
    def test_missing_file_gives_the_default_theme(self, tmp_path: Path):
        assert settings.load_settings(tmp_path / "nope.json") == {"theme": settings.DEFAULT_THEME}

    def test_corrupt_file_gives_defaults_instead_of_raising(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        p.write_text("{not json", encoding="utf-8")
        assert settings.load_settings(p)["theme"] == settings.DEFAULT_THEME

    def test_a_non_object_file_gives_defaults(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        assert settings.load_settings(p)["theme"] == settings.DEFAULT_THEME

    def test_an_unknown_stored_theme_is_ignored(self, tmp_path: Path):
        # e.g. a theme removed in a later version — fall back, don't render blank.
        p = tmp_path / "settings.json"
        p.write_text(json.dumps({"theme": "retired-theme"}), encoding="utf-8")
        assert settings.load_settings(p)["theme"] == settings.DEFAULT_THEME


class TestSave:
    def test_round_trip(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        saved, _ = settings.save_settings(p, {"theme": "canadian"})
        assert saved
        assert settings.load_settings(p)["theme"] == "canadian"

    def test_every_offered_theme_is_accepted(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        for theme in settings.THEMES:
            saved, msg = settings.save_settings(p, {"theme": theme})
            assert saved, f"{theme} rejected: {msg}"
            assert settings.load_settings(p)["theme"] == theme

    def test_unknown_theme_is_rejected_and_changes_nothing(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        settings.save_settings(p, {"theme": "light"})
        saved, msg = settings.save_settings(p, {"theme": "neon"})
        assert not saved
        assert "neon" in msg
        assert settings.load_settings(p)["theme"] == "light", "a bad write must not clobber"

    def test_a_non_object_body_is_rejected(self, tmp_path: Path):
        saved, _ = settings.save_settings(tmp_path / "settings.json", ["theme"])
        assert not saved

    def test_an_empty_update_keeps_the_current_theme(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        settings.save_settings(p, {"theme": "midnight"})
        saved, _ = settings.save_settings(p, {})
        assert saved
        assert settings.load_settings(p)["theme"] == "midnight"

    def test_no_temp_file_is_left_behind(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        settings.save_settings(p, {"theme": "terminal"})
        assert [f.name for f in tmp_path.iterdir()] == ["settings.json"]

    def test_concurrent_saves_leave_valid_json(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        themes = list(settings.THEMES) * 6
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda t: settings.save_settings(p, {"theme": t}), themes))
        assert settings.load_settings(p)["theme"] in settings.THEMES
        json.loads(p.read_text(encoding="utf-8"))


class TestStaysInStepWithTheUI:
    """The theme list exists in two places; neither is allowed to drift."""

    def _ui_theme_ids(self) -> set[str]:
        # Scoped to <style>: prose and comments elsewhere in the file mention the
        # same selector pattern, and only the stylesheet actually defines a theme.
        html = INDEX_HTML.read_text(encoding="utf-8")
        style = re.search(r"<style>(.*?)</style>", html, re.S).group(1)
        return set(re.findall(r'\[data-theme="([a-z]+)"\]\s*\{', style))

    def test_backend_themes_match_the_stylesheet(self):
        assert set(settings.THEMES) == self._ui_theme_ids(), (
            "insight/settings.py THEMES and the [data-theme=…] blocks in "
            "webui/index.html have diverged — the backend would reject a theme "
            "the picker offers, or accept one that renders as default."
        )

    def test_the_default_theme_is_one_of_them(self):
        assert settings.DEFAULT_THEME in settings.THEMES

    def test_the_javascript_default_matches_the_backend(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        js_default = re.search(r'const DEFAULT_THEME = "([a-z]+)"', html).group(1)
        assert js_default == settings.DEFAULT_THEME
