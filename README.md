<p align="center">
  <img src="assets/logo.png" alt="InSight — Uncovering Insider Intelligence" width="360" />
</p>

# InSight

See what **company insiders** — directors, officers, big shareholders, and the
companies themselves — are **buying and selling** across a watchlist of Canadian
and US stocks. InSight collects the trades and shows them in a clean desktop app.

---

## 1. Install

**Step 1 — install `uv`** (a small, free tool that sets everything up for you).
Copy‑paste one line into your terminal:

- **macOS / Linux**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows** (PowerShell)
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Close and reopen your terminal afterwards.

**Step 2 — install InSight** (same command on every OS):

```bash
uv tool install git+https://github.com/raminrasoulinezhad/InSight
```

**Step 3 — one‑time browser download** (needed to fetch fresh data):

```bash
uv tool run --from playwright playwright install chromium
```

That's it. You now have two commands: **`insight`** (the app) and
**`insight-scrape`** (the data collector).

> **Updating later:** `uv tool upgrade insight`
> **Uninstalling:** `uv tool uninstall insight`

---

## 2. Use it

**Open the app:**

```bash
insight
```

Your browser opens to InSight. Prefer a real app window? Use:

```bash
insight --window
```

**Get the latest data.** The first time (and whenever you want fresh numbers),
either click **↻ Refresh data** in the app, or run:

```bash
insight-scrape
```

Then reload the app to see the update.

### What you can do in the app

- **Search** — filter by company, ticker, or insider name.
- **Filter** — show only net buyers, net sellers, or institutions.
- **Time range** — show trades from the last month, 3 months, 6 months, year,
  or 2 years (the totals update to match).
- **Add a company** — type a name in the *“Add a company…”* box, pick the right
  match, and it joins your watchlist.
- **Remove a company** — click **✕ Remove** on any company.
- **Refresh** — click **↻ Refresh data** to re‑fetch the newest trades.

Each company shows one card per insider: how much they bought vs. sold, share
counts, dollar amounts, and the latest trade date. A purple **Buyback** badge
means the company is buying its own shares.

---

## 3. Desktop shortcut (optional)

Want InSight in your applications menu with an icon?

- **Linux** — from a clone of this project, run:
  ```bash
  ./install-desktop.sh
  ```
- **macOS / Windows** — just run `insight --window`, or create a shortcut that
  points to it.

---

## Where is my data?

Your watchlist and the collected trades are stored in a personal folder, so
nothing is lost if you move or delete the project:

| OS | Folder |
|---|---|
| Linux | `~/.local/share/InSight` |
| macOS | `~/Library/Application Support/InSight` |
| Windows | `%LOCALAPPDATA%\InSight` |

---

## Good to know

- Data comes from **MarketBeat**; large caps are well covered, but some very
  small TSX‑Venture names may not be. Coverage gaps show as empty cards.
- Numbers may lag the official source by a day or two — fine for spotting trends,
  not for split‑second decisions.

---

*Developers:* build, architecture, data sources, and internals are in
**[DEVELOPER_README.md](DEVELOPER_README.md)**.
