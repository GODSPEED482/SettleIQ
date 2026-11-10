"""Outbound email through an SMTP relay. Tests replace `get_mail_sender`."""

import smtplib
import ssl
from email.message import EmailMessage
from typing import Protocol

from app.config import get_settings


class MailDeliveryError(RuntimeError):
    pass


class MailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None:
        """Deliver one message, or raise MailDeliveryError."""


class SmtpMailSender:
    def send(self, to: str, subject: str, body: str) -> None:
        settings = get_settings()
        if not settings.smtp_host or not settings.smtp_from:
            raise MailDeliveryError("SMTP relay is not configured")
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            # The type alone: SMTP error text can echo credentials or addresses.
            raise MailDeliveryError(type(error).__name__) from None


def get_mail_sender() -> MailSender:
    return SmtpMailSender()
