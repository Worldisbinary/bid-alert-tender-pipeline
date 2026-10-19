"""Alert notifiers (email, Telegram) behind one interface, plus an idempotent dispatcher.

Alerts are OFF unless BIDALERT_ALERTS_ENABLED=true and a channel is configured.
The dispatcher records every (tender, channel, kind) it sends in `alerts_sent`, so
re-running the pipeline never re-sends the same alert (at-most-once per channel
after a successful send; a failed send is retried on the next run).
"""

from __future__ import annotations

import logging
import smtplib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol

import httpx

from bidalert.config import Settings

log = logging.getLogger(__name__)

TELEGRAM_LIMIT = 4096  # max characters per Telegram message


@dataclass(frozen=True)
class Alert:
    tender_db_id: int
    kind: str  # "new_high_priority" | "reminder_7d" | "reminder_3d" | "reminder_1d"
    title: str
    organisation: str
    closing_at: datetime
    score: float | None
    url: str | None

    def line(self) -> str:
        score = f"score {self.score:.2f}" if self.score is not None else "unscored"
        when = self.closing_at.strftime("%d %b %Y %H:%M %Z")
        label = self.kind.replace("_", " ")
        link = f"\n  {self.url}" if self.url else ""
        return f"[{label}] {self.title} - {self.organisation} (closes {when}, {score}){link}"


class Notifier(Protocol):
    channel: str

    def send(self, alerts: Sequence[Alert]) -> None: ...


def render_digest(alerts: Sequence[Alert]) -> str:
    header = f"Bid Alert: {len(alerts)} tender alert(s)\n\n"
    return header + "\n".join(a.line() for a in alerts)


class EmailNotifier:
    channel = "email"

    def __init__(
        self,
        host: str,
        port: int,
        sender: str,
        recipients: Sequence[str],
        username: str | None = None,
        password: str | None = None,
        starttls: bool = True,
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
        timeout_s: float = 30.0,
    ) -> None:
        if not recipients:
            raise ValueError("EmailNotifier needs at least one recipient")
        self.host, self.port = host, port
        self.sender, self.recipients = sender, list(recipients)
        self.username, self.password = username, password
        self.starttls = starttls
        self._smtp_factory = smtp_factory
        self.timeout_s = timeout_s

    def build_message(self, alerts: Sequence[Alert]) -> EmailMessage:
        msg = EmailMessage()
        msg["Subject"] = f"[Bid Alert] {len(alerts)} tender alert(s)"
        msg["From"] = self.sender
        msg["To"] = ", ".join(self.recipients)
        msg.set_content(render_digest(alerts))
        return msg

    def send(self, alerts: Sequence[Alert]) -> None:
        if not alerts:
            return
        msg = self.build_message(alerts)
        with self._smtp_factory(self.host, self.port, timeout=self.timeout_s) as smtp:
            if self.starttls:
                smtp.starttls()
            if self.username and self.password:
                smtp.login(self.username, self.password)
            smtp.send_message(msg)


class TelegramNotifier:
    channel = "telegram"
    api_base = "https://api.telegram.org"

    def __init__(self, bot_token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        self._token = bot_token
        self.chat_id = chat_id
        self._client = client or httpx.Client(timeout=30.0)

    @staticmethod
    def chunks(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
        """Split on line boundaries so no message exceeds Telegram's limit."""
        out: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            while len(line) > limit:  # a single overlong line: hard split
                if current:
                    out.append(current)
                    current = ""
                out.append(line[:limit])
                line = line[limit:]
            if len(current) + len(line) > limit:
                out.append(current)
                current = ""
            current += line
        if current:
            out.append(current)
        return out

    def send(self, alerts: Sequence[Alert]) -> None:
        if not alerts:
            return
        url = f"{self.api_base}/bot{self._token}/sendMessage"
        for part in self.chunks(render_digest(alerts)):
            resp = self._client.post(
                url, json={"chat_id": self.chat_id, "text": part, "disable_web_page_preview": True}
            )
            if resp.status_code != 200:
                # Never log the URL: it contains the bot token.
                raise RuntimeError(f"Telegram API returned HTTP {resp.status_code}")


def build_notifiers(settings: Settings) -> list[Notifier]:
    if not settings.alerts_enabled:
        return []
    notifiers: list[Notifier] = []
    if settings.smtp_host and settings.email_from and settings.email_to:
        notifiers.append(
            EmailNotifier(
                host=settings.smtp_host,
                port=settings.smtp_port,
                sender=settings.email_from,
                recipients=settings.email_to,
                username=settings.smtp_username,
                password=settings.smtp_password,
                starttls=settings.smtp_starttls,
            )
        )
    if settings.telegram_bot_token and settings.telegram_chat_id:
        notifiers.append(TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id))
    if not notifiers:
        log.warning("alerts enabled but no channel configured (set SMTP_* or TELEGRAM_* variables)")
    return notifiers


class AlertLedger(Protocol):
    def already_sent(self, tender_id: int, channel: str, kind: str) -> bool: ...

    def record(self, tender_id: int, channel: str, kind: str, sent_at: datetime) -> None: ...


@dataclass(frozen=True)
class DispatchResult:
    sent: dict[str, int]
    skipped_already_sent: int
    failed_channels: tuple[str, ...]


class AlertDispatcher:
    def __init__(self, notifiers: Sequence[Notifier], ledger: AlertLedger) -> None:
        self.notifiers = list(notifiers)
        self.ledger = ledger

    def dispatch(self, alerts: Sequence[Alert], now: datetime) -> DispatchResult:
        sent: dict[str, int] = {}
        skipped = 0
        failed: list[str] = []
        for notifier in self.notifiers:
            fresh = [
                a
                for a in alerts
                if not self.ledger.already_sent(a.tender_db_id, notifier.channel, a.kind)
            ]
            skipped += len(alerts) - len(fresh)
            if not fresh:
                continue
            try:
                notifier.send(fresh)
            except Exception as exc:  # one broken channel must not stop the others
                log.error(
                    "alert channel failed", extra={"channel": notifier.channel, "error": str(exc)}
                )
                failed.append(notifier.channel)
                continue
            for a in fresh:
                self.ledger.record(a.tender_db_id, notifier.channel, a.kind, now)
            sent[notifier.channel] = len(fresh)
        return DispatchResult(
            sent=sent, skipped_already_sent=skipped, failed_channels=tuple(failed)
        )
