"""Analiza todas las grabaciones en data/recordings y reporta errores por tipo."""
from pathlib import Path
from src.inspection import inspect_image


def analyze(rec: Path) -> None:
    frames = sorted(rec.glob("*.png"))
    if not frames:
        return

    print(f"\n{'='*60}")
    print(f"{rec.name}  ({len(frames)} frames)")
    print(f"{'='*60}")

    results = []
    for p in frames:
        results.append(inspect_image("modelo_A", p))

    ok  = sum(1 for r in results if r.status == "OK")
    nok = len(results) - ok
    missing_vals = [len(r.report.missing_points) for r in results]
    shift_y_abs  = [abs(r.shift_xy[1]) if r.shift_xy else 0.0 for r in results]

    print(f"  Resumen     : {ok} OK / {nok} NOK")
    print(f"  Missing     : min={min(missing_vals)}  max={max(missing_vals)}"
          f"  promedio={sum(missing_vals)/len(missing_vals):.1f}")
    print(f"  Shift|Y| max: {max(shift_y_abs):.0f} px")

    # Clasificar frames
    in_motion    = [i for i, r in enumerate(results)
                    if r.shift_xy and abs(r.shift_xy[1]) > 50]
    no_transform = [i for i, r in enumerate(results) if r.shift_xy is None]
    rest_nok     = [i for i, r in enumerate(results)
                    if r.status == "NOK"
                    and (r.shift_xy is None or abs(r.shift_xy[1]) <= 50)]

    if in_motion:
        print(f"\n  [MOVIMIENTO] {len(in_motion)} frames con |shiftY|>50px"
              f"  (esperado durante avance)")

    if no_transform:
        print(f"\n  [SIN ALINEACION] {len(no_transform)} frames — transform=None")
        for i in no_transform:
            r = results[i]
            print(f"    frame_{i:04d}: det={len(r.holes)}  missing={len(r.report.missing_points)}")

    if rest_nok:
        print(f"\n  [NOK EN REPOSO] {len(rest_nok)} frames (|shiftY|<=50px)")
        for i in rest_nok[:8]:
            r = results[i]
            sx = r.shift_xy[0] if r.shift_xy else 0.0
            sy = r.shift_xy[1] if r.shift_xy else 0.0
            miss_y = sorted(round(y, 0) for _, y in r.report.missing_points)
            print(f"    frame_{i:04d}: det={len(r.holes)}  missing={len(r.report.missing_points)}"
                  f"  shift=({sx:+.1f},{sy:+.1f})  miss_Y={miss_y[:6]}")
        if len(rest_nok) > 8:
            print(f"    ... y {len(rest_nok)-8} frames mas")

    if not rest_nok and not no_transform:
        print("\n  Todos los NOK son frames en movimiento (avance). Sin errores en reposo.")


def main() -> None:
    base = Path("data/recordings")
    for rec in sorted(base.iterdir()):
        if rec.is_dir():
            analyze(rec)


if __name__ == "__main__":
    main()
