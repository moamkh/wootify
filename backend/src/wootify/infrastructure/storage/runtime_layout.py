"""Explicit migration from legacy runtime locations into ``var``."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MigrationItem:
    """One legacy runtime path and its desired destination."""

    source: Path
    destination: Path


class RuntimeLayoutMigrator:
    """Plan and explicitly apply safe runtime-state moves."""

    _PATHS = (
        ("wootify.db", "db/wootify.db"),
        ("wootify.db-shm", "db/wootify.db-shm"),
        ("wootify.db-wal", "db/wootify.db-wal"),
        ("backend.log", "logs/backend.log"),
        ("backend.log.1", "logs/backend.log.1"),
        ("backend.log.2", "logs/backend.log.2"),
        ("backend.log.3", "logs/backend.log.3"),
        ("backend.log.4", "logs/backend.log.4"),
        ("backend.log.5", "logs/backend.log.5"),
        ("data/bale_pv_sessions", "sessions/bale_pv"),
        ("data/instagram_pv_sessions", "sessions/instagram_pv"),
        ("data/enterprise_assets", "assets/enterprise"),
        ("data/tmp-enterprise-smoke", "tmp/enterprise-smoke"),
    )

    def __init__(self, project_root: Path, var_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.var_root = var_root.resolve()
        if not self.var_root.is_relative_to(self.project_root):
            raise ValueError("runtime var directory must be inside the project root")

    def plan(self) -> list[MigrationItem]:
        """Return existing legacy paths that can be migrated."""
        return [
            MigrationItem(
                source=(self.project_root / source).resolve(),
                destination=(self.var_root / destination).resolve(),
            )
            for source, destination in self._PATHS
            if (self.project_root / source).exists()
        ]

    def apply(self, items: Iterable[MigrationItem]) -> list[MigrationItem]:
        """Move planned items, refusing unsafe paths and collisions."""
        completed: list[MigrationItem] = []
        for item in items:
            source = item.source.resolve()
            destination = item.destination.resolve()
            if not source.is_relative_to(self.project_root):
                raise ValueError(f"source escapes project root: {source}")
            if not destination.is_relative_to(self.var_root):
                raise ValueError(f"destination escapes var root: {destination}")
            if not source.exists():
                continue
            if destination.exists():
                raise FileExistsError(f"destination already exists: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            completed.append(item)
        return completed
