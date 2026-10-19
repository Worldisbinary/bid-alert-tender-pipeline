"""Runtime settings, read from environment variables (12-factor style).

Every setting has a safe default so `bidalert run` works on a fresh clone with no
configuration: SQLite in ./data, the synthetic source, alerts disabled.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from bidalert import __version__

PREFIX = "BIDALERT_"

USER_AGENT = (
    f"bid-alert-tender-pipeline/{__version__} "
    "(+https://github.com/Worldisbinary/bid-alert-tender-pipeline; polite research crawler)"
)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    return tuple(part.strip() for part in value.split(",") if part.strip())


def normalise_database_url(url: str) -> str:
    """Map provider-style URLs (postgres://, postgresql://) to the psycopg 3 driver.

    Hosted Postgres services (Render, Heroku, Neon) hand out `postgres://` URLs;
    SQLAlchemy needs an explicit driver name.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/bidalert.db"
    profile_path: Path | None = None  # None -> packaged default_profile.toml
    sources: tuple[str, ...] = ("synthetic",)
    output_dir: Path = Path("output")
    site_dir: Path = Path("site")
    fixtures_dir: Path = Path("tests/fixtures/cppp")

    # Polite HTTP
    user_agent: str = USER_AGENT
    http_min_interval_s: float = 3.0
    http_max_retries: int = 4
    http_timeout_s: float = 30.0
    http_cache_dir: Path = Path(".cache/http")
    http_cache_ttl_s: int = 3600
    cppp_max_pages: int = 5

    # Alerts (disabled unless explicitly enabled)
    alerts_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = field(default=None, repr=False)
    smtp_starttls: bool = True
    email_from: str | None = None
    email_to: tuple[str, ...] = ()
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = None

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json | text

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env

        def get(name: str) -> str | None:
            return e.get(PREFIX + name)

        db = get("DATABASE_URL") or e.get("DATABASE_URL") or cls.database_url
        profile = get("PROFILE")
        return cls(
            database_url=normalise_database_url(db),
            profile_path=Path(profile) if profile else None,
            sources=_csv(get("SOURCES"), cls.sources),
            output_dir=Path(get("OUTPUT_DIR") or cls.output_dir),
            site_dir=Path(get("SITE_DIR") or cls.site_dir),
            fixtures_dir=Path(get("FIXTURES_DIR") or cls.fixtures_dir),
            http_min_interval_s=float(get("HTTP_MIN_INTERVAL_S") or cls.http_min_interval_s),
            http_max_retries=int(get("HTTP_MAX_RETRIES") or cls.http_max_retries),
            http_timeout_s=float(get("HTTP_TIMEOUT_S") or cls.http_timeout_s),
            http_cache_dir=Path(get("HTTP_CACHE_DIR") or cls.http_cache_dir),
            http_cache_ttl_s=int(get("HTTP_CACHE_TTL_S") or cls.http_cache_ttl_s),
            cppp_max_pages=int(get("CPPP_MAX_PAGES") or cls.cppp_max_pages),
            alerts_enabled=_bool(get("ALERTS_ENABLED"), False),
            smtp_host=get("SMTP_HOST"),
            smtp_port=int(get("SMTP_PORT") or cls.smtp_port),
            smtp_username=get("SMTP_USERNAME"),
            smtp_password=get("SMTP_PASSWORD"),
            smtp_starttls=_bool(get("SMTP_STARTTLS"), True),
            email_from=get("EMAIL_FROM"),
            email_to=_csv(get("EMAIL_TO"), ()),
            telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=get("TELEGRAM_CHAT_ID"),
            log_level=(get("LOG_LEVEL") or cls.log_level).upper(),
            log_format=(get("LOG_FORMAT") or cls.log_format).lower(),
        )
