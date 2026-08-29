from __future__ import annotations

from .common import *

def build_report_email(*, recipient: str, sender: str, completion: Mapping[str, Any]) -> EmailMessage:
    if not EMAIL_RE.fullmatch(recipient):
        raise WorkerError('ARTIFACT_RECOVERY_REPORT_TO is not a valid email address')
    if not EMAIL_RE.fullmatch(sender):
        raise WorkerError('ARTIFACT_RECOVERY_REPORT_FROM is not a valid email address')
    ensure_public_safe(completion, 'completion')
    message = EmailMessage()
    message['To'] = recipient
    message['From'] = sender
    message['Subject'] = f"Artifact recovery {completion['outcome']}: {completion['run_key_sha256'][:12]}"
    summary = completion['summary']
    message.set_content('\n'.join(['Nightly artifact-recovery completion receipt', '', f"Outcome: {completion['outcome']}", f"Job: {completion['job_id']}", f"Run key digest: {completion['run_key_sha256']}", f"Completed at: {completion['completed_at']}", f"Entries: {summary['entries']}", f"Complete: {summary['complete']}", f"Excluded: {summary['excluded']}", f"Actionable: {summary['actionable']}", f"Blocked: {summary['blocked']}", f"Source coverage: {completion['source_coverage']['status']}", '', 'No prompt bodies or credential values are included.']))
    ensure_public_safe(message.as_string(), 'email report')
    return message

def deliver_report_email(completion: Mapping[str, Any], *, environment: Mapping[str, str] | None=None, smtp_factory: Callable[..., smtplib.SMTP]=smtplib.SMTP, smtp_ssl_factory: Callable[..., smtplib.SMTP_SSL]=smtplib.SMTP_SSL) -> dict[str, Any]:
    env = environment if environment is not None else os.environ
    recipient = env.get('ARTIFACT_RECOVERY_REPORT_TO', '').strip()
    generated_at = format_instant(utc_now())
    if not recipient:
        receipt = {'schema_version': DELIVERY_SCHEMA, 'generated_at': generated_at, 'configured': False, 'status': 'not_configured', 'recipient_sha256': None, 'message_sha256': None}
        receipt['receipt_sha256'] = sha256_value(receipt)
        return receipt
    sender = env.get('ARTIFACT_RECOVERY_REPORT_FROM', '').strip()
    host = env.get('ARTIFACT_RECOVERY_SMTP_HOST', '').strip()
    if not host:
        raise WorkerError('ARTIFACT_RECOVERY_SMTP_HOST is required when report delivery is configured')
    port_raw = env.get('ARTIFACT_RECOVERY_SMTP_PORT', '587')
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise WorkerError('ARTIFACT_RECOVERY_SMTP_PORT must be an integer') from exc
    if not 1 <= port <= 65535:
        raise WorkerError('ARTIFACT_RECOVERY_SMTP_PORT is out of range')
    security = env.get('ARTIFACT_RECOVERY_SMTP_SECURITY', 'starttls').strip().lower()
    if security not in {'starttls', 'tls', 'none'}:
        raise WorkerError('ARTIFACT_RECOVERY_SMTP_SECURITY must be starttls, tls, or none')
    if security == 'none' and host not in {'localhost', '127.0.0.1', '::1'}:
        raise WorkerError('unencrypted SMTP is permitted only on loopback')
    username = env.get('ARTIFACT_RECOVERY_SMTP_USERNAME', '')
    password_env = env.get('ARTIFACT_RECOVERY_SMTP_PASSWORD_ENV', 'ARTIFACT_RECOVERY_SMTP_PASSWORD')
    if not ENV_NAME_RE.fullmatch(password_env):
        raise WorkerError('ARTIFACT_RECOVERY_SMTP_PASSWORD_ENV is invalid')
    password = env.get(password_env, '')
    if bool(username) != bool(password):
        raise WorkerError('SMTP username and password must be configured together')
    message = build_report_email(recipient=recipient, sender=sender, completion=completion)
    context = ssl.create_default_context()
    try:
        if security == 'tls':
            client = smtp_ssl_factory(host, port, timeout=20, context=context)
        else:
            client = smtp_factory(host, port, timeout=20)
        with client:
            if security == 'starttls':
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise WorkerError('artifact-recovery email report delivery failed', retryable=True, error_class='availability') from exc
    receipt = {'schema_version': DELIVERY_SCHEMA, 'generated_at': generated_at, 'configured': True, 'status': 'delivered', 'recipient_sha256': redacted_digest(recipient.lower()), 'message_sha256': hashlib.sha256(message.as_bytes()).hexdigest()}
    receipt['receipt_sha256'] = sha256_value(receipt)
    ensure_public_safe(receipt, 'delivery receipt')
    return receipt
