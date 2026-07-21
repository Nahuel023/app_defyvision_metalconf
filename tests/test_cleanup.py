import os
import time
from pathlib import Path

from src.utils.cleanup import prune_output


def _make_entry(root: Path, name: str, size_bytes: int, age_days: float) -> Path:
    d = root / name
    d.mkdir(parents=True)
    f = d / "payload.bin"
    f.write_bytes(b"x" * size_bytes)
    old = time.time() - age_days * 86400
    os.utime(f, (old, old))
    os.utime(d, (old, old))
    return d


def test_dry_run_no_borra_nada(tmp_path: Path) -> None:
    _make_entry(tmp_path, "viejo", 1000, age_days=90)
    report = prune_output(tmp_path, keep_days=30, max_gb=0, apply=False)
    assert len(report.deleted) == 1
    assert (tmp_path / "viejo").exists()
    assert report.applied is False


def test_poda_por_antiguedad(tmp_path: Path) -> None:
    _make_entry(tmp_path, "viejo", 1000, age_days=90)
    _make_entry(tmp_path, "reciente", 1000, age_days=1)
    report = prune_output(tmp_path, keep_days=30, max_gb=0, apply=True)
    assert not (tmp_path / "viejo").exists()
    assert (tmp_path / "reciente").exists()
    assert report.freed_bytes == 1000


def test_poda_por_presupuesto_mas_viejo_primero(tmp_path: Path) -> None:
    _make_entry(tmp_path, "a_mas_viejo", 600_000_000, age_days=10)
    _make_entry(tmp_path, "b_medio", 600_000_000, age_days=5)
    _make_entry(tmp_path, "c_nuevo", 600_000_000, age_days=1)
    # total 1.8 GB, presupuesto 1.3 GB -> debe caer solo el mas viejo
    report = prune_output(tmp_path, keep_days=0, max_gb=1.3, apply=True)
    assert not (tmp_path / "a_mas_viejo").exists()
    assert (tmp_path / "b_medio").exists()
    assert (tmp_path / "c_nuevo").exists()
    assert len(report.deleted) == 1


def test_keep_days_cero_desactiva_antiguedad(tmp_path: Path) -> None:
    _make_entry(tmp_path, "viejo", 1000, age_days=365)
    report = prune_output(tmp_path, keep_days=0, max_gb=0, apply=True)
    assert (tmp_path / "viejo").exists()
    assert report.deleted == []


def test_failed_delete_is_not_reported_as_freed(tmp_path: Path, monkeypatch) -> None:
    import src.utils.cleanup as cleanup_module

    entry = _make_entry(tmp_path, "bloqueado", 1000, age_days=90)
    monkeypatch.setattr(
        cleanup_module.shutil,
        "rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("locked")),
    )

    report = prune_output(tmp_path, keep_days=30, max_gb=0, apply=True)

    assert entry.exists()
    assert report.deleted == []
    assert report.freed_bytes == 0
