# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""App preferences: validation, round-trip, and staying in step with the UI."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from insight import settings

INDEX_HTML = Path(__file__).parent.parent / "insight" / "webui" / "index.html"


class TestLoad:
    def test_missing_file_gives_the_defaults(self, tmp_path: Path):
        assert settings.load_settings(tmp_path / "nope.json") == {
            "theme": settings.DEFAULT_THEME,
            "auto": False,
            "auto_dark": settings.DEFAULT_AUTO_DARK,
            "auto_light": settings.DEFAULT_AUTO_LIGHT,
        }

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


class TestFollowSystem:
    def test_defaults_to_not_following(self, tmp_path: Path):
        s = settings.load_settings(tmp_path / "settings.json")
        assert s["auto"] is False
        assert s["auto_dark"] in settings.DARK_THEMES
        assert s["auto_light"] in settings.LIGHT_THEMES

    def test_round_trip(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        saved, _ = settings.save_settings(
            p, {"auto": True, "auto_dark": "chic", "auto_light": "sage"}
        )
        assert saved
        s = settings.load_settings(p)
        assert (s["auto"], s["auto_dark"], s["auto_light"]) == (True, "chic", "sage")

    def test_turning_auto_on_keeps_the_manual_theme(self, tmp_path: Path):
        # Otherwise turning "match my system" back off would lose the theme the
        # user had picked by hand.
        p = tmp_path / "settings.json"
        settings.save_settings(p, {"theme": "caramel"})
        settings.save_settings(p, {"auto": True})
        assert settings.load_settings(p)["theme"] == "caramel"

    def test_a_light_theme_cannot_be_the_dark_pick(self, tmp_path: Path):
        # Storing one would make the app get *brighter* when the OS goes dark.
        p = tmp_path / "settings.json"
        saved, msg = settings.save_settings(p, {"auto_dark": "lemon"})
        assert not saved and "auto_dark" in msg

    def test_a_dark_theme_cannot_be_the_light_pick(self, tmp_path: Path):
        saved, msg = settings.save_settings(tmp_path / "s.json", {"auto_light": "terminal"})
        assert not saved and "auto_light" in msg

    def test_every_theme_is_accepted_on_its_own_shelf(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        for theme in settings.DARK_THEMES:
            assert settings.save_settings(p, {"auto_dark": theme})[0], theme
        for theme in settings.LIGHT_THEMES:
            assert settings.save_settings(p, {"auto_light": theme})[0], theme

    def test_auto_must_be_a_boolean(self, tmp_path: Path):
        saved, msg = settings.save_settings(tmp_path / "s.json", {"auto": "yes"})
        assert not saved and "true or false" in msg

    def test_a_rejected_write_changes_nothing(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        settings.save_settings(p, {"auto": True, "auto_dark": "midnight"})
        settings.save_settings(p, {"auto_dark": "lemon"})  # rejected
        assert settings.load_settings(p)["auto_dark"] == "midnight"

    def test_a_corrupt_auto_pick_falls_back_to_its_shelf(self, tmp_path: Path):
        p = tmp_path / "settings.json"
        p.write_text(
            json.dumps({"auto": True, "auto_dark": "lemon", "auto_light": "terminal"}),
            encoding="utf-8",
        )
        s = settings.load_settings(p)
        assert s["auto_dark"] in settings.DARK_THEMES
        assert s["auto_light"] in settings.LIGHT_THEMES

    def test_the_shelves_partition_the_themes(self):
        assert set(settings.DARK_THEMES) | set(settings.LIGHT_THEMES) == set(settings.THEMES)
        assert not set(settings.DARK_THEMES) & set(settings.LIGHT_THEMES)


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

    def test_the_shelves_match_the_uis_modes(self):
        # The backend validates auto_dark/auto_light against these tuples while
        # the UI decides which shelf a click lands on from its own `mode`. If the
        # two disagree, a click is rejected by the server it was meant for.
        html = INDEX_HTML.read_text(encoding="utf-8")
        ui: dict[str, set[str]] = {"dark": set(), "light": set()}
        for tid, mode in re.findall(r'\{id:"(\w+)",\s*mode:"(\w+)"', html):
            ui[mode].add(tid)
        assert ui["dark"] == set(settings.DARK_THEMES)
        assert ui["light"] == set(settings.LIGHT_THEMES)
