import json
import numpy as np

with open("data/patterns/scanner_1/modelo_B/holes.json") as f:
    d = json.load(f)

pts = d["points"]
cells = d.get("cells", [])
dx = d.get("dx")
dy = d.get("dy")
phase_x = d.get("phase_x")
phase_y = d.get("phase_y")
stagger = d.get("stagger_x_odd", 0)

print("dx:", dx, "dy:", dy, "phase_x:", phase_x, "phase_y:", phase_y, "stagger:", stagger)
print("Total cells:", len(cells))

if cells:
    # Encontrar columnas y sus ci
    ci_vals = [c[0] for c in cells]
    cj_vals = [c[1] for c in cells]
    print("CI range:", min(ci_vals), "-", max(ci_vals))
    print("CJ range:", min(cj_vals), "-", max(cj_vals))
    
    # Contar agujeros por ci (columna)
    from collections import Counter
    ci_count = Counter(ci_vals)
    print("\nAgujeros por columna (ci):")
    for ci in sorted(ci_count.keys()):
        # Calcular x esperada para esta columna con phase_x
        exp_x = phase_x + ci * dx
        print(f"  ci={ci}: {ci_count[ci]} agujeros, exp_x={exp_x:.1f}")

    # Cells con max ci
    max_ci = max(ci_vals)
    max_ci_cells = [(c, p) for c, p in zip(cells, pts) if c[0] == max_ci]
    print(f"\nCelulas en max ci={max_ci} ({len(max_ci_cells)} agujeros):")
    for c, p in max_ci_cells[:5]:
        print(f"  ci={c[0]} cj={c[1]} x={p[\"x\"]:.1f} y={p[\"y\"]:.1f}")
