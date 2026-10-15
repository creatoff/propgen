"""Иконка инсталлятора и ярлыков: installer/propgen.ico — тот же знак, что .logo в web/style.css
(шестиугольник, linear-gradient(160deg, #ffc061, #ff8a0c)). Готовый .ico лежит в репозитории, чтобы сборка
не зависела от Pillow; этот скрипт — чтобы перерисовать его при смене логотипа:

    .venv\\Scripts\\python tools\\build\\make_icon.py
"""
import math
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "installer", "propgen.ico")
C0, C1 = (0xFF, 0xC0, 0x61), (0xFF, 0x8A, 0x0C)
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def render(size, ss=4):
    """Рисуем с запасом (ss×) и уменьшаем: у ImageDraw.polygon нет сглаживания краёв."""
    n = size * ss
    # градиент как в CSS: угол 160° от «вверх» по часовой, длина линии — |w·sin a| + |h·cos a|
    a = math.radians(160)
    dx, dy = math.sin(a), -math.cos(a)
    half = (abs(n * dx) + abs(n * dy)) / 2
    grad = Image.new("RGB", (n, n))
    px = grad.load()
    for y in range(n):
        for x in range(n):
            t = ((x + 0.5 - n / 2) * dx + (y + 0.5 - n / 2) * dy) / (2 * half) + 0.5
            px[x, y] = tuple(round(c0 + (c1 - c0) * t) for c0, c1 in zip(C0, C1))
    # clip-path: polygon(50% 0, 100% 25%, 100% 75%, 50% 100%, 0 75%, 0 25%); мелкие размеры — с полем в 1/16,
    # иначе шестиугольник упирается в край и в панели задач выглядит обрезанным
    m = n / 16
    w = n - 2 * m
    pts = [(m + w * u, m + w * v) for u, v in ((.5, 0), (1, .25), (1, .75), (.5, 1), (0, .75), (0, .25))]
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    img = grad.convert("RGBA")
    img.putalpha(mask)
    return img.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    big = render(256)
    # каждый размер рисуется отдельно (а не ужимается из 256) — края на 16–32 px остаются чёткими
    imgs = {s: render(s, ss=max(4, 512 // s)) for s in SIZES}
    big.save(OUT, format="ICO", sizes=[(s, s) for s in SIZES], append_images=[imgs[s] for s in SIZES if s != 256])
    print(OUT, os.path.getsize(OUT), "bytes")
