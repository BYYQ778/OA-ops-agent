# -*- coding: utf-8 -*-
"""生成 OA运维Agent 桌面图标 oa_agent.ico（一次性工具）"""
from PIL import Image, ImageDraw, ImageFont

SIZE = 256
img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 靛蓝竖向渐变背景
for y in range(SIZE):
    t = y / SIZE
    r = int(79 + (49 - 79) * t)
    g = int(70 + (46 - 70) * t)
    b = int(229 + (164 - 229) * t)
    d.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

# 圆角遮罩
mask = Image.new("L", (SIZE, SIZE), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=52, fill=255)
img.putalpha(mask)
d = ImageDraw.Draw(img)

# 白色盾牌
cx = SIZE // 2
d.polygon([(54, 36), (202, 36), (202, 118), (cx, 172), (54, 118)], fill=(255, 255, 255, 255))

# 盾内对勾（靛蓝）
d.line([(92, 100), (120, 128), (168, 74)], fill=(79, 70, 229, 255), width=13, joint="curve")

# 文字 OA
try:
    font = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 42)
except Exception:
    font = ImageFont.load_default()
txt = "OA"
bbox = d.textbbox((0, 0), txt, font=font)
tw = bbox[2] - bbox[0]
th = bbox[3] - bbox[1]
d.text((cx - tw / 2 - bbox[0], 218 - th / 2 - bbox[1]), txt, font=font, fill=(255, 255, 255, 255))

out = "E:/YunweiAgent/oa-ops-agent/oa_agent.ico"
img.save(out, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print("icon saved:", out)