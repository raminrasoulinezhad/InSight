<p align="center">
  <img src="assets/logo.png" alt="InSight — Uncovering Insider Intelligence" width="360" />
</p>

# InSight

See what **company insiders** — directors, officers, big shareholders, and the
companies themselves — are **buying and selling** across a watchlist of Canadian
and US stocks.

Everything runs on your own machine. No account, no cloud, no subscription.

---

## 1. Install

**Step 1 — install `uv`**, a small free tool that sets everything up. One line:

- **macOS / Linux**
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Windows** (PowerShell)
  ```powershell
  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
  ```

Close and reopen your terminal afterwards.

**Step 2 — install InSight.** Same command on every OS:

```bash
uv tool install git+https://github.com/raminrasoulinezhad/InSight
```

**Step 3 — download the browser** it uses to fetch data (once):

```bash
uv tool run --from playwright playwright install chromium
```

You now have two commands:

- **`insight`** — the app
- **`insight-scrape`** — the data collector

Later: `uv tool upgrade insight` to update, `uv tool uninstall insight` to remove.

---

## 2. Use it

```bash
insight            # opens in your browser
insight --window   # opens as its own app window
```

To get fresh numbers, click **↻ Refresh data** in the app (or run
`insight-scrape`, then reload).

### What you can do

- **Search** — filter by company, ticker, or insider name.
- **Filter** — show only net buyers, net sellers, or institutions.
- **Time range** — opens on the **last 2 weeks**; widen it up to 2 years. Short
  is the default because it keeps the app instant.
- **Notes 📝** — keep your own notes on any company, shown above its activity.
  Writes as a bullet list: **Enter** for the next bullet, **Ctrl+Enter** to save,
  **Esc** to discard.
- **Follow a name** — click an insider to see everything *they* traded; click a
  company on their card to jump there. **← Back** (or **Alt+←**) retraces up to
  10 steps.
- **Add a company** — type a name in *“Add a company…”* and pick the right match.
- **Remove a company** — **✕ Remove** on any company.

Each company shows one card per insider: buys vs. sells, share counts, dollar
amounts, and the latest trade date. A purple **Buyback** badge means the company
is buying its own shares.

### Settings ⚙ (top right)

| Page | What's there |
|---|---|
| **Startup** | Open InSight automatically when you log in. |
| **Appearance** | Ten themes, plus **Match my system** to follow your computer's light/dark setting. |
| **Notifications** | Email / phone-push setup for alerts (see §4). |

Themes are shelved **Dark** (Dark, Midnight, Terminal, ☕ Caramel, ✦ Chic) and
**Light** (Light, Newsprint, 🌿 Sage, 🍋 Lemon, 🍁 Canadian). With **Match my
system** ticked, pick one from each shelf and InSight switches between them by
itself.

---

## 3. Where the data comes from

InSight has two sources. **MarketBeat** is the everyday one; **SEDI** is there
for the names MarketBeat misses.

| | MarketBeat (default) | SEDI |
|---|---|---|
| **Covers** | Large and mid-cap TSX / US names | *Every* Canadian filing, incl. TSX-V and CSE micro-caps |
| **Speed** | Automatic, unattended | Opens a visible browser; may ask you to solve a CAPTCHA once |
| **Use it** | **↻ Refresh data**, or `insight-scrape` | **⛏ Fetch from SEDI**, or `insight-scrape --source sedi` |

SEDI is Canada's official insider-filing system — the authoritative source, and
the only one that sees small venture names. It is bot-protected, so it can't run
unattended:

- A real browser window opens. If a CAPTCHA appears, solve it — **once**. The
  login is remembered for later runs.
- Only Canadian companies on your watchlist are fetched.
- **A progress bar** under the toolbar tracks it, naming the company being
  fetched (*“Fetching 3 of 7 — West Red Lake Gold”*). It takes a few minutes;
  the bar is how you know it's working and not stuck.
- Where a company has a saved SEDI report, a **⛏ SEDI report** link appears on
  its card and opens the official page.

