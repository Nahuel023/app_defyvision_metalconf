from pathlib import Path

import pytest

import src.utils.atomic_write as atomic_module
from src.utils.atomic_write import atomic_write_json, atomic_write_text


def test_atomic_write_replaces_complete_file(tmp_path: Path) -> None:
    target = tmp_path / "config.json"
    target.write_text("old", encoding="utf-8")

    atomic_write_json(target, {"value": 42})

    assert '"value": 42' in target.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".config.json.*.tmp")) == []


def test_atomic_write_preserves_original_when_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    target = tmp_path / "config.yaml"
    target.write_text("original", encoding="utf-8")

    def _fail_replace(*_args) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(atomic_module.os, "replace", _fail_replace)

    with pytest.raises(OSError, match="disk failure"):
        atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".config.yaml.*.tmp")) == []
