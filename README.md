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
- **Time range** — opens on the **last 2 weeks**; widen it to a month, 3 months,
  6 months, a year or 2 years whenever you want the longer view (the totals
  update to match). The short default is what keeps the app quick — a 2‑year
  window has to draw tens of thousands of rows at once.
- **Notes** — click **📝** on any company to open a note above its activity. It
  writes as a bullet list: **Enter** starts the next bullet, **Ctrl+Enter** saves,
  **Esc** discards. Notes are yours, kept per company, and survive every refresh.
- **Follow a name** — click an insider in a company's table to see everything
  *they* traded; click a company on an insider's card to open that company. The
  **← Back** button (or **Alt+←**) retraces up to 10 steps.
- **Settings ⚙** — top right, two pages. **Appearance** picks from ten themes,
  shelved as **Dark** (Dark, Midnight, Terminal, ☕ Caramel, ✦ Chic) and
  **Light** (Light, Newsprint, 🌿 Sage, 🍋 Lemon, 🍁 Canadian) — it applies
  instantly and is remembered. **Notifications** holds the email/push setup.
- **Add a company** — type a name in the *“Add a company…”* box, pick the right
  match, and it joins your watchlist.
- **Remove a company** — click **✕ Remove** on any company.
- **Refresh** — click **↻ Refresh data** to re‑fetch the newest trades.

Each company shows one card per insider: how much they bought vs. sold, share
counts, dollar amounts, and the latest trade date. A purple **Buyback** badge
means the company is buying its own shares.

---

## 3. Alerts (optional)

InSight can **notify you when a new insider trade appears** for a company or
person you care about. You set an **alarm**, and after each data refresh InSight
checks for anything new and pushes you a message. Two free channels are
supported — use either or both:

- **Email** — via Gmail (or any SMTP provider).
- **Phone push** — via **ntfy**, a free push app; alerts pop up on your phone.

### Set an alarm

In the app, click the **🔔** button on any company card or insider, then open the
**Alarms** tab to manage them — it lists what you're watching, split into
**Companies** and **Insiders**. Only trades that arrive *after* you set the alarm
will fire — your existing history won't spam you.

Setting up *how* you get told (email or push) lives in **Settings ⚙ →
Notifications** — you do that once, then forget about it.

Alarms are checked after each scrape (the daily timer, or when you click
**↻ Refresh data**).

### Option A — Email (free, via Gmail)

Email is sent over **SMTP** — the internet's standard for sending mail. You just
tell InSight which mail server to use and give it a login. With Gmail this is
free, but there's one catch: **you can't use your normal Gmail password.** Google
requires a one-time **App Password** instead.

**1. Turn on 2-Step Verification.** Go to
[myaccount.google.com](https://myaccount.google.com) → **Security** → enable
**2-Step Verification** (App Passwords aren't available without it).

**2. Create an App Password.** Go straight to
[myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
(the menu link is often hidden — the direct URL is easiest). Name it `InSight`
and click **Create**. Google shows a **16-character password** in a popup —
**copy it now**, you can't see it again (but you can always delete it and make a
new one).

**3. Fill in Settings ⚙ → Notifications → 📧 Email (SMTP)** panel:

| Field | Value |
|---|---|
| **Enabled** | ✅ checked |
| **SMTP host** | `smtp.gmail.com` |
| **SMTP port** | `587` |
| **Username** | your full Gmail address (e.g. `you@gmail.com`) |
| **App password** | the 16-character password from step 2 |
| **From** | your Gmail address (Gmail rewrites anything else anyway) |
| **Send to** | where you want alerts — usually the same Gmail address |

**From and Send to can be the same address** — InSight simply emails you, and it
lands in your inbox.

**4. Click Save settings, then Send test** to confirm it works.

> **Note:** free Gmail allows up to ~500 emails/day — far more than personal
> alerts will ever use. For higher volume, a provider like Brevo or Mailgun has a
> free tier, but for one person watching a watchlist, Gmail is plenty.

### Option B — Phone push (free, via ntfy)

**ntfy** delivers instant push notifications to your phone with **no account and
no credentials** — you just pick a private *topic* name and subscribe to it.

**1. Install the app.** Get **ntfy** from the
[App Store](https://apps.apple.com/us/app/ntfy/id1625396347) or
[Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy).

**2. Pick a private topic and subscribe.** In the app tap **+**, and enter a
**long, hard-to-guess** topic name — anyone who knows it can send you
notifications, so treat it like a password. For example: `insight-alerts-pick-your-own`.

**3. Fill in Settings ⚙ → Notifications → 🔔 Push (ntfy.sh)** panel:

| Field | Value |
|---|---|
| **Enabled** | ✅ checked |
| **Server** | `https://ntfy.sh` (the host only — **not** the topic) |
| **Topic** | the topic you chose, e.g. `insight-alerts-pick-your-own` |

The full push URL is just *Server* + `/` + *Topic* — so if you can push to
`ntfy.sh/insight-alerts-pick-your-own` from a browser or `curl`, put `https://ntfy.sh` in
**Server** and `insight-alerts-pick-your-own` in **Topic**. Don't paste the topic into the
Server field.

**4. Click Save settings, then Send test** — a notification should appear on your
phone within a second or two.

---

## 4. Desktop shortcut (optional)

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

Every refresh saves a dated file, and those files repeat each other heavily, so
the folder grows much faster than the actual data does. InSight keeps a single
combined copy (`data/store.json`) of everything it has ever collected, so if the
folder gets large you can safely reclaim the space:

```bash
insight-scrape --prune-snapshots
```

That deletes only the dated files already merged into the combined copy — no
trades are lost, and the app looks exactly the same afterwards.

If you used a version before this one, the folder may also hold `.csv` files and
a `by_ticker` folder. InSight no longer writes those and never reads them, so
they are safe to delete.

---

## Good to know

- Data comes from **MarketBeat**; large caps are well covered, but some very
  small TSX‑Venture names may not be. Coverage gaps show as empty cards.
- Numbers may lag the official source by a day or two — fine for spotting trends,
  not for split‑second decisions.

---

*Developers:* build, architecture, data sources, and internals are in
**[DEVELOPER_README.md](DEVELOPER_README.md)**.
