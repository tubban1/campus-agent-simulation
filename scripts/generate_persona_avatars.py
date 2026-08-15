"""Generate SVG and PNG avatar assets for 10 historical persona figures (IDs 21-30)."""

import os
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

AVATAR_DIR = Path(__file__).resolve().parents[1] / "frontend" / "assets" / "avatars"
AVATAR_DIR.mkdir(parents=True, exist_ok=True)

AVATARS_DEF = [
    {
        "id": "21_confucius",
        "name": "孔子",
        "bg": "#C4D8C4",
        "robe": "#2A3A40",
        "skin": "#F5D0A9",
        "hair": "#1F1F1F",
        "beard": "#808B96",
        "extra_svg": """
          <!-- Topknot / Crown -->
          <rect x="70" y="24" width="20" height="16" rx="4" fill="#1F1F1F"/>
          <path d="M66 40 L94 40 L90 48 L70 48 Z" fill="#D4AC0D"/>
          <!-- Gown Collar -->
          <path d="M55 118 L80 140 L105 118" stroke="#D4AC0D" stroke-width="4" fill="none"/>
          <!-- Beard -->
          <path d="M65 82 Q80 115 95 82 Q80 100 65 82" fill="#808B96"/>
        """
    },
    {
        "id": "22_socrates",
        "name": "苏格拉底",
        "bg": "#EAD0B3",
        "robe": "#FDFBF7",
        "skin": "#F5CBA7",
        "hair": "#D5D8DC",
        "beard": "#D5D8DC",
        "extra_svg": """
          <!-- Curly Hair -->
          <circle cx="50" cy="50" r="14" fill="#D5D8DC"/>
          <circle cx="110" cy="50" r="14" fill="#D5D8DC"/>
          <circle cx="80" cy="36" r="16" fill="#D5D8DC"/>
          <!-- Toga Sash -->
          <path d="M48 118 Q80 135 112 154" stroke="#556B2F" stroke-width="6" fill="none"/>
          <!-- Beard -->
          <path d="M60 84 Q80 120 100 84" fill="#D5D8DC"/>
        """
    },
    {
        "id": "23_buddha",
        "name": "释迦牟尼",
        "bg": "#FAD7A0",
        "robe": "#E67E22",
        "skin": "#F7DC6F",
        "hair": "#4A235A",
        "beard": None,
        "extra_svg": """
          <!-- Halo -->
          <circle cx="80" cy="66" r="46" fill="none" stroke="#F1C40F" stroke-width="4" stroke-dasharray="6,4"/>
          <!-- Ushnisha -->
          <circle cx="80" cy="30" r="10" fill="#4A235A"/>
          <!-- Lotus Mark / Urna -->
          <circle cx="80" cy="56" r="3" fill="#C0392B"/>
          <!-- Robe Shoulder -->
          <path d="M45 154 C48 112 112 112 115 154 Z" fill="#E67E22"/>
          <path d="M45 125 Q70 135 115 120" stroke="#D35400" stroke-width="4" fill="none"/>
        """
    },
    {
        "id": "24_da_vinci",
        "name": "列奥纳多·达·芬奇",
        "bg": "#D5C4A1",
        "robe": "#5B2C2C",
        "skin": "#F5CBA7",
        "hair": "#EAEAEA",
        "beard": "#EAEAEA",
        "extra_svg": """
          <!-- Artist Beret -->
          <path d="M45 42 Q80 20 115 42 Q80 35 45 42 Z" fill="#3B2219"/>
          <!-- Long Hair -->
          <path d="M42 45 C35 70 42 100 48 115 M118 45 C125 70 118 100 112 115" stroke="#EAEAEA" stroke-width="8" stroke-linecap="round" fill="none"/>
          <!-- Long Beard -->
          <path d="M58 84 Q80 130 102 84" fill="#EAEAEA"/>
        """
    },
    {
        "id": "25_shakespeare",
        "name": "威廉·莎士比亚",
        "bg": "#D98880",
        "robe": "#2C3E50",
        "skin": "#F8C471",
        "hair": "#34495E",
        "beard": "#34495E",
        "extra_svg": """
          <!-- Balding Forehead Hair Sides -->
          <path d="M42 55 Q40 80 48 90 M118 55 Q120 80 112 90" stroke="#34495E" stroke-width="8" fill="none"/>
          <!-- Ruff Collar -->
          <path d="M50 112 Q80 125 110 112" stroke="#FFFFFF" stroke-width="10" stroke-linecap="round" fill="none"/>
          <!-- Moustache & Goatee -->
          <path d="M68 78 Q80 84 92 78" stroke="#34495E" stroke-width="3" fill="none"/>
          <polygon points="76,84 84,84 80,94" fill="#34495E"/>
        """
    },
    {
        "id": "26_newton",
        "name": "艾萨克·牛顿",
        "bg": "#A9CCE3",
        "robe": "#1B4F72",
        "skin": "#F5CBA7",
        "hair": "#D5D8DC",
        "beard": None,
        "extra_svg": """
          <!-- 17th C Wig Hair -->
          <path d="M42 45 C30 65 38 100 48 112 M118 45 C130 65 122 100 112 112" stroke="#D5D8DC" stroke-width="12" stroke-linecap="round" fill="none"/>
          <!-- Prism Triangle Icon -->
          <polygon points="120,25 132,45 108,45" fill="none" stroke="#F1C40F" stroke-width="3"/>
          <line x1="120" y1="35" x2="140" y2="30" stroke="#E74C3C" stroke-width="2"/>
        """
    },
    {
        "id": "27_cixi",
        "name": "慈禧太后",
        "bg": "#D7BDE2",
        "robe": "#8E44AD",
        "skin": "#FADBD8",
        "hair": "#1C2833",
        "beard": None,
        "extra_svg": """
          <!-- Qing Headdress (Liangbatou) -->
          <rect x="35" y="25" width="90" height="18" rx="6" fill="#1C2833"/>
          <circle cx="80" cy="24" r="7" fill="#E74C3C"/>
          <circle cx="45" cy="34" r="5" fill="#F1C40F"/>
          <circle cx="115" cy="34" r="5" fill="#F1C40F"/>
          <!-- Silk Collar -->
          <path d="M50 118 L80 138 L110 118" stroke="#F1C40F" stroke-width="5" fill="none"/>
        """
    },
    {
        "id": "28_einstein",
        "name": "阿尔伯特·爱因斯坦",
        "bg": "#D4E6F1",
        "robe": "#5D6D7E",
        "skin": "#FAD7A0",
        "hair": "#F2F4F4",
        "beard": "#F2F4F4",
        "extra_svg": """
          <!-- Wild Hair -->
          <circle cx="42" cy="48" r="18" fill="#F2F4F4"/>
          <circle cx="118" cy="48" r="18" fill="#F2F4F4"/>
          <circle cx="80" cy="32" r="22" fill="#F2F4F4"/>
          <!-- Bushy Moustache -->
          <path d="M62 78 Q80 92 98 78 Q80 84 62 78" fill="#F2F4F4"/>
        """
    },
    {
        "id": "29_hepburn",
        "name": "奥黛丽·赫本",
        "bg": "#FADBD8",
        "robe": "#1C2833",
        "skin": "#FADBD8",
        "hair": "#212F3D",
        "beard": None,
        "extra_svg": """
          <!-- High Bun -->
          <circle cx="80" cy="28" r="16" fill="#212F3D"/>
          <!-- Tiara -->
          <path d="M70 30 L75 22 L80 28 L85 22 L90 30" stroke="#F1C40F" stroke-width="2" fill="none"/>
          <!-- Pearl Necklace -->
          <path d="M60 115 Q80 130 100 115" stroke="#FFFFFF" stroke-dasharray="4,4" stroke-width="5" fill="none"/>
        """
    },
    {
        "id": "30_steve_jobs",
        "name": "史蒂夫·乔布斯",
        "bg": "#D5D8DC",
        "robe": "#212F3D",
        "skin": "#F5CBA7",
        "hair": "#5D6D7E",
        "beard": "#795548",
        "extra_svg": """
          <!-- Round Glasses -->
          <circle cx="66" cy="66" r="10" fill="none" stroke="#2C3E50" stroke-width="3"/>
          <circle cx="94" cy="66" r="10" fill="none" stroke="#2C3E50" stroke-width="3"/>
          <line x1="76" y1="66" x2="84" y2="66" stroke="#2C3E50" stroke-width="3"/>
          <!-- Stubble Beard -->
          <path d="M64 84 Q80 96 96 84" stroke="#795548" stroke-width="3" stroke-dasharray="2,2" fill="none"/>
          <!-- Turtleneck collar -->
          <rect x="64" y="108" width="32" height="16" rx="4" fill="#1C2833"/>
        """
    }
]

