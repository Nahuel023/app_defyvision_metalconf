from pathlib import Path


SUSPECT_SEQUENCES = (
    "в–",
    "вљ",
    "в-Ћ",
    "Ã¡",
    "Ã©",
    "Ã­",
    "Ã³",
    "Ãº",
    "Ã±",
    "Ã",
    "Ã‰",
    "Ã",
    "Ã“",
    "Ãš",
    "Ã‘",
    "â€“",
    "â€”",
    "â†",
    "âœ",
    "âš",
    "Â·",
    "Â¿",
    "Â¡",
    "�",
)

TEXT_EXTS = {".py", ".md", ".yaml", ".yml", ".txt"}


def test_repository_has_no_mojibake_sequences():
    repo = Path(__file__).resolve().parents[1]
    failures: list[str] = []

    for root_name in ("src", "config", "tests"):
        root = repo / root_name
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in TEXT_EXTS:
                continue
            if path.name == "test_text_encoding.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for lineno, line in enumerate(text.splitlines(), 1):
                if any(seq in line for seq in SUSPECT_SEQUENCES):
                    failures.append(f"{path.relative_to(repo)}:{lineno}: {line}")

    for extra in ("CHANGELOG.md", "AGENTS.md"):
        path = repo / extra
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(seq in line for seq in SUSPECT_SEQUENCES):
                failures.append(f"{path.relative_to(repo)}:{lineno}: {line}")

    assert not failures, "Se encontraron secuencias sospechosas de mojibake:\n" + "\n".join(failures[:50])
