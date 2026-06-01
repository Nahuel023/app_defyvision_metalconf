"""Trim the topmost pattern row (min cj) — unreliable edge row causing top crosses."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.patterns.pattern_io import load_pattern, save_pattern, pattern_path

MODEL = "modelo_A"
for scanner in ("scanner_2", None):
    p = pattern_path(MODEL, scanner)
    pat = load_pattern(p)
    cjs = [cj for _, cj in pat.cells]
    cj_min = min(cjs)
    keep = [i for i, (_, cj) in enumerate(pat.cells) if cj != cj_min]
    removed = len(pat.cells) - len(keep)
    new = pat.__class__(
        model=pat.model, image_size=pat.image_size,
        points=[pat.points[i] for i in keep],
        radii=[pat.radii[i] for i in keep],
        dx=pat.dx, dy=pat.dy, phase_x=pat.phase_x, phase_y=pat.phase_y,
        cells=[pat.cells[i] for i in keep],
        stagger_x_odd=pat.stagger_x_odd,
    )
    save_pattern(new, p)
    print(f"{scanner or 'global'}: removidas {removed} celdas (cj={cj_min}); {len(pat.cells)}->{len(keep)}  -> {p}")
