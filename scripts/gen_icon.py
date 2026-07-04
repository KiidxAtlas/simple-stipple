"""Generate the Simple Stipple app icon as assets/icon.png."""

import math
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
BG = (18, 18, 24)
DOT_COLOR = (180, 140, 255)
DOT_COLOR_DIM = (90, 60, 140)

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# Rounded square background
r = 52
draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=r, fill=BG + (255,))

# Stipple dot grid — vary dot size by distance from center to form a radial gradient
cx, cy = SIZE / 2, SIZE / 2
spacing = 22
dot_base = 4.5

cols = range(int(cx % spacing), SIZE, spacing)
rows = range(int(cy % spacing), SIZE, spacing)

for x in cols:
    for y in rows:
        dist = math.hypot(x - cx, y - cy)
        max_dist = math.hypot(cx, cy)
        t = max(0.0, 1.0 - dist / (max_dist * 0.85))
        radius = dot_base * (0.3 + 0.7 * t)
        alpha = int(60 + 195 * t)
        r_val = int(DOT_COLOR_DIM[0] + (DOT_COLOR[0] - DOT_COLOR_DIM[0]) * t)
        g_val = int(DOT_COLOR_DIM[1] + (DOT_COLOR[1] - DOT_COLOR_DIM[1]) * t)
        b_val = int(DOT_COLOR_DIM[2] + (DOT_COLOR[2] - DOT_COLOR_DIM[2]) * t)
        color = (r_val, g_val, b_val, alpha)
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=color,
        )

assets = Path(__file__).parent.parent / "assets"

out_png = assets / "icon.png"
img.save(out_png, "PNG")
print(f"Saved {out_png}")

# ICO for Windows — embed standard sizes so Explorer/taskbar look sharp at every size
ico_sizes = [16, 32, 48, 256]
ico_frames = [img.resize((s, s), Image.Resampling.LANCZOS) for s in ico_sizes]
out_ico = assets / "icon.ico"
ico_frames[0].save(
    out_ico,
    format="ICO",
    sizes=[(s, s) for s in ico_sizes],
    append_images=ico_frames[1:],
)
print(f"Saved {out_ico}")
