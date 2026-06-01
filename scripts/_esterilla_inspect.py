import cv2, numpy as np, os

SRC = r"C:\Users\DefyC\Downloads\Esterilla_REDUCIDO\frame_0162.png"
OUT = r"C:\Tadeo\METALCONF\app_defyvision_metalconf\data"

img = cv2.imread(SRC)
print("full", img.shape)
roi = img[0:1080, 870:1250]
cv2.imwrite(os.path.join(OUT, "debug_roi_0162.png"), roi)
print("roi", roi.shape)
crop = roi[0:540, :]
crop2 = cv2.resize(crop, (crop.shape[1]*2, crop.shape[0]*2), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(os.path.join(OUT, "debug_roi_top.png"), crop2)
crop_b = roi[540:1080, :]
crop_b2 = cv2.resize(crop_b, (crop_b.shape[1]*2, crop_b.shape[0]*2), interpolation=cv2.INTER_NEAREST)
cv2.imwrite(os.path.join(OUT, "debug_roi_bot.png"), crop_b2)
print("done")
