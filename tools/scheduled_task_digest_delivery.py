from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

from scheduled_task_digest_core import *  # noqa: F401,F403
from scheduled_task_digest_core import _safe_message

def _location(record: Mapping[str, Any]) -> str:
    return str(record.get("repository") or record.get("id") or record.get("source") or "unknown")


def _line(record: Mapping[str, Any]) -> str:
    status = str(record.get("status") or "unknown")
    expected, observed = record.get("expected_occurrences"), record.get("observed_runs")
    evidence = f"expected={expected} observed={observed}" if observed is not None else f"expected={expected}"
    return f"[{LABELS.get(status, status.upper())}] {record.get('name') or 'unnamed'} — {_location(record)} — {record.get('reason') or ''} ({evidence})"


def render_plain_text(digest: Mapping[str, Any]) -> str:
    summary, coverage = digest["summary"], digest["coverage"]
    lines = ["Scheduled-task digest — previous 24 hours", f"Window: {digest['window']['start']} to {digest['window']['end']}", f"Critical: {summary['critical']} | Attention: {summary['attention']} | Certified success: {summary['success']} | Not due: {summary['not_due']}", f"Coverage: {'complete' if coverage['complete'] else 'partial'}; repositories={coverage['repositories_scanned']}; scheduled workflows={coverage['scheduled_workflows']}; runs={coverage['schedule_runs']}", ""]
    alert_records = [item for item in digest["records"] if item.get("status") in CRITICAL | ATTENTION]
    lines.append("Action required / review")
    lines.extend([_line(item) for item in alert_records] or ["No critical or attention records."])
    lines.extend(["", "Certified successes"])
    successes = [item for item in digest["records"] if item.get("status") == "success"]
    lines.extend([_line(item) for item in successes[:40]] or ["No runs met the substantive-success certification rule."])
    if coverage.get("errors"):
        lines.extend(["", "Coverage errors"] + [f"- {item}" for item in coverage["errors"][:80]])
    lines.extend(["", f"Digest SHA-256: {digest['digest_sha256']}"])
    return "\n".join(lines) + "\n"


def render_html(digest: Mapping[str, Any]) -> str:
    summary, coverage = digest["summary"], digest["coverage"]
    def rows(items: Sequence[Mapping[str, Any]]) -> str:
        if not items:
            return '<tr><td colspan="4">None</td></tr>'
        return "".join(f"<tr><td><strong>{html.escape(LABELS.get(str(item.get('status')), str(item.get('status')).upper()))}</strong></td><td>{html.escape(str(item.get('name') or 'unnamed'))}</td><td>{html.escape(_location(item))}</td><td>{html.escape(str(item.get('reason') or ''))}</td></tr>" for item in items)
    alerts = [item for item in digest["records"] if item.get("status") in CRITICAL | ATTENTION]
    successes = [item for item in digest["records"] if item.get("status") == "success"][:40]
    error_html = "" if not coverage.get("errors") else "<h2>Coverage errors</h2><ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in coverage["errors"][:80]) + "</ul>"
    return f"""<!doctype html><html><body><h1>Scheduled-task digest — previous 24 hours</h1><p><strong>Window:</strong> {html.escape(digest['window']['start'])} to {html.escape(digest['window']['end'])}</p><p><strong>Critical:</strong> {summary['critical']} &nbsp; <strong>Attention:</strong> {summary['attention']} &nbsp; <strong>Certified success:</strong> {summary['success']} &nbsp; <strong>Not due:</strong> {summary['not_due']}</p><p><strong>Coverage:</strong> {'complete' if coverage['complete'] else 'partial'}; repositories={coverage['repositories_scanned']}; scheduled workflows={coverage['scheduled_workflows']}; runs={coverage['schedule_runs']}</p><h2>Action required / review</h2><table border="1" cellpadding="5" cellspacing="0"><tr><th>Status</th><th>Task</th><th>Location</th><th>Reason</th></tr>{rows(alerts)}</table><h2>Certified successes</h2><table border="1" cellpadding="5" cellspacing="0"><tr><th>Status</th><th>Task</th><th>Location</th><th>Reason</th></tr>{rows(successes)}</table>{error_html}<p><small>Digest SHA-256: {digest['digest_sha256']}</small></p></body></html>"""


def digest_subject(digest: Mapping[str, Any], logical_date: str) -> str:
    summary = digest["summary"]
    return f"Scheduled tasks {logical_date}: {summary['critical']} critical, {summary['attention']} attention, {summary['success']} certified"


