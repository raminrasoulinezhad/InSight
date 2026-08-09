# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Opening InSight at login.

Each platform is exercised by pretending to be it — sys.platform and the home
directory are both redirected, so the Linux, macOS and Windows entries are all
covered wherever the tests happen to run. Nothing here touches the real
autostart directories.
"""

from __future__ import annotations

import plistlib
import shlex
import sys
from pathlib import Path

import pytest

from insight import autostart

PLATFORMS = ["linux", "darwin", "win32"]


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A throwaway HOME, with the real autostart locations out of reach."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData" / "Roaming"))
    monkeypatch.setattr(autostart.shutil, "which", lambda _: "/usr/local/bin/insight")
    # launchctl must never actually run during a test
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)
    return tmp_path


def as_platform(monkeypatch, name: str) -> None:
    monkeypatch.setattr(sys, "platform", name)


class TestEntryLocation:
    @pytest.mark.parametrize(
        ("platform", "parts"),
        [
            ("linux", (".config", "autostart", "insight.desktop")),
            ("darwin", ("Library", "LaunchAgents", f"{autostart.MAC_LABEL}.plist")),
            ("win32", ("Startup", "InSight.cmd")),
        ],
    )
    def test_uses_the_convention_the_platform_looks_for(
        self, home: Path, monkeypatch, platform, parts
    ):
        as_platform(monkeypatch, platform)
        path = autostart.entry_path()
        for part in parts:
            assert part in str(path), f"{part} missing from {path}"

    def test_every_entry_lives_under_the_user_home(self, home: Path, monkeypatch):
        # Never a system-wide daemon: a per-user file needs no admin rights and
        # can be deleted by the person it affects.
        for platform in PLATFORMS:
            as_platform(monkeypatch, platform)
            assert home in autostart.entry_path().parents

    def test_an_unknown_platform_is_reported_not_guessed(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "sunos5")
        with pytest.raises(autostart.UnsupportedPlatform):
            autostart.entry_path()
        assert autostart.is_enabled() is False
        assert autostart.status()["supported"] is False


class TestEnableDisable:
    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_round_trip(self, home: Path, monkeypatch, platform):
        as_platform(monkeypatch, platform)
        assert autostart.is_enabled() is False

        ok, msg = autostart.enable()
        assert ok, msg
        assert autostart.is_enabled() is True
        assert autostart.entry_path().exists()

        ok, _ = autostart.disable()
        assert ok
        assert autostart.is_enabled() is False
        assert not autostart.entry_path().exists()

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_enabling_twice_is_harmless(self, home: Path, monkeypatch, platform):
        as_platform(monkeypatch, platform)
        autostart.enable()
        first = autostart.entry_path().read_text(encoding="utf-8")
        autostart.enable()
        assert autostart.entry_path().read_text(encoding="utf-8") == first

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_disabling_when_off_is_not_an_error(self, home: Path, monkeypatch, platform):
        as_platform(monkeypatch, platform)
        ok, msg = autostart.disable()
        assert ok and "not" in msg.lower()

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_the_entry_launches_the_app_window(self, home: Path, monkeypatch, platform):
        as_platform(monkeypatch, platform)
        autostart.enable()
        body = autostart.entry_path().read_text(encoding="utf-8")
        assert "insight" in body
        assert "--window" in body, "the point is that the app opens, not just the server"

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_the_command_is_an_absolute_path(self, home: Path, monkeypatch, platform):
        # A login shell often has a different PATH than the terminal the user
        # enabled this from; a bare name is how autostart entries silently do
        # nothing.
        as_platform(monkeypatch, platform)
        autostart.enable()
        assert "/usr/local/bin/insight" in autostart.entry_path().read_text(encoding="utf-8")

    def test_falls_back_when_the_console_script_is_missing(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        monkeypatch.setattr(autostart.shutil, "which", lambda _: None)
        autostart.enable()
        body = autostart.entry_path().read_text(encoding="utf-8")
        assert "-m insight.app" in body, "a source checkout should still get something runnable"


class TestFileContents:
    def test_the_linux_entry_is_a_valid_desktop_file(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        autostart.enable()
        body = autostart.entry_path().read_text(encoding="utf-8")
        assert body.startswith("[Desktop Entry]")
        keys = dict(line.split("=", 1) for line in body.splitlines()[1:] if "=" in line)
        assert keys["Type"] == "Application"
        assert keys["Terminal"] == "false"
        assert keys["Name"] == "InSight"

    def test_the_macos_entry_is_a_valid_plist_that_runs_at_load(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "darwin")
        autostart.enable()
        with autostart.entry_path().open("rb") as fh:
            plist = plistlib.load(fh)  # raises if the XML is malformed
        assert plist["Label"] == autostart.MAC_LABEL
        assert plist["RunAtLoad"] is True
        assert plist["ProgramArguments"][-1] == "--window"
        # Not a daemon: closing the window must not resurrect it.
        assert "KeepAlive" not in plist

    def test_the_plist_filename_matches_its_label(self, home: Path, monkeypatch):
        # launchd ignores an agent whose filename and Label disagree.
        as_platform(monkeypatch, "darwin")
        assert autostart.entry_path().stem == autostart.MAC_LABEL

    def test_the_windows_entry_is_a_batch_file_that_does_not_linger(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "win32")
        autostart.enable()
        # read as bytes: read_text would normalise the line endings away, and the
        # exact bytes are the thing under test
        raw = autostart.entry_path().read_bytes()
        assert raw.startswith(b"@echo off")
        assert b"start " in raw, "without start, a console window stays open for the session"
        assert b"\r\n" in raw, "cmd wants CRLF"
        assert b"\r\r\n" not in raw, "newline translation must not double the CR"

    def test_windows_without_appdata_is_reported(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        with pytest.raises(autostart.UnsupportedPlatform):
            autostart.entry_path()


class TestPathsWithSpaces:
    """A home directory with a space is ordinary on macOS and Windows.

    Every one of these formats treats a bare space as an argument separator, so
    an unquoted path produced an entry that silently never launched — the
    checkbox said enabled and nothing happened at login.
    """

    SPACED = "/Users/jo smith/.local/bin/insight"

    @pytest.fixture(autouse=True)
    def _spaced(self, home, monkeypatch):
        # depends on `home` so it runs after it — otherwise that fixture's own
        # `which` patch would win and the spaced path never reach the code
        monkeypatch.setattr(autostart.shutil, "which", lambda _: self.SPACED)

    def test_the_linux_exec_quotes_the_path(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "linux")
        autostart.enable()
        exec_line = next(
            line
            for line in autostart.entry_path().read_text(encoding="utf-8").splitlines()
            if line.startswith("Exec=")
        )
        assert exec_line == f'Exec="{self.SPACED}" --window'

    def test_the_macos_argv_keeps_the_path_in_one_piece(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "darwin")
        autostart.enable()
        with autostart.entry_path().open("rb") as fh:
            plist = plistlib.load(fh)
        assert plist["ProgramArguments"] == [self.SPACED, "--window"]

    def test_the_windows_command_quotes_the_path(self, home: Path, monkeypatch):
        as_platform(monkeypatch, "win32")
        autostart.enable()
        body = autostart.entry_path().read_text(encoding="utf-8")
        assert f'"{self.SPACED}"' in body

    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_the_reported_command_round_trips_through_a_shell_split(
        self, home: Path, monkeypatch, platform
    ):
        # What the Startup page shows must still name one real program.
        as_platform(monkeypatch, platform)
        assert shlex.split(str(autostart.status()["command"]))[0] == self.SPACED

    def test_xml_special_characters_in_a_path_are_escaped(self, home: Path, monkeypatch):
        # plistlib handles this; hand-written XML silently would not.
        as_platform(monkeypatch, "darwin")
        monkeypatch.setattr(autostart.shutil, "which", lambda _: "/home/a&b/<x>/insight")
        autostart.enable()
        with autostart.entry_path().open("rb") as fh:
            plist = plistlib.load(fh)
        assert plist["ProgramArguments"][0] == "/home/a&b/<x>/insight"


class TestStatus:
    @pytest.mark.parametrize("platform", PLATFORMS)
    def test_reports_what_the_ui_needs(self, home: Path, monkeypatch, platform):
        as_platform(monkeypatch, platform)
        off = autostart.status()
        assert off["supported"] is True
        assert off["enabled"] is False
        assert off["path"] and off["command"]

        autostart.enable()
        on = autostart.status()
        assert on["enabled"] is True
        assert on["path"] == off["path"], "the path should not move when it is switched on"

    def test_a_deleted_file_reads_as_disabled(self, home: Path, monkeypatch):
        # Deleting the file by hand is a supported way to turn this off.
        as_platform(monkeypatch, "linux")
        autostart.enable()
        autostart.entry_path().unlink()
        assert autostart.status()["enabled"] is False
