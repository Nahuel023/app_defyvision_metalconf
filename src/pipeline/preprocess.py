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
    blur_ksize: int = 5,
    open_ksize: int = 3,
    close_ksize: int = 5,
    use_adaptive: bool = False,
    adaptive_block_size: int = 61,
    adaptive_c: float = -5.0,
) -> np.ndarray:
    """
    Devuelve una máscara binaria donde los agujeros quedan en blanco (255).

    use_clahe: aplica ecualización adaptativa (CLAHE) antes de umbralizar.
               Ideal para iluminación no uniforme (backlight lateral).
    use_adaptive: umbral adaptativo local (cv2.adaptiveThreshold gaussiano) en lugar
               de Otsu/umbral fijo global. Captura agujeros tenues en zonas más oscuras
               o levemente desenfocadas donde un único umbral global los pierde.
               Tiene precedencia sobre use_otsu. adaptive_block_size debe ser impar.
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

    ksize = blur_ksize if blur_ksize % 2 == 1 else blur_ksize + 1
    blur = cv2.GaussianBlur(channel, (ksize, ksize), 0)
    thresh_mode = cv2.THRESH_BINARY if polarity == "bright" else cv2.THRESH_BINARY_INV
    if use_adaptive:
        block = int(adaptive_block_size)
        if block % 2 == 0:
            block += 1
        block = max(3, block)
        th = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_mode,
            block, float(adaptive_c),
        )
    elif use_otsu:
        _, th = cv2.threshold(blur, 0, 255, thresh_mode | cv2.THRESH_OTSU)
    else:
        _, th = cv2.threshold(blur, threshold, 255, thresh_mode)

    if open_ksize > 0:
        kernel_open = np.ones((open_ksize, open_ksize), np.uint8)
        th = cv2.morphologyEx(th, cv2.MORPH_OPEN, kernel_open, iterations=1)

    if close_ksize > 0:
        kernel_close = np.ones((close_ksize, close_ksize), np.uint8)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel_close, iterations=1)

    return th
