#!/usr/bin/env bash
# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

# Install (or remove) InSight as a desktop application — an icon in the app grid
# that launches the chromeless window via `uv run --script app.py --window`.
#
#   ./install-desktop.sh              # install / update
#   ./install-desktop.sh --uninstall  # remove
#
# Idempotent: re-run after moving the repo to refresh the absolute paths.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DEST="$APPS/insight.desktop"

refresh_db() {
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DEST"
  refresh_db
  echo "Removed: $DEST"
  exit 0
fi

UV="$(command -v uv || true)"
if [ -z "$UV" ]; then
  echo "uv not found on PATH. Install it: https://docs.astral.sh/uv/" >&2
  exit 1
fi

mkdir -p "$APPS"
sed -e "s|__UV__|$UV|g" -e "s|__REPO__|$REPO|g" "$REPO/insight.desktop" >"$DEST"
chmod +x "$DEST"
refresh_db

echo "Installed: $DEST"
echo "InSight should now appear in your application menu."
