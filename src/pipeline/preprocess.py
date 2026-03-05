import cv2
import numpy as np


def preprocess_for_holes(gray: np.ndarray, threshold: int = 90) -> np.ndarray:
    """
    Devuelve una máscara binaria donde los agujeros (oscuros) quedan en blanco (255).
    """
    if gray.ndim != 2:
        raise ValueError("preprocess_for_holes espera una imagen en escala de grises (2D).")

    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(blur, threshold, 255, cv2.THRESH_BINARY_INV)

    kernel = np.ones((3, 3), np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel, iterations=1)

    return th