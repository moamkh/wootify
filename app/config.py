"""
Module Overview
---------------
Purpose: Centralized runtime settings loaded from environment variables.
Documentation Standard: module/class/public-method docstrings.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from pydantic_settings import BaseSettings, SettingsConfigDict

repo_root = Path(__file__).resolve().parents[1]
default_sqlite_database_url = f"sqlite:///{(repo_root / 'wootify.db').as_posix()}"


class Settings(BaseSettings):
    """Represents settings."""
    model_config = SettingsConfigDict(
        env_file=str(repo_root / '.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    SERVER_BASE_URL: str = 'http://localhost:8000'
    DATABASE_URL: str = default_sqlite_database_url
    DATABASE_NAME: str = 'wootify_instance_manager_pg'
    DATABASE_AUTO_CREATE: bool = True
    POSTGRES_ADMIN_DATABASE: str = 'postgres'
    SQLITE_MIGRATION_SOURCE_URL: str = default_sqlite_database_url
    SQLITE_BUSY_TIMEOUT_MS: int = 60000
    SQLITE_JOURNAL_MODE: str = 'WAL'

    CHATWOOT_BASE_URL: str = 'http://localhost:3000'
    CHATWOOT_API_TOKEN: str = ''
    CHATWOOT_STATUS_NOTIFY_TO_BALE: bool = True
    CHATWOOT_STATUS_MESSAGE_OPEN: str = 'Your chat has been opened.'
    CHATWOOT_STATUS_MESSAGE_OPEN_BY_OPERATOR: str = 'Your chat has been opened by {operator_name}.'
    CHATWOOT_STATUS_MESSAGE_RESOLVED: str = 'Your chat has been resolved.'
    CHATWOOT_STATUS_MESSAGE_PENDING: str = 'Your chat is pending.'
    CHATWOOT_STATUS_MESSAGE_SNOOZED: str = 'Your chat has been snoozed.'

    BALE_API_BASE_URL: str = 'https://tapi.bale.ai'
    BALE_FILE_BASE_URL: str = 'https://tapi.bale.ai/file'
    BALE_POLL_INTERVAL_SECONDS: int = 5
    BALE_LONG_POLL_TIMEOUT_SECONDS: int = 25
    BALE_SHARE_PHONE_BUTTON: bool = True
    BALE_SHARE_PHONE_BUTTON_TEXT: str = 'Share phone number'
    BALE_START_MESSAGE_TEXT: str = 'Send message and wait for our operators to respond pls'
    BALE_SHARE_PHONE_PROMPT_TEXT: str = (
        'Use the button below to share your phone number.\n'
        'Commands: /share_phone, /help'
    )
    ENTERPRISE_SMS_SYNC_ENABLED: bool = False
    ENTERPRISE_SMS_API_URL: str = 'https://apiserver.novinmed.com/SoftNoNTFC/LastId'
    ENTERPRISE_SMS_API_TOKEN: str = ''
    ENTERPRISE_SMS_TOKEN_HEADER: str = 'Authorization'
    ENTERPRISE_SMS_TOKEN_PREFIX: str = ''
    ENTERPRISE_SMS_POLL_INTERVAL_MINUTES: int = 20
    ENTERPRISE_SMS_INITIAL_LAST_ID: int = 0
    ENTERPRISE_SMS_HTTP_TIMEOUT_SECONDS: int = 30
    ENTERPRISE_SMS_FILE_LOG_ENABLED: bool = False

    ENTERPRISE_GRE_API_URL: str = 'https://apiserver.novinmed.com/SoftNoCRM/FindCustomer'
    ENTERPRISE_GRE_API_TIMEOUT_SECONDS: int = 10
    ENTERPRISE_GRE_BACKDOOR_PHONES: str = ''

    TELEGRAM_API_BASE_URL: str = 'https://api.telegram.org/bot'
    TELEGRAM_FILE_BASE_URL: str = 'https://api.telegram.org/file/bot'
    TELEGRAM_POLL_INTERVAL_SECONDS: int = 5
    TELEGRAM_LONG_POLL_TIMEOUT_SECONDS: int = 25
    TELEGRAM_SHARE_PHONE_BUTTON: bool = True
    TELEGRAM_SHARE_PHONE_BUTTON_TEXT: str = 'Share phone number'
    TELEGRAM_START_MESSAGE_TEXT: str = 'Send message and wait for our operators to respond pls'
    TELEGRAM_SHARE_PHONE_PROMPT_TEXT: str = (
        'Use the button below to share your phone number.\n'
        'Commands: /share_phone, /help'
    )

    DATA_ENCRYPTION_KEY: str = ''
    STORE_MESSAGE_PAYLOADS: bool = False

    LOG_LEVEL: str = 'INFO'
    LOG_MESSAGE_CONTENT: bool = False
    LOG_PAYLOAD_TRUNCATE: int = 200
    LOG_REDACT_SECRETS: bool = True
    LOG_HTTP_REQUESTS: bool = False
    LOG_COLOR: bool = True
    LOG_COLOR_FORCE: bool = False

    @property
    def resolved_database_url(self) -> str:
        """Resolve the final application database URL from environment settings."""
        raw_url = str(self.DATABASE_URL or '').strip() or default_sqlite_database_url
        url = make_url(raw_url)
        if not url.drivername.startswith('postgresql'):
            return raw_url

        database_name = str(self.DATABASE_NAME or '').strip() or str(url.database or '').strip()
        if not database_name:
            raise ValueError('DATABASE_NAME is required for PostgreSQL connections')
        return url.set(database=database_name).render_as_string(hide_password=False)

    @property
    def postgres_admin_url(self) -> str | None:
        """Resolve the PostgreSQL admin URL used to create the target database."""
        raw_url = str(self.DATABASE_URL or '').strip() or default_sqlite_database_url
        url = make_url(raw_url)
        if not url.drivername.startswith('postgresql'):
            return None

        admin_database = str(self.POSTGRES_ADMIN_DATABASE or '').strip() or 'postgres'
        return url.set(database=admin_database).render_as_string(hide_password=False)

    @property
    def sqlite_migration_source_url(self) -> str:
        """Resolve the SQLite source URL used for one-time Postgres data migration."""
        return str(self.SQLITE_MIGRATION_SOURCE_URL or '').strip() or default_sqlite_database_url


settings = Settings()
