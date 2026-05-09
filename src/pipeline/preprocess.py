import cv2
import numpy as np

_VALID_CHANNELS = {"gray", "r", "g", "b"}


def preprocess_for_holes(
    img_bgr_or_gray: np.ndarray,
    threshold: int = 90,
    use_channel: str = "gray",
    polarity: str = "dark",
    use_clahe: bool = False,
    clahe_clip: float = 2.0,
    clahe_tile: int = 8,
    use_otsu: bool = False,
) -> np.ndarray:
    """
    Devuelve una máscara binaria donde los agujeros quedan en blanco (255).

    use_clahe: aplica ecualización adaptativa (CLAHE) antes de umbralizar.
               Ideal para iluminación no uniforme (backlight lateral).
    """
    if use_channel not in _VALID_CHANNELS:
        raise ValueError(f"use_channel debe ser uno de {_VALID_CHANNELS}.")
    if polarity not in {"dark", "bright"}:
        raise ValueError("polarity debe ser 'dark' o 'bright'.")

    if img_bgr_or_gray.ndim == 2:
        channel = img_bgr_or_gray
    elif img_bgr_or_gray.ndim == 3:
        if use_channel == "r":
            channel = img_bgr_or_gray[:, :, 2]
        elif use_channel == "g":
            channel = img_bgr_or_gray[:, :, 1]
        elif use_channel == "b":
            channel = img_bgr_or_gray[:, :, 0]
        else:
            channel = cv2.cvtColor(img_bgr_or_gray, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("preprocess_for_holes espera una imagen 2D o BGR.")

    if use_clahe:
        clahe = cv2.createCLAHE(
            clipLimit=clahe_clip,
            tileGridSize=(clahe_tile, clahe_tile),
        )
        channel = clahe.apply(channel)

    blur = cv2.GaussianBlur(channel, (5, 5), 0)
    thresh_mode = cv2.THRESH_BINARY if polarity == "bright" else cv2.THRESH_BINARY_INV
    if use_otsu:
        _, th = cv2.threshold(blur, 0, 255, thresh_mode | cv2.THRESH_OTSU)
    else:
        _, th = cv2.threshold(blur, threshold, 255, thresh_mode)

    kernel = np.ones((3, 3), np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=1)

    return th
