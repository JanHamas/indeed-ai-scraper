"""
my_actor/email_logs.py
Send a run summary / log excerpt to your own Gmail via SMTP.
"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Any


SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def send_logs_email(
    subject: str,
    body: str,
    log: Any,
    to_address: str = GMAIL_ADDRESS,
) -> None:
    """
    Sends a plain-text email with the given subject/body to `to_address`.
    Call this at the end of a run (success or failure) with a short summary —
    don't dump full scraped job content into the body, just status/errors,
    so this stays a debugging tool rather than a data pipeline.
    """
    msg = MIMEMultipart()
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = to_address
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, [to_address], msg.as_string())
        log.info(f"📧 Sent log email to {to_address}")
    except Exception as e:
        log.warning(f"⚠️ Failed to send log email: {e}")