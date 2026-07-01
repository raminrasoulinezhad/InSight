#!/usr/bin/env bash
# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

# Install (or remove) InSight as a Linux desktop application — an icon in the
# app grid that launches the chromeless window via the installed `insight`
# command. Run `uv tool install .` (from this repo) first so `insight` is on
# PATH; the launcher then survives even if this repo is deleted.
#
#   ./install-desktop.sh              # install / update
#   ./install-desktop.sh --uninstall  # remove
#
# Idempotent: re-run to refresh paths.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons"
DEST="$APPS/insight.desktop"
ICON="$ICONS/InSight.png"

refresh_db() {
  command -v update-desktop-database >/dev/null 2>&1 &&
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$DEST" "$ICON"
  refresh_db
  echo "Removed: $DEST"
  exit 0
fi

INSIGHT="$(command -v insight || true)"
if [ -z "$INSIGHT" ]; then
  echo "The 'insight' command was not found on PATH." >&2
  echo "Install it first, e.g.:  uv tool install \"$REPO\"" >&2
  exit 1
fi

# Copy the icon somewhere stable so the launcher survives repo deletion.
mkdir -p "$ICONS"
cp "$REPO/insight/webui/icon-512.png" "$ICON"

mkdir -p "$APPS"
sed -e "s|__INSIGHT__|$INSIGHT|g" -e "s|__ICON__|$ICON|g" "$REPO/insight.desktop" >"$DEST"
chmod +x "$DEST"
refresh_db

echo "Installed: $DEST"
echo "InSight should now appear in your application menu."
