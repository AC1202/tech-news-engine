"""
Failure email notifier — called by the GitHub Actions workflows (`if: failure()`).

Usage:  python notify_failure.py "<Engine name>" [run.log]

Env vars:
  SMTP_USER          Gmail address used to send (e.g. you@gmail.com)
  SMTP_APP_PASSWORD  Gmail App Password (not the account password)
  NOTIFY_EMAIL_TO    Recipient (defaults to SMTP_USER)
  STEP_OUTCOMES      Optional, e.g. "install=success run=failure commit=skipped"
  RUN_URL            Optional, link to the Actions run
"""
import os
import re
import sys
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_REASON_LINES = 25

# Lines from the engine log worth surfacing as the failure reason
REASON_PATTERNS = re.compile(
    r"ERROR|❌|Traceback|Error:|Exception|Telegram attempt",
)


def extract_reasons(log_path: Path) -> list:
    if not log_path.exists():
        return []
    lines = log_path.read_text(errors="replace").splitlines()

    reasons = []
    # Uncaught exception → keep the traceback tail (last line is the actual error)
    if any(l.startswith("Traceback") for l in lines):
        start = max(i for i, l in enumerate(lines) if l.startswith("Traceback"))
        reasons.extend(lines[start:][-10:])

    for line in lines:
        if REASON_PATTERNS.search(line) and line not in reasons:
            reasons.append(line)

    # Collapse repeats (same error for every story) → one line with a count
    counts = {}
    for r in reasons:
        key = re.sub(r"^\d{4}-\d\d-\d\d \S+ ", "", r)   # drop timestamp
        key = re.sub(r"#\d+", "#N", key).strip()        # "#1", "#2" → "#N"
        counts[key] = counts.get(key, 0) + 1
    unique = [f"{k} (x{n})" if n > 1 else k for k, n in counts.items()]
    return unique[-MAX_REASON_LINES:]


def failed_steps() -> list:
    outcomes = os.environ.get("STEP_OUTCOMES", "")
    return [pair.split("=")[0] for pair in outcomes.split() if pair.endswith("=failure")]


def build_body(engine: str, reasons: list) -> str:
    now_utc = datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))

    steps = failed_steps()
    body = [
        f"Push failed: {engine}",
        "",
        f"Time: {now_et:%Y-%m-%d %H:%M %Z} ({now_utc:%H:%M} UTC)",
        f"Failed step: {', '.join(steps) if steps else 'unknown'}",
    ]
    if os.environ.get("RUN_URL"):
        body.append(f"Run: {os.environ['RUN_URL']}")

    body += ["", "Failure reason:"]
    if reasons:
        body += [f"  {r}" for r in reasons]
    else:
        body.append("  No error captured in the engine log — check the run link above.")
    return "\n".join(body)


def main():
    engine = sys.argv[1] if len(sys.argv) > 1 else "News Engine"
    log_path = Path(sys.argv[2] if len(sys.argv) > 2 else "run.log")

    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_APP_PASSWORD")
    to = os.environ.get("NOTIFY_EMAIL_TO") or user

    body = build_body(engine, extract_reasons(log_path))
    print(body)

    if not (user and password):
        print("SMTP_USER / SMTP_APP_PASSWORD not set — email not sent.")
        sys.exit(1)

    msg = EmailMessage()
    msg["Subject"] = f"❌ {engine} push failed — {datetime.now(ZoneInfo('America/New_York')):%Y-%m-%d}"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
    print(f"Failure email sent to {to}")


if __name__ == "__main__":
    main()
