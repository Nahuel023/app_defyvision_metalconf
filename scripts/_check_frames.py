import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.inspection import inspect_image

FOLDER = Path(r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF_editado")
names = sys.argv[1:] or ["frame_0017","frame_0100","frame_0117","frame_0118","frame_0119"]
for n in names:
    p = FOLDER / f"{n}.png"
    if not p.exists():
        print(f"{n}: NO EXISTE"); continue
    r = inspect_image("modelo_A", p, scanner_id="scanner_2", save=True)
    print(f"{n}: tilt={r.sheet_tilt_deg:+.2f} warn={r.tilt_warn} "
          f"missing={r.report.missing} extra={r.report.extra} "
          f"det={r.report.detected} exp={r.report.expected} "
          f"machine_stop={r.machine_stop} status={r.status}")
