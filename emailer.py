"""Email delivery for SCT.

Local mode: every outgoing message is written to ``data/emails/`` and also
recorded in-app so end-to-end journeys can be traced without sending mail.
SMTP mode: configured SMTP (TLS) is used for real delivery.

Email is intentionally never sent automatically to real addresses during the
test harness; the test flow "simulates" approval emails via this logger.
"""
import os
import smtplib
from email.message import EmailMessage
from datetime import datetime, timezone

import config

EMAIL_DIR = os.path.join(config.PROJECT_ROOT, "data", "emails")


def _ensure_email_dir():
    os.makedirs(EMAIL_DIR, exist_ok=True)


def send_email(to, subject, body, reply_to=None):
    """Dispatch an email. Returns a record describing what was done."""
    msg = EmailMessage()
    msg["From"] = config.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    if reply_to:
        msg["Reply-To"] = reply_to

    if config.is_email_external():
        try:
            server = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15)
            if config.SMTP_USE_TLS:
                server.starttls()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.send_message(msg)
            server.quit()
            mode = "sent"
        except Exception as exc:
            return {"mode": "failed", "error": str(exc), "to": to, "subject": subject}
    else:
        _ensure_email_dir()
        filename = os.path.join(EMAIL_DIR, f"mail_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.txt")
        with open(filename, "w", encoding="utf-8") as fh:
            fh.write(f"To: {to}\nSubject: {subject}\n\n{body}")
        mode = "logged"

    return {"mode": mode, "to": to, "subject": subject, "filename": filename}
