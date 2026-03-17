import cv2
import numpy as np


def preprocess_for_holes(
    img_bgr_or_gray: np.ndarray,
    threshold: int = 90,
    use_channel: str = "gray",
    polarity: str = "dark",
) -> np.ndarray:
    """
    Devuelve una mascara binaria donde los agujeros quedan en blanco (255).
    """
    if use_channel not in {"gray", "r"}:
        raise ValueError("use_channel debe ser 'gray' o 'r'.")
    if polarity not in {"dark", "bright"}:
        raise ValueError("polarity debe ser 'dark' o 'bright'.")

    if img_bgr_or_gray.ndim == 2:
        gray = img_bgr_or_gray
    elif img_bgr_or_gray.ndim == 3:
        if use_channel == "r":
            gray = img_bgr_or_gray[:, :, 2]
        else:
            gray = cv2.cvtColor(img_bgr_or_gray, cv2.COLOR_BGR2GRAY)
    else:
        raise ValueError("preprocess_for_holes espera una imagen 2D o BGR.")

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    thresh_mode = cv2.THRESH_BINARY if polarity == "bright" else cv2.THRESH_BINARY_INV
    _, th = cv2.threshold(blur, threshold, 255, thresh_mode)

    kernel = np.ones((3, 3), np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=1)

    return th