def build_svg(item):
    bg = item["bg"]
    skin = item["skin"]
    hair = item["hair"]
    robe = item["robe"]
    extra = item["extra_svg"]
    
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160">
  <rect width="160" height="160" rx="28" fill="{bg}"/>
  <circle cx="80" cy="66" r="34" fill="{skin}"/>
  <path d="M48 62 C50 28 110 24 114 62 C104 42 58 42 48 62 Z" fill="{hair}"/>
  <circle cx="67" cy="68" r="4" fill="#1F2937"/>
  <circle cx="93" cy="68" r="4" fill="#1F2937"/>
  <path d="M70 83 Q80 91 91 83" stroke="#8B3A3A" stroke-width="4" fill="none" stroke-linecap="round"/>
  <path d="M45 154 C48 112 112 112 115 154 Z" fill="{robe}"/>
  {extra}
</svg>
"""
    return svg

def draw_png_fallback(item, png_path):
    img = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Draw rounded rect background
    bg_hex = item["bg"].lstrip('#')
    bg_rgb = tuple(int(bg_hex[i:i+2], 16) for i in (0, 2, 4))
    draw.rounded_rectangle([0, 0, 160, 160], radius=28, fill=bg_rgb)
    
    # Body/Robe
    robe_hex = item["robe"].lstrip('#')
    robe_rgb = tuple(int(robe_hex[i:i+2], 16) for i in (0, 2, 4))
    draw.chord([40, 100, 120, 180], start=180, end=360, fill=robe_rgb)
    
    # Face
    skin_hex = item["skin"].lstrip('#')
    skin_rgb = tuple(int(skin_hex[i:i+2], 16) for i in (0, 2, 4))
    draw.ellipse([46, 32, 114, 100], fill=skin_rgb)
    
    # Eyes
    draw.ellipse([63, 64, 71, 72], fill=(31, 41, 55))
    draw.ellipse([89, 64, 97, 72], fill=(31, 41, 55))
    
    # Hair / Cap
    hair_hex = item["hair"].lstrip('#')
    hair_rgb = tuple(int(hair_hex[i:i+2], 16) for i in (0, 2, 4))
    draw.chord([44, 25, 116, 68], start=180, end=360, fill=hair_rgb)
    
    img.save(png_path, "PNG")

def main():
    for item in AVATARS_DEF:
        avatar_id = item["id"]
        svg_path = AVATAR_DIR / f"{avatar_id}.svg"
        png_path = AVATAR_DIR / f"{avatar_id}.png"
        
        svg_content = build_svg(item)
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_content)
        
        # Try qlmanage to convert SVG to PNG
        res = subprocess.run(["qlmanage", "-t", "-s", "160", "-o", str(AVATAR_DIR), str(svg_path)], capture_output=True)
        converted_png = AVATAR_DIR / f"{avatar_id}.svg.png"
        if converted_png.exists():
            converted_png.rename(png_path)
        else:
            # Fallback PIL drawing
            draw_png_fallback(item, png_path)
            
        print(f"Generated avatar assets: {svg_path.name} & {png_path.name}")

if __name__ == "__main__":
    main()
