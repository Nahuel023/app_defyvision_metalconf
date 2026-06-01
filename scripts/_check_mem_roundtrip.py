import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cv2
from src.inspection import inspect_image

r = inspect_image("modelo_A", Path(r"C:\Users\DefyC\Downloads\Patron_Esterilla_METALCONF\frame_0162.png"), scanner_id="scanner_2")
ov = r.overlay
print("overlay shape:", None if ov is None else ov.shape)
ok, buf = cv2.imencode(".jpg", ov, [cv2.IMWRITE_JPEG_QUALITY, 92])
print("encode ok:", ok, "jpeg KB:", round(len(buf) / 1024, 1), "vs raw KB:", round(ov.nbytes / 1024, 1))
object.__setattr__(r, "overlay", None)
object.__setattr__(r, "mask", None)
print("freed overlay:", r.overlay, " mask:", r.mask)
dec = cv2.imdecode(buf, cv2.IMREAD_COLOR)
print("decoded shape:", dec.shape)
