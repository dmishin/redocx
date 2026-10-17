"""Draw the pictures of the magazine example (run from the repository root):

    python examples/magazine/img/make_images.py

They are generated so the example carries no third-party images.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).parent
TEAL, DEEP, CORAL, SAND = (11, 122, 117), (27, 42, 65), (209, 73, 91), (238, 242, 246)


def cover() -> None:
    """An A4 cover (1240 x 1754 px at 150 dpi): a dusk sky over a harbour of light trails."""
    w, h = 1240, 1754
    img = Image.new("RGB", (w, h))
    px = img.load()
    assert px is not None
    for y in range(h):
        t = y / h
        sky = tuple(round(DEEP[i] * (1 - t) + TEAL[i] * t) for i in range(3))
        for x in range(w):
            px[x, y] = sky
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(26):  # light trails sweeping across the lower half
        y0 = 980 + i * 26
        points = [(x, y0 + 60 * math.sin(x / 210 + i / 3)) for x in range(-20, w + 40, 20)]
        color = CORAL if i % 3 == 0 else (255, 214, 170)
        draw.line(points, fill=(*color, 70 + (i % 4) * 30), width=4 + i % 3)
    draw.ellipse((820, 260, 1040, 480), fill=(255, 220, 180, 210))  # a low sun
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    img.save(HERE / "cover.png", dpi=(150, 150))


def skyline() -> None:
    """A wide figure: a city skyline with traffic below (1200 x 500 px)."""
    w, h = 1200, 500
    img = Image.new("RGB", (w, h), SAND)
    draw = ImageDraw.Draw(img)
    x = 0
    i = 0
    while x < w:
        width = 40 + (i * 37) % 90
        height = 120 + (i * 71) % 260
        draw.rectangle((x, h - 140 - height, x + width - 6, h - 140), fill=DEEP if i % 2 else TEAL)
        x += width
        i += 1
    draw.rectangle((0, h - 140, w, h), fill=(60, 66, 80))
    for lane in range(3):
        y = h - 110 + lane * 35
        for car in range(18):
            cx = (car * 71 + lane * 23) % w
            draw.rounded_rectangle((cx, y, cx + 34, y + 16), 4, fill=CORAL if car % 4 else (255, 214, 170))
    img.save(HERE / "skyline.png", dpi=(150, 150))


def portrait() -> None:
    """A square figure: a ring of sensors (600 x 600 px)."""
    s = 600
    img = Image.new("RGB", (s, s), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((60, 60, s - 60, s - 60), outline=TEAL, width=40)
    for k in range(12):
        a = k * math.pi / 6
        x, y = s / 2 + 240 * math.cos(a), s / 2 + 240 * math.sin(a)
        draw.ellipse((x - 22, y - 22, x + 22, y + 22), fill=CORAL)
    draw.ellipse((s / 2 - 60, s / 2 - 60, s / 2 + 60, s / 2 + 60), fill=DEEP)
    img.save(HERE / "sensors.png", dpi=(150, 150))


if __name__ == "__main__":
    cover()
    skyline()
    portrait()
