# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Where everything lives.

Two things here are worth pinning. `app_dir` branches on `sys.platform` and on
environment variables, so it is the one place a packaging change could silently
start writing to the wrong folder — and every other path hangs off it.
`sedi_page_filename` sanitizes an attacker-influenced ticker into a single path
segment; it is what stops `/api/sedi-page?ticker=...` reading arbitrary files.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from insight import paths


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for var in ("XDG_DATA_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def as_platform(monkeypatch, name: str) -> None:
    monkeypatch.setattr(sys, "platform", name)


class TestAppDir:
    @pytest.mark.parametrize(
        ("platform", "expected"),
        [
            ("linux", (".local", "share", "InSight")),
            ("darwin", ("Library", "Application Support", "InSight")),
            ("win32", ("AppData", "Local", "InSight")),
        ],
    )
    def test_uses_each_platforms_convention(self, home: Path, monkeypatch, platform, expected):
        as_platform(monkeypatch, platform)
        assert paths.app_dir().parts[-len(expected) :] == expected

    def test_linux_honours_xdg_data_home(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(home / "custom"))
        assert paths.app_dir() == home / "custom" / "InSight"

    def test_windows_honours_localappdata(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "win32")
        monkeypatch.setenv("LOCALAPPDATA", str(home / "Local"))
        assert paths.app_dir() == home / "Local" / "InSight"

    def test_the_folder_is_created_on_demand(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        assert paths.app_dir().is_dir()

    def test_everything_else_lives_under_it(self, home: Path, monkeypatch):
        # One root means "where is my data?" has a single answer, and the
        # uninstall story is deleting one folder.
        as_platform(monkeypatch, "linux")
        root = paths.app_dir()
        for getter in (
            paths.config_file,
            paths.delisted_file,
            paths.settings_file,
            paths.notes_file,
            paths.notify_file,
            paths.notify_log_file,
            paths.data_dir,
            paths.cache_dir,
            paths.chrome_profile_dir,
            paths.sedi_pages_dir,
            paths.sedi_profile_dir,
        ):
            assert root in getter().parents or getter() == root, getter.__name__

    def test_the_directories_are_created_but_the_files_are_not(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        assert paths.data_dir().is_dir()
        assert paths.cache_dir().is_dir()
        # A settings file that exists but is empty would be a corrupt-file case
        # every loader then has to tolerate; absent is cleaner.
        assert not paths.settings_file().exists()
        assert not paths.notes_file().exists()

    def test_no_two_kinds_of_state_share_a_path(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        named = [
            paths.config_file(),
            paths.delisted_file(),
            paths.settings_file(),
            paths.notes_file(),
            paths.notify_file(),
            paths.notify_log_file(),
            paths.data_dir(),
            paths.cache_dir(),
            paths.chrome_profile_dir(),
            paths.sedi_pages_dir(),
            paths.sedi_profile_dir(),
        ]
        assert len(set(named)) == len(named)


class TestSediPageFilename:
    """The ticker reaches this straight from a query string."""

    def test_the_ordinary_case(self):
        assert paths.sedi_page_filename("TSE", "ATH") == "TSE_ATH.html"

    def test_it_upper_cases(self):
        assert paths.sedi_page_filename("tse", "ath") == "TSE_ATH.html"

    @pytest.mark.parametrize(
        "ticker",
        [
            "../../../../etc/passwd",
            "..\\..\\windows\\system32",
            "a/b",
            "a\\b",
            "..",
            ".",
            "with space",
            "semi;colon",
            "null\x00byte",
            "-rf",
        ],
    )
    def test_a_hostile_ticker_stays_one_safe_segment(self, ticker: str):
        name = paths.sedi_page_filename("TSE", ticker)
        assert "/" not in name and "\\" not in name
        assert not Path(name).is_absolute()
        # The property that matters is structural, not textual: dots may survive
        # as characters ("TSE_.._.._etc_passwd.html") but never as a component,
        # so the join cannot escape the directory.
        assert Path("/base", name).parent == Path("/base")
        assert name not in (".", "..")

    def test_the_result_always_ends_in_html(self):
        assert paths.sedi_page_filename("x", "y").endswith(".html")

    def test_different_companies_get_different_files(self):
        assert paths.sedi_page_filename("TSE", "ATH") != paths.sedi_page_filename("TSX", "ATH")
