from pathlib import Path

import pytest

from wootify.infrastructure.storage.runtime_layout import RuntimeLayoutMigrator


def test_runtime_layout_plan_and_apply(tmp_path: Path) -> None:
    root = tmp_path / "project"
    var = root / "var"
    legacy = root / "data" / "bale_pv_sessions"
    legacy.mkdir(parents=True)
    (legacy / "account.session").write_text("secret", encoding="utf-8")

    migrator = RuntimeLayoutMigrator(root, var)
    items = migrator.plan()

    assert [(item.source, item.destination) for item in items] == [
        (legacy.resolve(), (var / "sessions" / "bale_pv").resolve())
    ]
    completed = migrator.apply(items)
    assert completed == items
    assert not legacy.exists()
    assert (var / "sessions" / "bale_pv" / "account.session").read_text(encoding="utf-8") == "secret"


def test_runtime_layout_refuses_existing_destination(tmp_path: Path) -> None:
    root = tmp_path / "project"
    source = root / "wootify.db"
    destination = root / "var" / "db" / "wootify.db"
    source.parent.mkdir(parents=True)
    source.write_text("old", encoding="utf-8")
    destination.parent.mkdir(parents=True)
    destination.write_text("new", encoding="utf-8")

    migrator = RuntimeLayoutMigrator(root, root / "var")
    with pytest.raises(FileExistsError):
        migrator.apply(migrator.plan())

    assert source.read_text(encoding="utf-8") == "old"
    assert destination.read_text(encoding="utf-8") == "new"
