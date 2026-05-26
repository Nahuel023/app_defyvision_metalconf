"""Diagnóstico del patrón modelo_A (holes.json)."""
import json
import math
from collections import Counter, defaultdict

with open("data/patterns/modelo_A/holes.json") as f:
    data = json.load(f)

points = data["points"]
radii  = data["radii"]
cells  = [tuple(c) for c in data["grid"]["cells"]]
grid   = data["grid"]

print("=== ANALISIS holes.json modelo_A ===")
print(f"  Puntos totales:    {len(points)}")
print(f"  Radios totales:    {len(radii)}")
print(f"  Celdas totales:    {len(cells)}")
unique_cells = set(cells)
print(f"  Celdas unicas:     {len(unique_cells)}")
print(f"  Celdas duplicadas: {len(cells) - len(unique_cells)}")
print()
print(f"  dx={grid['dx']}  dy={grid['dy']}")
print(f"  phase_x={grid['phase_x']}  phase_y={grid['phase_y']}")
print()

dup_map = {c: cnt for c, cnt in Counter(cells).items() if cnt > 1}
if dup_map:
    print("  ADVERTENCIA: celdas con mas de 1 punto asignado:")
    for c, cnt in sorted(dup_map.items(), key=lambda x: x[0][1]):
        idxs = [i for i, cc in enumerate(cells) if cc == c]
        pts  = [points[i] for i in idxs]
        rs   = [radii[i] for i in idxs]
        print(f"    celda (ci={c[0]}, cj={c[1]}) x{cnt}:")
        for idx, p, r in zip(idxs, pts, rs):
            print(f"      idx={idx}  x={p['x']:.1f}  y={p['y']:.1f}  r={r:.1f}")
else:
    print("  Sin celdas duplicadas.")
print()

# Radios
small = [r for r in radii if r < 20]
large = [r for r in radii if r >= 20]
r_small = sum(small) / len(small)
r_large = sum(large) / len(large)
print(f"  Radios pequenos (<20px): {len(small)}  med={r_small:.1f}px  area_med={math.pi*r_small**2:.0f}px2")
print(f"  Radios grandes (>=20px): {len(large)}  med={r_large:.1f}px  area_med={math.pi*r_large**2:.0f}px2")
print()

# Estructura por fila
rows = defaultdict(list)
for (ci, cj), r in zip(cells, radii):
    rows[cj].append((ci, r))
print("  Estructura por fila (cj):")
for cj in sorted(set(cj for ci, cj in cells)):
    items = sorted(rows[cj], key=lambda x: x[0])
    ci_vals = [ci for ci, r in items]
    r_vals  = [r for ci, r in items]
    tipo = "GRANDE" if r_vals[0] >= 20 else "pequeno"
    n = len(ci_vals)
    flag = "  <<< DUPLICADO" if n != len(set(ci_vals)) else ""
    flag = flag or ("  <<< INESPERADO" if (cj % 2 == 0 and n != 5) or (cj % 2 == 1 and n != 4) else "")
    print(f"    cj={cj:2d} ({tipo:7s}): ci={ci_vals}  n={n}{flag}")
print()

# Calculo de X esperadas por ci y phase_x
dx = grid["dx"]
phase_x = grid["phase_x"]
ci_vals_global = sorted(set(ci for ci, cj in unique_cells))
print(f"  Columnas X esperadas (ci={ci_vals_global}):")
for ci in ci_vals_global:
    x_exp = phase_x + ci * dx
    print(f"    ci={ci} -> x_esperada={x_exp:.1f}px")