Both sources merge into the same view, so you can use either or both. Keep
MarketBeat for the daily habit and reach for SEDI when a company shows up empty.

---

## 4. Alerts (optional)

Get told when a **new** insider trade appears for a company or person you care
about.

1. Click **🔔** on any company card or insider to set an alarm.
2. Manage them on the **Alarms** tab, split into **Companies** and **Insiders**.
3. Set up *how* you're told — once — in **Settings ⚙ → Notifications**.

Only trades that arrive *after* you set the alarm will fire, so your existing
history won't spam you. Alarms are checked after each refresh.

Two free channels, use either or both:

### Email (via Gmail)

Gmail won't accept your normal password — it needs a one-time **App Password**.

1. Turn on **2-Step Verification** at
   [myaccount.google.com](https://myaccount.google.com) → **Security**.
   (App Passwords don't exist without it.)
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   name it `InSight`, click **Create**. **Copy the 16-character password now** —
   it's shown once.
3. Fill in **Settings ⚙ → Notifications → 📧 Email (SMTP)**:

   | Field | Value |
   |---|---|
   | **Enabled** | ✅ |
   | **SMTP host** | `smtp.gmail.com` |
   | **SMTP port** | `587` |
   | **Username** | your Gmail address |
   | **App password** | the 16 characters from step 2 |
   | **From** | your Gmail address |
   | **Send to** | usually the same address |

4. Click **Save settings**, then **Send test**.

> Free Gmail allows ~500 emails/day — far beyond what personal alerts need.

### Phone push (via ntfy)

No account, no credentials — just a topic name you pick.

1. Install **ntfy** from the
   [App Store](https://apps.apple.com/us/app/ntfy/id1625396347) or
   [Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy).
2. Tap **+** and enter a **long, hard-to-guess** topic — anyone who knows it can
   push to you, so treat it like a password.
3. Fill in **Settings ⚙ → Notifications → 🔔 Push (ntfy.sh)**:

   | Field | Value |
   |---|---|
   | **Enabled** | ✅ |
   | **Server** | `https://ntfy.sh` — the host only |
   | **Topic** | your topic, e.g. `insight-alerts-pick-your-own` |

4. Click **Save settings**, then **Send test**.

> The push URL is *Server* + `/` + *Topic*. Don't paste the topic into Server.

---

## 5. Desktop shortcut (optional)

- **Linux** — from a clone of this project: `./install-desktop.sh`
- **macOS / Windows** — run `insight --window`, or make a shortcut to it.

---

## Where is my data?

In a personal folder, so nothing is lost if you move or delete the project:

| OS | Folder |
|---|---|
| Linux | `~/.local/share/InSight` |
| macOS | `~/Library/Application Support/InSight` |
| Windows | `%LOCALAPPDATA%\InSight` |

**If the folder gets big**, it's safe to reclaim. Nothing below loses any trades.

```bash
insight-scrape --prune-snapshots      # delete dated files already merged
insight-scrape --prune-browser-cache  # clear the browser caches
```

- Every refresh saves a dated file, and those files repeat each other heavily.
  InSight keeps one combined copy (`data/store.json`) of everything ever
  collected, so the dated ones are safe to drop — the app looks identical after.
- InSight drives a real browser (to fetch data, and for `--window`), and browsers
  cache a *lot* — 526 MB on one machine. Close InSight first, or the profile
  still in use is skipped. Your SEDI login is kept, so no CAPTCHA to redo.
- Upgrading from an older version? Any leftover `.csv` files and `by_ticker`
  folder are no longer written or read. Delete them.

---

## Good to know

- **Coverage** — MarketBeat covers large caps well; small TSX-V names may show as
  empty cards. Use **⛏ Fetch from SEDI** for those.
- **Freshness** — numbers may lag the official source by a day or two. Good for
  spotting trends, not for split-second decisions.

---

*Developers:* build, architecture, and internals are in
**[DEVELOPER_README.md](DEVELOPER_README.md)**.
