import json
from pathlib import Path

from src.ui.frame_viewer import _event_summary


def test_event_summary_places_terminal_evidence_between_pre_and_post(tmp_path: Path) -> None:
    event_dir = tmp_path / "event"
    event_dir.mkdir()
    for name in (
        "frame_0000.jpg",
        "frame_0001.jpg",
        "trigger_raw.jpg",
        "trigger_overlay.jpg",
        "post_0000.jpg",
        "post_0000_overlay.jpg",
    ):
        (event_dir / name).write_bytes(b"image")
    (event_dir / "manifest.json").write_text(
        json.dumps({
            "schema_version": 2,
            "trigger_role": "visual_trigger",
            "trigger_raw_file": "trigger_raw.jpg",
            "trigger_overlay_file": "trigger_overlay.jpg",
        }),
        encoding="utf-8",
    )

    summary = _event_summary(event_dir)

    assert [path.name for path in summary["all_frames"]] == [
        "frame_0000.jpg",
        "frame_0001.jpg",
        "trigger_raw.jpg",
        "post_0000.jpg",
    ]
    assert summary["trigger_index"] == 2
    assert summary["trigger_role"] == "visual_trigger"
    assert summary["trigger_overlay"].name == "trigger_overlay.jpg"


def test_legacy_event_is_not_falsely_labeled_as_having_a_trigger(tmp_path: Path) -> None:
    event_dir = tmp_path / "legacy"
    event_dir.mkdir()
    (event_dir / "frame_0000.jpg").write_bytes(b"image")

    summary = _event_summary(event_dir)

    assert summary["trigger_index"] is None
    assert summary["trigger_available"] is False
    assert summary["trigger_role"] == "legacy_event"
