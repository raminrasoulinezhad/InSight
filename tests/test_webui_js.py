# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: Apache-2.0
# Licensed under the Apache License, Version 2.0. You may obtain a copy at
# http://www.apache.org/licenses/LICENSE-2.0. Provided "AS IS", WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.

"""Bridge the browser-UI tests into pytest.

All of the app's interaction logic lives in `insight/webui/index.html`, so it
needs real coverage — but the page is deliberately a single self-contained file
with no build step and no frontend dependencies. The tests under `tests/webui/`
therefore run on Node's built-in test runner (no npm install, nothing added to
the lockfile) against the actual shipped file.

Running them from pytest keeps `uv run pytest` the single command that tells you
whether the app is healthy. Node is not a hard requirement for working on the
Python side, so this skips (loudly) when it is missing rather than failing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

WEBUI_TESTS = Path(__file__).parent / "webui"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed — UI tests skipped")
def test_webui_javascript_suite():
    proc = subprocess.run(
        ["node", "--test", str(WEBUI_TESTS)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        pytest.fail(f"webui JS tests failed:\n{proc.stdout}\n{proc.stderr}")
