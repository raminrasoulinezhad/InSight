# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Alarms + notifications for new insider transactions.

The user sets alarms on a company ("tell me about any insider trade in ATH") or a
person ("tell me whenever Eric Sprott trades, in any company"). After each scrape
`evaluate_and_notify` finds transactions that are new *since the alarm was set*
and pushes a message over the enabled free channels. Compensation events — option
/ rights / warrant grants and their later exercise — are *not* market trades, so
they never alert (see `_alertable`); only genuine buys/sells and the like do.

Channels:

    - Email  — SMTP (e.g. Gmail/Workspace app password), HTML with the InSight
               logo embedded via CID.
    - ntfy   — a free push topic (https://ntfy.sh/<topic>), no credentials.

State lives in a single JSON file (see paths.notify_file()): {email, ntfy, alarms}.
Each alarm carries a `seen` set of transaction keys so only genuinely new trades
fire; the baseline is captured at creation so pre-existing history never alerts.
Both the baseline and the alerting scan are bounded to ALERT_HORIZON_DAYS, which
keeps `seen` proportional to recent activity instead of to all history, and the
UI is served a projection without it (see public_config).
Sends are best-effort — a failure is reported but never breaks a scrape, and an
alarm's `seen` set is only advanced once at least one channel delivered, so a
transient outage retries on the next scrape.

Every notification generated is stamped with a monotonic index (`#N`, shown in the
message) and appended to an append-only JSONL log beside notify.json (see
paths.notify_log_file()). The log records the index, timestamp, target and the
per-channel delivery outcome so any alert — delivered or failed — can be traced
back later for debugging / issue reports.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.request
import uuid
from datetime import UTC, date, datetime, timedelta
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from importlib import resources
from pathlib import Path
from typing import Any

from .aggregate import _person_key, _txn_key, load_all_records

_LOGO = resources.files("insight").joinpath("webui", "icon.png")

_DEFAULT: dict[str, Any] = {
    "email": {
        "enabled": False,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "username": "",
        "password": "",
        "from": "",
        "to": "",
    },
    "ntfy": {"enabled": False, "server": "https://ntfy.sh", "topic": ""},
    "alarms": [],
}
_MASK = "••••••••"  # returned to the UI instead of the real SMTP password


# ---- storage ------------------------------------------------------------------


def load_config(path: Path) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text())
        except (ValueError, OSError):
            stored = {}
    # Build fresh containers (never alias _DEFAULT's dict/list) and shallow-merge
    # the settings sections so newly-added default keys appear for old files.
    return {
        "email": {**_DEFAULT["email"], **(stored.get("email") or {})},
        "ntfy": {**_DEFAULT["ntfy"], **(stored.get("ntfy") or {})},
        "alarms": list(stored.get("alarms") or []),
    }


def save_config(path: Path, cfg: dict[str, Any]) -> None:
    path.write_text(json.dumps(cfg, indent=2))


# ---- notification log ---------------------------------------------------------
# An append-only JSONL trail of every notification generated, kept beside
# notify.json. Each notification gets a monotonic index (`#N`) that shows up in
# the message and is the reference used when tracing an alert later. The next
# index is derived from the log itself so it survives restarts without a separate
# counter to keep in sync. Logging is best-effort — it must never break a scrape.


def _log_file(path: Path) -> Path:
    """The notification log that sits next to a given notify.json path."""
    return path.with_name("notifications.log")


def _next_index(log_path: Path) -> int:
    """One past the highest index recorded in the log (1 if empty/missing)."""
    last = 0
    if log_path.exists():
        try:
            for line in log_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    last = max(last, int(json.loads(line).get("index", 0)))
                except (ValueError, TypeError):
                    continue
        except OSError:
            return 1
    return last + 1


def _append_log(log_path: Path, entry: dict[str, Any]) -> None:
    """Append one notification record as a JSON line. Never raises."""
    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # the log is a debugging aid, not worth failing a scrape over


# ---- alarms -------------------------------------------------------------------


