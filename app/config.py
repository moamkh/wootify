"""
Module Overview
---------------
Purpose: Centralized runtime settings loaded from environment variables.
Documentation Standard: module/class/public-method docstrings.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

repo_root = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Represents settings."""
    model_config = SettingsConfigDict(
        env_file=str(repo_root / '.env'),
        env_file_encoding='utf-8',
        extra='ignore',
    )

    SERVER_BASE_URL: str = 'http://localhost:8000'
    DATABASE_URL: str = f"sqlite:///{(repo_root / 'wootify.db').as_posix()}"
    SQLITE_BUSY_TIMEOUT_MS: int = 30000
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


settings = Settings()
