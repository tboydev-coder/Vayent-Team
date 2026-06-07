"""SMTP-backed notification delivery for user lifecycle events."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage
from html import escape

from app.config import get_settings
from app.models import DatabaseConnection, User

logger = logging.getLogger(__name__)


class NotificationService:
    """Send best-effort notification emails for key user actions."""

    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def is_enabled(self) -> bool:
        settings = self.settings
        return bool(
            settings.email_notifications_enabled
            and settings.smtp_host.strip()
            and settings.smtp_from_email.strip()
            and settings.smtp_username.strip()
            and settings.smtp_password.strip()
        )

    async def send_signup_email(self, user: User, provider: str) -> None:
        if not self.is_enabled:
            logger.info("Signup email skipped because notifications are not configured")
            return

        timestamp = self._timestamp_label()
        subject = "Welcome to Vayent"
        body, html_body = self._build_notification_content(
            title="Welcome to Vayent",
            greeting=f"Hi {user.username},",
            intro="Your Vayent account has been created successfully.",
            details=[
                ("Email", user.email),
                ("Sign-in provider", provider.title()),
                ("Time", timestamp),
            ],
            closing="You can now connect a database and start using the workspace.",
        )
        await self._send_email(user.email, subject, body, html_body)

    async def send_login_email(self, user: User, provider: str) -> None:
        if not self.is_enabled:
            logger.info("Login email skipped because notifications are not configured")
            return

        timestamp = self._timestamp_label()
        subject = "New Vayent login detected"
        body, html_body = self._build_notification_content(
            title="New Vayent login detected",
            greeting=f"Hi {user.username},",
            intro="We noticed a new sign-in to your Vayent account.",
            details=[
                ("Email", user.email),
                ("Sign-in provider", provider.title()),
                ("Time", timestamp),
            ],
            closing=(
                "If this was not you, rotate your OAuth credentials and review "
                "your activity logs."
            ),
            tone="security",
        )
        await self._send_email(user.email, subject, body, html_body)

    async def send_database_connected_email(
        self,
        user: User,
        connection: DatabaseConnection,
    ) -> None:
        if not self.is_enabled:
            logger.info(
                "Database connection email skipped because notifications are not configured"
            )
            return

        timestamp = self._timestamp_label()
        subject = "A database was connected to your Vayent workspace"
        database_type = (
            connection.db_type.value
            if hasattr(connection.db_type, "value")
            else connection.db_type
        )
        body, html_body = self._build_notification_content(
            title="Database connected",
            greeting=f"Hi {user.username},",
            intro="A database connection was added to your Vayent workspace.",
            details=[
                ("Connection name", connection.name),
                ("Database type", str(database_type)),
                ("Database name", connection.database_name),
                ("Host", f"{connection.host}:{connection.port}"),
                ("Time", timestamp),
            ],
            closing=(
                "If you did not make this change, revoke the connection and "
                "rotate the database credentials."
            ),
            tone="security",
        )
        await self._send_email(user.email, subject, body, html_body)

    async def _send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._send_email_sync,
            to_email,
            subject,
            body,
            html_body,
        )

    def _send_email_sync(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: str | None = None,
    ) -> None:
        settings = self.settings

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self._from_header()
        message["To"] = to_email
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as client:
                self._authenticate_and_send(client, message)
            return

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as client:
            client.ehlo()
            if settings.smtp_use_tls:
                client.starttls()
                client.ehlo()
            self._authenticate_and_send(client, message)

    def _authenticate_and_send(self, client, message: EmailMessage) -> None:
        settings = self.settings
        client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)

    def _from_header(self) -> str:
        settings = self.settings
        if settings.smtp_from_name.strip():
            return f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        return settings.smtp_from_email

    def _build_notification_content(
        self,
        *,
        title: str,
        greeting: str,
        intro: str,
        details: list[tuple[str, str]],
        closing: str,
        tone: str = "standard",
    ) -> tuple[str, str]:
        text_lines = [
            greeting,
            "",
            intro,
            "",
            *(f"{label}: {value}" for label, value in details),
            "",
            closing,
            "",
            f"Open Vayent: {self._app_url()}",
        ]

        logo_url = f"{self._app_url()}/img/logo.jpeg"
        detail_rows = "".join(
            self._detail_row(label, value) for label, value in details
        )
        accent_color = "#38bdf8" if tone == "standard" else "#fbbf24"
        safe_title = escape(title)
        safe_greeting = escape(greeting)
        safe_intro = escape(intro)
        safe_closing = escape(closing)
        safe_logo_url = escape(logo_url, quote=True)
        safe_app_url = escape(self._app_url(), quote=True)

        html_body = f"""<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#0b0f19;font-family:Inter,Arial,sans-serif;color:#e5e7eb;">
    <div style="display:none;max-height:0;overflow:hidden;color:transparent;">
      {safe_intro}
    </div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#0b0f19;padding:32px 16px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:620px;background:#111827;border:1px solid #243044;border-radius:18px;overflow:hidden;">
            <tr>
              <td style="padding:28px 32px 18px;background:#0f172a;border-bottom:1px solid #243044;">
                <img src="{safe_logo_url}" width="48" height="48" alt="Vayent logo" style="display:block;border-radius:12px;margin-bottom:18px;object-fit:cover;">
                <div style="font-size:12px;line-height:18px;letter-spacing:0.16em;text-transform:uppercase;color:{accent_color};font-weight:700;">Vayent</div>
                <h1 style="margin:8px 0 0;font-size:28px;line-height:34px;color:#ffffff;font-weight:700;">{safe_title}</h1>
              </td>
            </tr>
            <tr>
              <td style="padding:30px 32px 12px;">
                <p style="margin:0 0 14px;font-size:16px;line-height:26px;color:#f8fafc;">{safe_greeting}</p>
                <p style="margin:0;font-size:16px;line-height:26px;color:#cbd5e1;">{safe_intro}</p>
              </td>
            </tr>
            <tr>
              <td style="padding:10px 32px;">
                <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid #263244;border-radius:14px;overflow:hidden;">
                  {detail_rows}
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:14px 32px 30px;">
                <p style="margin:0 0 22px;font-size:15px;line-height:25px;color:#cbd5e1;">{safe_closing}</p>
                <a href="{safe_app_url}" style="display:inline-block;background:#ffffff;color:#0f172a;text-decoration:none;border-radius:10px;padding:12px 18px;font-size:14px;font-weight:700;">Open Vayent</a>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 32px;background:#0b1220;border-top:1px solid #243044;">
                <p style="margin:0;font-size:12px;line-height:20px;color:#94a3b8;">This email was sent for activity on your Vayent workspace.</p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""

        return "\n".join(text_lines), html_body

    @staticmethod
    def _detail_row(label: str, value: str) -> str:
        return (
            "<tr>"
            "<td style=\"padding:13px 16px;border-bottom:1px solid #263244;"
            "font-size:12px;line-height:18px;color:#94a3b8;text-transform:uppercase;"
            "letter-spacing:0.08em;\">"
            f"{escape(label)}"
            "</td>"
            "<td style=\"padding:13px 16px;border-bottom:1px solid #263244;"
            "font-size:14px;line-height:22px;color:#f8fafc;text-align:right;\">"
            f"{escape(str(value))}"
            "</td>"
            "</tr>"
        )

    def _app_url(self) -> str:
        return (
            getattr(self.settings, "frontend_app_uri", "http://localhost:3000")
            .strip()
            .rstrip("/")
            or "http://localhost:3000"
        )

    @staticmethod
    def _timestamp_label() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


notification_service = NotificationService()