def alarm_key(alarm: dict[str, Any]) -> str:
    """Stable identity used for de-dup and for the UI to mark active bells."""
    if alarm.get("type") == "person":
        return "person:" + _person_key(alarm.get("name", ""))
    return f"company:{(alarm.get('exchange') or '').upper()}:{(alarm.get('ticker') or '').upper()}"


def _matches(alarm: dict[str, Any], rec: dict[str, Any]) -> bool:
    if alarm.get("type") == "person":
        return _person_key(rec.get("insider_name", "")) == _person_key(alarm.get("name", ""))
    return (rec.get("exchange") or "").upper() == (alarm.get("exchange") or "").upper() and (
        rec.get("ticker") or ""
    ).upper() == (alarm.get("ticker") or "").upper()


def _matching_keys(alarm: dict[str, Any], records: list[dict[str, Any]]) -> set[str]:
    return {_key(r) for r in records if _matches(alarm, r)}


def _key(rec: dict[str, Any]) -> str:
    return "|".join(str(x) for x in _txn_key(rec))


# How far back a transaction can be and still be worth an alert.
#
# This bounds the `seen` set, which is the whole point. `seen` exists to answer
# "did I already alert on this trade?", and it used to be re-baselined to an
# alarm's *entire* matching history on every fire — so it grew forever. Across
# 103 alarms that reached 28,604 keys and a 3.1 MB notify.json, re-read and
# re-written on every scrape.
#
# Restricting the records considered for alerting to this window means a key
# older than the window can never fire again, so it is safe to forget: the set
# now stays proportional to recent activity rather than to all history.
#
# The trade-off is real: a scrape that backfills a genuinely old filing (SEDI
# serves up to 24 months) will not alert on it. That is the intended behaviour —
# an alert about a trade from last year is noise, not news.
ALERT_HORIZON_DAYS = 90


