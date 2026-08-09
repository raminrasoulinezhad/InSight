# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Browser-profile housekeeping.

The load-bearing property here is what is *not* deleted: the SEDI profile's
cookies carry a solved bot-wall challenge, and losing them means solving a
CAPTCHA by hand again. Everything else in these tests is secondary to that.
"""

from __future__ import annotations

import os
from pathlib import Path

from insight import profiles


def make_profile(root: Path) -> Path:
    """A miniature Chromium profile: some caches, some real session state."""
    root.mkdir(parents=True, exist_ok=True)
    files = {
        # disposable
        "Default/Cache/data_0": "x" * 4096,
        "Default/Cache/f_00001": "x" * 8192,
        "Default/Code Cache/js/index": "x" * 2048,
        "Default/GPUCache/data_1": "x" * 1024,
        "Default/Service Worker/CacheStorage/abc/entry": "x" * 512,
        "Default/Sessions/Session_1": "x" * 256,
        "GPUPersistentCache/blob": "x" * 1024,
        "GrShaderCache/shader": "x" * 512,
        "ShaderCache/shader": "x" * 512,
        "component_crx_cache/thing.crx": "x" * 4096,
        "optimization_guide_model_store/model.tflite": "x" * 8192,
        "WasmTtsEngine/voice.wasm": "x" * 4096,
        "Safe Browsing/list.store": "x" * 2048,
        "BrowserMetrics-spare.pma": "x" * 4096,
        # must survive
        "Default/Cookies": "the solved captcha lives here",
        "Default/Network/Cookies": "and here",
        "Default/Local Storage/leveldb/000003.log": "session state",
        "Default/Preferences": '{"profile": {}}',
        "Default/Secure Preferences": "{}",
        "Local State": '{"variations": []}',
        "Default/History": "browsing history",
    }
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


class TestProtectedState:
    def test_session_state_is_never_deleted(self, tmp_path: Path):
        prof = make_profile(tmp_path / "sedi-profile")
        profiles.prune_profile(prof)
        for rel in profiles.PROTECTED:
            assert (prof / rel).exists(), f"{rel} was deleted — that is the solved CAPTCHA"

    def test_cookie_contents_are_untouched(self, tmp_path: Path):
        prof = make_profile(tmp_path / "sedi-profile")
        profiles.prune_profile(prof)
        assert (prof / "Default/Cookies").read_text() == "the solved captcha lives here"
        assert (prof / "Default/Local Storage/leveldb/000003.log").read_text() == "session state"

    def test_unlisted_directories_are_left_alone(self, tmp_path: Path):
        # The allowlist is deliberate: a future Chromium directory holding real
        # state must not be swept up.
        prof = make_profile(tmp_path / "p")
        (prof / "Default/SomeFutureState").mkdir(parents=True)
        (prof / "Default/SomeFutureState/data").write_text("important", encoding="utf-8")
        profiles.prune_profile(prof)
        assert (prof / "Default/SomeFutureState/data").read_text() == "important"
        assert (prof / "Default/History").exists()


class TestReclaim:
    def test_caches_are_removed(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        removed, freed = profiles.prune_profile(prof)
        assert not (prof / "Default/Cache").exists()
        assert not (prof / "Default/Code Cache").exists()
        assert not (prof / "optimization_guide_model_store").exists()
        assert not (prof / "BrowserMetrics-spare.pma").exists()
        assert freed > 30_000
        assert len(removed) >= 10

    def test_reports_what_it_freed(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        before = sum(p.stat().st_size for p in prof.rglob("*") if p.is_file())
        removed, freed = profiles.prune_profile(prof)
        after = sum(p.stat().st_size for p in prof.rglob("*") if p.is_file())
        assert "Default/Cache" in removed
        assert freed == before - after, "the reported figure must match what left the disk"

    def test_is_idempotent(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        profiles.prune_profile(prof)
        removed, freed = profiles.prune_profile(prof)
        assert removed == [] and freed == 0

    def test_a_missing_profile_is_not_an_error(self, tmp_path: Path):
        assert profiles.prune_profile(tmp_path / "never-created") == ([], 0)

    def test_a_profile_with_only_state_frees_nothing(self, tmp_path: Path):
        prof = tmp_path / "p"
        (prof / "Default").mkdir(parents=True)
        (prof / "Default/Cookies").write_text("c", encoding="utf-8")
        removed, freed = profiles.prune_profile(prof)
        assert removed == [] and freed == 0
        assert (prof / "Default/Cookies").exists()

    def test_a_symlink_out_of_the_profile_is_not_followed(self, tmp_path: Path):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "precious").write_text("do not delete", encoding="utf-8")
        prof = tmp_path / "p"
        (prof / "Default").mkdir(parents=True)
        (prof / "Default" / "Cache").symlink_to(outside, target_is_directory=True)
        profiles.prune_profile(prof)
        assert (outside / "precious").read_text() == "do not delete"


class TestInUseGuard:
    """Deleting cache under a live browser is asking for a confusing crash."""

    def test_a_profile_with_no_lock_is_free(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        assert profiles.in_use(prof) is False

    def test_a_lock_naming_a_live_process_is_in_use(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        (prof / "SingletonLock").symlink_to(f"thishost-{os.getpid()}")
        assert profiles.in_use(prof) is True

    def test_a_stale_lock_from_a_crash_is_ignored(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        # a pid that cannot be running: find one nothing owns
        dead = 999_999
        while True:
            try:
                os.kill(dead, 0)
            except ProcessLookupError:
                break
            except OSError:
                pass
            dead -= 1
        (prof / "SingletonLock").symlink_to(f"thishost-{dead}")
        assert profiles.in_use(prof) is False

    def test_an_unparseable_lock_errs_toward_in_use(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        (prof / "SingletonLock").symlink_to("something-unexpected")
        assert profiles.in_use(prof) is True

    def test_a_live_profile_is_not_pruned(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        (prof / "SingletonLock").symlink_to(f"thishost-{os.getpid()}")
        removed, freed = profiles.prune_profile(prof)
        assert removed == [profiles.IN_USE] and freed == 0
        assert (prof / "Default/Cache/data_0").exists(), "cache must survive a live browser"

    def test_the_guard_can_be_overridden(self, tmp_path: Path):
        prof = make_profile(tmp_path / "p")
        (prof / "SingletonLock").symlink_to(f"thishost-{os.getpid()}")
        removed, freed = profiles.prune_profile(prof, skip_if_running=False)
        assert freed > 0 and profiles.IN_USE not in removed


class TestLaunchArgs:
    def test_the_disk_cache_is_capped(self):
        args = profiles.cache_args()
        assert any(a.startswith("--disk-cache-size=") for a in args)

    def test_the_scraper_keeps_component_updates(self):
        # This profile faces a bot wall; it should look like an ordinary browser.
        assert "--disable-component-update" not in profiles.cache_args()

    def test_the_app_window_skips_component_downloads(self):
        assert "--disable-component-update" in profiles.cache_args(component_updates=False)

    def test_every_disposable_entry_is_relative(self):
        # An absolute path here would escape the profile directory entirely.
        for rel in profiles.DISPOSABLE:
            assert not Path(rel).is_absolute(), rel
            assert ".." not in rel
