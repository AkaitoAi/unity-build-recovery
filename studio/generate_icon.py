"""
Generates an ultra-premium studio-quality icon (.ico and .png) for Unity Recovery Studio.
Features a high-tech geometric cyber-cube with glowing cyan/neon-blue accents and dark slate shading.
"""

import os
from PIL import Image, ImageDraw, ImageFilter

def create_studio_icon(output_ico_path: str, output_png_path: str):
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. Background Rounded Dark Hexagon / Shield
    cx, cy = size // 2, size // 2
    r = 112

    # Draw outer glow
    glow_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_img)
    glow_draw.ellipse([cx - r - 8, cy - r - 8, cx + r + 8, cy + r + 8], fill=(57, 197, 207, 70))
    glow_img = glow_img.filter(ImageFilter.GaussianBlur(10))
    img.paste(glow_img, (0, 0), glow_img)

    # Draw dark base rounded pill/circle
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#161b22", outline="#30363d", width=3)

    # 2. Draw 3D Isometric Cyber Cube / Unity Recovery Crystal
    # Cube points
    top_y = cy - 65
    mid_y = cy - 10
    bot_y = cy + 55
    left_x = cx - 55
    right_x = cx + 55

    # Top Face (Electric Cyan)
    top_poly = [(cx, top_y), (right_x, top_y + 32), (cx, mid_y), (left_x, top_y + 32)]
    draw.polygon(top_poly, fill="#39c5cf", outline="#58a6ff")

    # Left Face (Deep Studio Blue)
    left_poly = [(left_x, top_y + 32), (cx, mid_y), (cx, bot_y), (left_x, bot_y - 32)]
    draw.polygon(left_poly, fill="#1f6feb", outline="#58a6ff")

    # Right Face (Obsidian Violet)
    right_poly = [(cx, mid_y), (right_x, top_y + 32), (right_x, bot_y - 32), (cx, bot_y)]
    draw.polygon(right_poly, fill="#8957e5", outline="#a371f7")

    # 3. Inner Recovery / Infinity Core Symbol
    # Glowing center core
    draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill="#ffffff", outline="#39c5cf", width=2)
    draw.polygon([(cx - 7, cy - 5), (cx + 8, cy), (cx - 7, cy + 5)], fill="#1f6feb")

    # 4. Save PNG
    os.makedirs(os.path.dirname(output_ico_path), exist_ok=True)
    img.save(output_png_path, "PNG")

    # 5. Save multi-resolution ICO (256, 128, 64, 48, 32, 16)
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save(output_ico_path, format="ICO", sizes=sizes)
    print(f"Created Studio Icon: {output_ico_path} and {output_png_path}")

if __name__ == "__main__":
    app_dir = os.path.dirname(os.path.abspath(__file__))
    ico = os.path.join(app_dir, "studio_icon.ico")
    png = os.path.join(app_dir, "studio_icon.png")
    create_studio_icon(ico, png)
