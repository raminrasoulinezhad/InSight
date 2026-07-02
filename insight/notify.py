# Copyright (c) 2026 Seyedramin Rasoulinezhad
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Noncommercial use permitted. Commercial use requires a separate license;
# contact the author. Provided "as is", without warranty of any kind.

"""Alarms + notifications for new insider transactions.

The user sets alarms on a company ("tell me about any insider trade in ATH") or a
person ("tell me whenever Eric Sprott trades, in any company"). After each scrape
`evaluate_and_notify` finds transactions that are new *since the alarm was set*
and pushes a message over the enabled free channels:

    - Email  — SMTP (e.g. Gmail/Workspace app password), HTML with the InSight
               logo embedded via CID.
    - ntfy   — a free push topic (https://ntfy.sh/<topic>), no credentials.

State lives in a single JSON file (see paths.notify_file()): {email, ntfy, alarms}.
Each alarm carries a `seen` set of transaction keys so only genuinely new trades
fire; the baseline is captured at creation so pre-existing history never alerts.
Sends are best-effort — a failure is reported but never breaks a scrape, and an
alarm's `seen` set is only advanced once at least one channel delivered, so a
transient outage retries on the next scrape.
"""

from __future__ import annotations

import json
import smtplib
import ssl
import urllib.request
import uuid
from datetime import UTC, datetime
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

    records = load_all_records(data_dir)
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
    """A brief one-line sentence: who did what, how many shares, at what price."""
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
    return f"{who} {action} {sh}{issuer}{_price_suffix(rec)}"


def _email_html(label: str, lines: list[str]) -> str:
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
        "Open the app to review, or delete the alarm from the Alarms tab.</p>"
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


def _deliver(cfg: dict[str, Any], subject: str, lines: list[str], label: str) -> tuple[bool, str]:
    """Send one alarm's message over every enabled channel. Returns (any_ok, err).

    Kept terse: a single transaction stands on its own line; several are bulleted.
    """
    text = lines[0] if len(lines) == 1 else "\n".join("• " + x for x in lines)
    any_ok = False
    errs = []
    email_cfg = cfg.get("email", {})
    if email_cfg.get("enabled") and email_cfg.get("username") and email_cfg.get("to"):
        try:
            send_email(email_cfg, subject, _email_html(label, lines), text)
            any_ok = True
        except Exception as e:  # report, never crash a scrape
            errs.append(f"email: {type(e).__name__}: {e}")
    ntfy_cfg = cfg.get("ntfy", {})
    if ntfy_cfg.get("enabled") and ntfy_cfg.get("topic"):
        try:
            send_ntfy(ntfy_cfg, "InSight", text)
            any_ok = True
        except Exception as e:
            errs.append(f"ntfy: {type(e).__name__}: {e}")
    return any_ok, "; ".join(errs)


# ---- evaluation ---------------------------------------------------------------


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

    records = load_all_records(data_dir)
    by_key = {_key(r): r for r in records}
    sent = 0
    errors: list[str] = []
    changed = False

    for alarm in alarms:
        current = _matching_keys(alarm, records)
        seen = set(alarm.get("seen", []))
        new = current - seen
        if not new:
            continue
        recs = sorted(
            (by_key[k] for k in new if k in by_key),
            key=lambda r: r.get("transaction_date") or "",
        )
        lines = [_describe(r) for r in recs]
        label = alarm.get("label", alarm_key(alarm))
        if len(lines) == 1:
            subject = f"InSight: {lines[0]}"
        else:
            subject = f"InSight: {len(lines)} new insider trades — {label}"
        ok, err = _deliver(cfg, subject, lines, label)
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


def public_config(path: Path) -> dict[str, Any]:
    """Config for the settings UI, with the SMTP password masked."""
    cfg = load_config(path)
    email = dict(cfg["email"])
    email["password"] = _MASK if email.get("password") else ""
    return {"email": email, "ntfy": cfg["ntfy"], "alarms": cfg["alarms"]}


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
    ok, err = _deliver(cfg, "InSight: test alert — notifications are working", lines, "test")
    if ok and not err:
        return True, "Test sent."
    if ok:
        return True, f"Test sent (some channels failed: {err})"
    return False, err or "No channel enabled/configured."
