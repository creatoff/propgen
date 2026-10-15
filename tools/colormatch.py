"""Подгонка цвета запечённой текстуры под исходные виды (перенос среднего и разброса в Lab по самому предмету).
TRELLIS/Pixal3D при декодировании текстуры уводят цвет (жёлтая эмаль → оранжевая), это возвращает его к фото.

.venv/Scripts/python tools/colormatch.py texture.png ref1.png[,ref2.png…] [--strength 0.85]
Эталоны — вырезки на чёрном фоне (mesh.py сохраняет их как <name>_in_*.png). Текстура перезаписывается.
"""
import argparse

import numpy as np
from PIL import Image
from skimage import color

ap = argparse.ArgumentParser()
ap.add_argument("tex")
ap.add_argument("refs")
ap.add_argument("--strength", type=float, default=0.85)
a = ap.parse_args()


def fg_lab(path, dark=20):
    px = np.asarray(Image.open(path).convert("RGB")).reshape(-1, 3)
    px = px[px.sum(1) > dark * 3]  # чёрный фон вырезки и пустое место атласа — мимо
    return color.rgb2lab(px[None].astype(np.float32) / 255)[0]


ref = np.concatenate([fg_lab(p) for p in a.refs.split(",") if p])
img = np.asarray(Image.open(a.tex).convert("RGB")).astype(np.float32) / 255
lab = color.rgb2lab(img)
mask = img.sum(-1) > 20 * 3 / 255
src = lab[mask]
m_s, s_s, m_r, s_r = src.mean(0), src.std(0) + 1e-6, ref.mean(0), ref.std(0)
out = (lab - m_s) / s_s * s_r + m_r
out = lab + (out - lab) * a.strength
res = np.clip(color.lab2rgb(out), 0, 1)
res[~mask] = img[~mask]
Image.fromarray((res * 255 + 0.5).astype(np.uint8)).save(a.tex)
print(f"COLOR src L/a/b {m_s.round(1)} -> ref {m_r.round(1)}")
