import json
from typing import ClassVar

import httpx
import pytest

from bidalert.alerts import Alert, EmailNotifier, TelegramNotifier, build_notifiers
from bidalert.config import Settings, normalise_database_url
from tests.conftest import NOW

ALERT = Alert(7, "reminder_3d", "Supply of reagents", "CSIR", NOW, 0.81, "https://example.org/t/7")


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port
        self.calls: list[str] = []
        self.sent = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append("quit")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login:{user}")

    def send_message(self, msg):
        self.sent.append(msg)


def test_email_notifier_uses_starttls_login_and_digest():
    FakeSMTP.instances.clear()
    n = EmailNotifier(
        "smtp.test", 587, "bot@example.org", ["a@example.org"], "user", "pw", smtp_factory=FakeSMTP
    )
    n.send([ALERT])
    smtp = FakeSMTP.instances[0]
    assert smtp.calls == ["starttls", "login:user", "quit"]
    msg = smtp.sent[0]
    assert msg["To"] == "a@example.org" and "1 tender alert" in msg["Subject"]
    assert "Supply of reagents" in msg.get_content() and "reminder 3d" in msg.get_content()


def test_email_notifier_needs_recipients_and_skips_empty():
    with pytest.raises(ValueError):
        EmailNotifier("h", 25, "f@x", [])
    FakeSMTP.instances.clear()
    EmailNotifier("h", 25, "f@x", ["t@x"], smtp_factory=FakeSMTP).send([])
    assert FakeSMTP.instances == []


def test_telegram_notifier_posts_chunks():
    posts = []

    def handler(req: httpx.Request) -> httpx.Response:
        posts.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"ok": True})

    n = TelegramNotifier(
        "123:SECRET", "42", client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    n.send([ALERT] * 3)
    assert posts[0][0] == "/bot123:SECRET/sendMessage"
    assert posts[0][1]["chat_id"] == "42" and "Supply of reagents" in posts[0][1]["text"]


def test_telegram_error_does_not_leak_token():
    n = TelegramNotifier(
        "123:SECRET",
        "42",
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))),
    )
    with pytest.raises(RuntimeError) as exc:
        n.send([ALERT])
    assert "SECRET" not in str(exc.value)


def test_telegram_chunking_respects_limit():
    text = "\n".join("x" * 50 for _ in range(300)) + "\n" + "y" * 9000
    parts = TelegramNotifier.chunks(text, limit=4096)
    assert all(len(p) <= 4096 for p in parts)
    assert "".join(parts) == text


def test_alerts_disabled_by_default():
    assert build_notifiers(Settings.from_env({})) == []
    s = Settings.from_env({"BIDALERT_ALERTS_ENABLED": "true"})
    assert build_notifiers(s) == []  # enabled but nothing configured


def test_settings_from_env_builds_both_channels():
    env = {
        "BIDALERT_ALERTS_ENABLED": "1",
        "BIDALERT_SMTP_HOST": "smtp.test",
        "BIDALERT_EMAIL_FROM": "bot@example.org",
        "BIDALERT_EMAIL_TO": "a@example.org, b@example.org",
        "BIDALERT_TELEGRAM_BOT_TOKEN": "t",
        "BIDALERT_TELEGRAM_CHAT_ID": "c",
        "BIDALERT_SOURCES": "cppp, synthetic",
        "BIDALERT_CPPP_MAX_PAGES": "9",
    }
    s = Settings.from_env(env)
    assert [n.channel for n in build_notifiers(s)] == ["email", "telegram"]
    assert s.email_to == ("a@example.org", "b@example.org")
    assert s.sources == ("cppp", "synthetic") and s.cppp_max_pages == 9


def test_secrets_not_in_repr():
    s = Settings(smtp_password="hunter2", telegram_bot_token="tok-123")
    assert "hunter2" not in repr(s) and "tok-123" not in repr(s)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u@h/db", "postgresql+psycopg://u@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ],
)
def test_database_url_normalisation(url, expected):
    assert normalise_database_url(url) == expected


def test_generic_database_url_fallback():
    assert (
        Settings.from_env({"DATABASE_URL": "postgres://h/db"}).database_url
        == "postgresql+psycopg://h/db"
    )