def _recent(records: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Records inside the alerting horizon (see ALERT_HORIZON_DAYS)."""
    cutoff = ((today or date.today()) - timedelta(days=ALERT_HORIZON_DAYS)).isoformat()
    return [r for r in records if (r.get("transaction_date") or "") >= cutoff]


def add_alarm(path: Path, spec: dict[str, Any], data_dir: Path) -> tuple[bool, str]:
    """Create an alarm, baselining `seen` to current transactions so only trades
    that arrive *after* now will fire. De-dupes on alarm_key."""
    atype = spec.get("type")
    if atype == "person":
        name = (spec.get("name") or "").strip()
        if not name:
            return False, "missing insider name"
        alarm = {"type": "person", "name": name, "label": spec.get("label") or name}
    elif atype == "company":
        exch = (spec.get("exchange") or "").upper()
        ticker = (spec.get("ticker") or "").upper()
        if not (exch and ticker):
            return False, "missing exchange/ticker"
        alarm = {
            "type": "company",
            "exchange": exch,
            "ticker": ticker,
            "label": spec.get("label") or f"{exch}:{ticker}",
        }
    else:
        return False, "alarm type must be 'company' or 'person'"

    cfg = load_config(path)
    if any(alarm_key(a) == alarm_key(alarm) for a in cfg["alarms"]):
        return False, f"Alarm for {alarm['label']} already exists"

    # Baseline against the alerting horizon only: trades older than that can
    # never fire anyway (see ALERT_HORIZON_DAYS), so recording them would just
    # bloat the alarm from birth.
    records = _recent(load_all_records(data_dir))
    alarm["id"] = uuid.uuid4().hex[:12]
    alarm["created"] = datetime.now(UTC).isoformat(timespec="seconds")
    alarm["seen"] = sorted(_matching_keys(alarm, records))  # baseline: existing trades
    cfg["alarms"].append(alarm)
    save_config(path, cfg)
    return True, f"Alarm set for {alarm['label']}"


def remove_alarm(path: Path, alarm_id: str) -> tuple[bool, str]:
    cfg = load_config(path)
    before = len(cfg["alarms"])
    cfg["alarms"] = [a for a in cfg["alarms"] if a.get("id") != alarm_id]
    if len(cfg["alarms"]) == before:
        return False, "alarm not found"
    save_config(path, cfg)
    return True, "Alarm removed"


# ---- message building ---------------------------------------------------------


def _fmt_money(v: float | None, cur: str) -> str:
    if not v:
        return (cur or "$") + "0"
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"{v / 1e6:.2f}M"
    elif a >= 1e3:
        s = f"{v / 1e3:.1f}K"
    else:
        s = f"{v:.0f}"
    return (cur + " " if cur else "$") + s


def _num(v: float) -> str:
    """Compact number: 50.50 -> '50.5', 50.0 -> '50', 1234.5 -> '1,234.5'."""
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def _price_suffix(rec: dict[str, Any]) -> str:
    """' (~50.5 CAD each)' from avg_price, falling back to total value."""
    cur = rec.get("currency") or ""
    price = rec.get("avg_price")
    if price:
        return f" (~{_num(price)} {cur} each)" if cur else f" (~{_num(price)} each)"
    val = rec.get("total_value")
    return f" ({_fmt_money(val, cur)})" if val else ""


def _describe(rec: dict[str, Any]) -> str:
    """A brief one-line sentence: who did what, how many shares, at what price, when.

    The buy/sell date always appears — it's what distinguishes an alert.
    """
    ttype = (rec.get("transaction_type") or "").lower()
    if "buy" in ttype:
        action = "bought"
    elif "sell" in ttype:
        action = "sold"
    else:
        action = rec.get("transaction_type") or "traded"
    who = rec.get("insider_name") or "An insider"
    issuer = rec.get("issuer_name") or rec.get("ticker") or ""
    shares = rec.get("shares")
    sh = f"{shares:,} shares of " if shares else ""
    date = rec.get("transaction_date")
    when = f" on {date}" if date else ""
    return f"{who} {action} {sh}{issuer}{_price_suffix(rec)}{when}"


def _email_html(label: str, lines: list[str], index: int) -> str:
    items = "".join(f"<li style='margin:6px 0'>{_esc(x)}</li>" for x in lines)
    wrap = (
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "background:#0e1116;color:#e6edf3;padding:24px;border-radius:12px;max-width:600px"
    )
    muted = "color:#8b949e"
    return (
        f'<div style="{wrap}">'
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">'
        '<img src="cid:insightlogo" width="36" height="36" alt="InSight"'
        ' style="border-radius:8px"/>'
        '<h2 style="margin:0;font-size:18px">InSight alert</h2></div>'
        f'<p style="{muted};margin:0 0 12px">New insider activity for '
        f'<b style="color:#e6edf3">{_esc(label)}</b>:</p>'
        f'<ul style="padding-left:18px;margin:0">{items}</ul>'
        f'<p style="{muted};font-size:12px;margin-top:18px">You set this alarm in InSight. '
        "Open the app to review, or delete the alarm from the Alarms tab. "
        f"<br>Notification #{index} — quote this when reporting an issue.</p>"
        "</div>"
    )


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---- channels -----------------------------------------------------------------


def send_email(email_cfg: dict[str, Any], subject: str, html: str, text: str) -> None:
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = email_cfg.get("from") or email_cfg["username"]
    msg["To"] = email_cfg["to"]
    alt = MIMEMultipart("alternative")
    msg.attach(alt)
    alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    try:
        img = MIMEImage(_LOGO.read_bytes())
        img.add_header("Content-ID", "<insightlogo>")
        img.add_header("Content-Disposition", "inline", filename="insight.png")
        msg.attach(img)
    except (FileNotFoundError, OSError):
        pass  # logo optional
    host = email_cfg.get("smtp_host") or "smtp.gmail.com"
    port = int(email_cfg.get("smtp_port") or 587)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ctx)
        s.login(email_cfg["username"], email_cfg["password"])
        s.send_message(msg)


def send_ntfy(ntfy_cfg: dict[str, Any], title: str, message: str) -> None:
    server = (ntfy_cfg.get("server") or "https://ntfy.sh").rstrip("/")
    topic = ntfy_cfg["topic"]
    req = urllib.request.Request(
        f"{server}/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("ascii", "replace").decode("ascii"),  # header must be ASCII
            "Tags": "chart_with_upwards_trend",
            "User-Agent": "InSight",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def _title(label: str) -> str:
    """The notification title/subject: always starts with InSight and names *what*
    it fired for (a company or a person) — never a bare count of trades. The
    reference index lives in the body, not the title."""
    return f"InSight: {label}"


def _deliver(
    cfg: dict[str, Any], title: str, lines: list[str], label: str, index: int
) -> tuple[bool, str, list[dict[str, Any]]]:
    """Send one notification over every enabled channel.

    Returns (any_ok, joined_err, channels) where `channels` records the per-channel
    outcome for the notification log. Email subject and ntfy title share `title`;
    the reference index `#N` is carried in the message body (not the title).
    Kept terse: a single transaction stands on its own line; several are bulleted.
    """
    body = lines[0] if len(lines) == 1 else "\n".join("• " + x for x in lines)
    text = f"{body}\n\n#{index}"  # reference index for tracing this notification later
    any_ok = False
    errs = []
    channels: list[dict[str, Any]] = []
    email_cfg = cfg.get("email", {})
    if email_cfg.get("enabled") and email_cfg.get("username") and email_cfg.get("to"):
        try:
            send_email(email_cfg, title, _email_html(label, lines, index), text)
            any_ok = True
            channels.append({"channel": "email", "ok": True})
        except Exception as e:  # report, never crash a scrape
            errs.append(f"email: {type(e).__name__}: {e}")
            channels.append({"channel": "email", "ok": False, "error": f"{type(e).__name__}: {e}"})
    ntfy_cfg = cfg.get("ntfy", {})
    if ntfy_cfg.get("enabled") and ntfy_cfg.get("topic"):
        try:
            send_ntfy(ntfy_cfg, title, text)
            any_ok = True
            channels.append({"channel": "ntfy", "ok": True})
        except Exception as e:
            errs.append(f"ntfy: {type(e).__name__}: {e}")
            channels.append({"channel": "ntfy", "ok": False, "error": f"{type(e).__name__}: {e}"})
    return any_ok, "; ".join(errs), channels


# ---- evaluation ---------------------------------------------------------------

# Insider filings include compensation/administrative events — option, rights and
# warrant *grants* and their later *exercise* — that aren't market buys/sells. The
# user only wants alerts for genuine trades, so any transaction type mentioning a
# grant or an exercise is skipped for notification purposes. Matching on the words
# (not a fixed list) covers every SEDI variant: "Grant of options/rights/warrants",
# "Exercise of rights/options/warrants", "Exercise for cash", … and survives new
# wording. Note this affects alerts ONLY — such rows still appear in the app views.
_ALERT_SKIP_WORDS = ("grant", "exercise")


def _alertable(rec: dict[str, Any]) -> bool:
    """True unless the transaction is a grant or exercise (never worth an alert)."""
    ttype = (rec.get("transaction_type") or "").lower()
    return not any(word in ttype for word in _ALERT_SKIP_WORDS)


def evaluate_and_notify(path: Path, data_dir: Path) -> dict[str, Any]:
    """Fire any alarm whose target has transactions new since it was last seen.

    Called at the end of every scrape/refresh. Best-effort: never raises.
    """
    if not path.exists():
        return {"sent": 0, "checked": 0}
    cfg = load_config(path)
    alarms = cfg.get("alarms", [])
    if not alarms:
        return {"sent": 0, "checked": 0}

    records = _recent(load_all_records(data_dir))
    by_key = {_key(r): r for r in records}
    log_path = _log_file(path)
    index = _next_index(log_path)
    sent = 0
    errors: list[str] = []
    changed = False

    for alarm in alarms:
        current = _matching_keys(alarm, records)
        seen = set(alarm.get("seen", []))
        # Forget keys that have aged out of the horizon (or vanished from the
        # data). They can no longer appear in `current`, so they can no longer
        # fire — keeping them only grows the file. This also migrates alarms
        # created before the horizon existed, whose `seen` holds all history.
        trimmed = seen & current
        if len(trimmed) != len(seen):
            alarm["seen"] = sorted(trimmed)
            seen = trimmed
            changed = True
        new = current - seen
        if not new:
            continue
        recs = sorted(
            (by_key[k] for k in new if k in by_key),
            key=lambda r: r.get("transaction_date") or "",
        )
        recs = [r for r in recs if _alertable(r)]  # drop grants / option exercises
        if not recs:
            # Only compensation events arrived — nothing to alert, but fold them
            # into the baseline so they aren't re-examined on every future scrape.
            alarm["seen"] = sorted(current)
            changed = True
            continue
        lines = [_describe(r) for r in recs]
        label = alarm.get("label", alarm_key(alarm))
        title = _title(label)
        ok, err, channels = _deliver(cfg, title, lines, label, index)
        _append_log(
            log_path,
            {
                "index": index,
                "time": datetime.now(UTC).isoformat(timespec="seconds"),
                "kind": "alarm",
                "alarm_id": alarm.get("id"),
                "alarm_key": alarm_key(alarm),
                "label": label,
                "title": title,
                "lines": lines,
                "delivered": ok,
                "channels": channels,
            },
        )
        index += 1
        if err:
            errors.append(err)
        if ok:
            # advance the baseline only on successful delivery so failures retry
            alarm["seen"] = sorted(current)
            changed = True
            sent += 1

    if changed:
        save_config(path, cfg)
    return {"sent": sent, "checked": len(alarms), "errors": errors}


# ---- API helpers --------------------------------------------------------------


# What the UI actually needs about an alarm: enough to list it and to light up
# the right 🔔. Everything else — notably the `seen` key set — is bookkeeping.
_PUBLIC_ALARM_FIELDS = ("id", "type", "label", "name", "exchange", "ticker", "created")


def _public_alarm(alarm: dict[str, Any]) -> dict[str, Any]:
    return {k: alarm[k] for k in _PUBLIC_ALARM_FIELDS if k in alarm}


def public_config(path: Path) -> dict[str, Any]:
    """Config for the settings UI, with the SMTP password masked.

    Alarms are projected down to the fields the UI renders. They used to be sent
    verbatim, which meant every page load shipped each alarm's `seen` set — 2.8 MB
    of transaction keys the browser never looks at, against 56 KB for the actual
    insider data. A whitelist rather than dropping `seen` by name, so a future
    bookkeeping field doesn't silently start being published too.
    """
    cfg = load_config(path)
    email = dict(cfg["email"])
    email["password"] = _MASK if email.get("password") else ""
    return {
        "email": email,
        "ntfy": cfg["ntfy"],
        "alarms": [_public_alarm(a) for a in cfg["alarms"]],
    }


def save_settings(path: Path, incoming: dict[str, Any]) -> None:
    """Persist email/ntfy settings; keep the stored password if the UI sent the
    mask (so editing other fields doesn't wipe the secret)."""
    cfg = load_config(path)
    email = {**cfg["email"], **(incoming.get("email") or {})}
    if email.get("password") == _MASK:
        email["password"] = cfg["email"].get("password", "")
    cfg["email"] = email
    cfg["ntfy"] = {**cfg["ntfy"], **(incoming.get("ntfy") or {})}
    save_config(path, cfg)


def send_test(path: Path) -> tuple[bool, str]:
    cfg = load_config(path)
    lines = ["Test alert — notifications are working."]
    label = "Test"
    log_path = _log_file(path)
    index = _next_index(log_path)
    title = _title(label)
    ok, err, channels = _deliver(cfg, title, lines, label, index)
    if not channels:  # nothing configured — no notification was generated
        return False, "No channel enabled/configured."
    _append_log(
        log_path,
        {
            "index": index,
            "time": datetime.now(UTC).isoformat(timespec="seconds"),
            "kind": "test",
            "label": label,
            "title": title,
            "lines": lines,
            "delivered": ok,
            "channels": channels,
        },
    )
    if ok and not err:
        return True, f"Test sent (#{index})."
    if ok:
        return True, f"Test sent (#{index}; some channels failed: {err})"
    return False, err
