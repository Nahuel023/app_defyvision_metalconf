import cv2
from pathlib import Path
import numpy as np


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def save_image(path: Path, img: np.ndarray) -> None:
    ensure_dir(path.parent)
    ok = cv2.imwrite(str(path), img)
    if not ok:
        raise RuntimeError(f"No se pudo guardar la imagen en: {path}")