def _send_sendgrid(*, api_key: str, sender: str, recipient: str, subject: str, plain_text: str, html_text: str, opener: Any | None = None) -> dict[str, Any]:
    payload = {"personalizations": [{"to": [{"email": recipient}]}], "from": {"email": sender}, "subject": subject, "content": [{"type": "text/plain", "value": plain_text}, {"type": "text/html", "value": html_text}]}
    request = urllib.request.Request("https://api.sendgrid.com/v3/mail/send", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "scheduled-task-digest/1"}, method="POST")
    try:
        with (opener or urllib.request.build_opener()).open(request, timeout=30) as response:
            if response.status not in {200, 202}:
                raise DigestError(f"SendGrid rejected the digest with HTTP {response.status}")
            return {"provider": "sendgrid", "accepted": True, "http_status": response.status, "message_id": response.headers.get("X-Message-Id")}
    except urllib.error.HTTPError as error:
        body = error.read(4096).decode(errors="replace")
        raise DigestError(f"SendGrid rejected the digest with HTTP {error.code}: {_safe_message(body)}") from None
    except urllib.error.URLError as error:
        raise DigestError(f"SendGrid delivery failed: {error.reason}") from None


def _send_smtp(*, host: str, port: int, username: str, password: str, sender: str, recipient: str, subject: str, plain_text: str, html_text: str) -> dict[str, Any]:
    if not host or not sender:
        raise DigestError("SMTP host and sender are required")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, recipient, subject
    message.set_content(plain_text)
    message.add_alternative(html_text, subtype="html")
    context = ssl.create_default_context()
    try:
        if port == 465:
            client: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(host, port, timeout=30)
        with client:
            if port != 465:
                client.ehlo(); client.starttls(context=context); client.ehlo()
            if username or password:
                client.login(username, password)
            refused = client.send_message(message)
    except (OSError, smtplib.SMTPException) as error:
        raise DigestError(f"SMTP delivery failed: {type(error).__name__}: {error}") from None
    if refused:
        raise DigestError(f"SMTP refused {len(refused)} recipient(s)")
    return {"provider": "smtp", "accepted": True, "host": host, "port": port}


def deliver_digest(*, recipient: str, subject: str, plain_text: str, html_text: str, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    env = environment if environment is not None else os.environ
    sendgrid = str(env.get("SCHEDULE_DIGEST_SENDGRID_API_KEY") or "")
    sender = str(env.get("SCHEDULE_DIGEST_FROM_EMAIL") or "")
    if sendgrid:
        if not sender:
            raise DigestError("SCHEDULE_DIGEST_FROM_EMAIL is required with SendGrid")
        return _send_sendgrid(api_key=sendgrid, sender=sender, recipient=recipient, subject=subject, plain_text=plain_text, html_text=html_text)
    gmail_user, gmail_password = str(env.get("GMAIL_SMTP_USERNAME") or ""), str(env.get("GMAIL_SMTP_APP_PASSWORD") or "")
    host = str(env.get("SCHEDULE_DIGEST_SMTP_HOST") or ("smtp.gmail.com" if gmail_user or gmail_password else ""))
    username = str(env.get("SCHEDULE_DIGEST_SMTP_USERNAME") or gmail_user)
    password = str(env.get("SCHEDULE_DIGEST_SMTP_PASSWORD") or gmail_password)
    if host or username or password:
        try:
            port = int(str(env.get("SCHEDULE_DIGEST_SMTP_PORT") or (465 if host == "smtp.gmail.com" else 587)))
        except ValueError as error:
            raise DigestError("SCHEDULE_DIGEST_SMTP_PORT must be an integer") from error
        return _send_smtp(host=host, port=port, username=username, password=password, sender=sender or username, recipient=recipient, subject=subject, plain_text=plain_text, html_text=html_text)
    raise DigestError("No email provider is configured; set SendGrid or SMTP/Gmail Actions secrets")


def write_outputs(output_dir: Path, *, digest: Mapping[str, Any], plain_text: str, html_text: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "digest.json").write_text(json.dumps(digest, indent=2, sort_keys=True) + "\n")
    (output_dir / "digest.txt").write_text(plain_text)
    (output_dir / "digest.html").write_text(html_text)


def write_receipt(output_dir: Path, *, decision: ScheduleDecision, digest: Mapping[str, Any], delivery: Mapping[str, Any], recipient: str, sent_at: datetime) -> dict[str, Any]:
    receipt = {"schema_version": RECEIPT_SCHEMA, "run_key": decision.run_key, "logical_date": decision.logical_date, "artifact_name": decision.artifact_name, "recipient": recipient, "digest_sha256": digest["digest_sha256"], "sent_at": format_instant(sent_at), "delivery": dict(delivery)}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "delivery-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    return receipt
