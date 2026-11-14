"""把 assets/icon-source.webp 做成 macOS 图标 → assets/AppIcon.icns、ui/favicon.png。

macOS 26 起，不铺满画布的图标会被系统放进一个灰色圆角底板里；所以这里做成「满版」：
只取原图深蓝圆角框里面的内容，铺在整张深蓝底上，圆角交给系统去裁。
用法：uv run --with pillow python scripts/make_icon.py
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
BG = (26, 57, 119)        # 原图框内的深蓝底色
INSET, RADIUS = 110, 240  # 原图（1920px）里浅蓝边框内侧的位置和圆角

src = Image.open(ROOT / "assets/icon-source.webp").convert("RGB")
w, h = src.size
mask = Image.new("L", src.size, 0)
ImageDraw.Draw(mask).rounded_rectangle((INSET, INSET, w - INSET, h - INSET), radius=RADIUS, fill=255)
flat = Image.new("RGB", src.size, BG)
flat.paste(src, (0, 0), mask)
# 四个角上残留的浅蓝边框（蓝明显高于红的亮蓝色）也涂成底色；白字、黄星、灰色机器都不满足这个条件
px = flat.load()
C = 520
for x in range(w):
    for y in range(h):
        if (x < C or x >= w - C) and (y < C or y >= h - C):
            r, g, bl = px[x, y]
            if bl > 130 and bl - r > 60:
                px[x, y] = BG
flat = flat.crop((INSET, INSET, w - INSET, h - INSET))

# 满版 1024 画布，内容缩到 90% 居中，四周留一点深蓝
canvas = Image.new("RGB", (1024, 1024), BG)
body = flat.resize((922, 922), Image.LANCZOS)
canvas.paste(body, ((1024 - 922) // 2, (1024 - 922) // 2))
canvas.save(ROOT / "assets/icon-1024.png")

# 网页小图标：自己裁成圆角（浏览器不会帮忙裁）
fav = canvas.resize((64, 64), Image.LANCZOS).convert("RGBA")
fm = Image.new("L", (64, 64), 0)
ImageDraw.Draw(fm).rounded_rectangle((0, 0, 63, 63), radius=14, fill=255)
fav.putalpha(fm)
fav.save(ROOT / "ui/favicon.png")

iconset = ROOT / "assets/AppIcon.iconset"
iconset.mkdir(exist_ok=True)
for size in (16, 32, 128, 256, 512):
    canvas.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
    canvas.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "assets/AppIcon.icns")], check=True)
print("ok")
