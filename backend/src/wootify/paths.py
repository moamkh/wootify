"""Project and runtime filesystem locations.

Existing installations may still keep state at the repository root or under
``data``. New installations use ``var`` while legacy locations continue to be
selected whenever they already exist.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _configured_var_root() -> Path:
    value = str(os.getenv("WOOTIFY_VAR_DIR") or "").strip()
    path = Path(value) if value else PROJECT_ROOT / "var"
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


VAR_ROOT = _configured_var_root()


def legacy_or_var(legacy_relative: str, var_relative: str) -> Path:
    """Return an existing legacy path, otherwise its new ``var`` location."""
    legacy = (PROJECT_ROOT / legacy_relative).resolve()
    return legacy if legacy.exists() else (VAR_ROOT / var_relative).resolve()


def resolve_project_path(value: str | Path) -> Path:
    """Resolve a path against the repository root without creating it."""
    path = Path(value)
    return path.resolve() if path.is_absolute() else (PROJECT_ROOT / path).resolve()


DEFAULT_DATABASE_PATH = legacy_or_var("wootify.db", "db/wootify.db")
DEFAULT_LOG_PATH = legacy_or_var("backend.log", "logs/backend.log")
DEFAULT_BALE_PV_SESSION_DIR = legacy_or_var("data/bale_pv_sessions", "sessions/bale_pv")
DEFAULT_INSTAGRAM_SESSION_DIR = legacy_or_var("data/instagram_pv_sessions", "sessions/instagram_pv")
DEFAULT_ENTERPRISE_ASSET_DIR = legacy_or_var("data/enterprise_assets", "assets/enterprise")
DEFAULT_ENTERPRISE_TMP_DIR = legacy_or_var("data/tmp-enterprise-smoke", "tmp/enterprise-smoke")
