"""Generate YouTube thumbnail using Pillow."""

from __future__ import annotations

from pathlib import Path

from logger import get_logger
from meta.meta_generator import VideoMetadata

log = get_logger(__name__)

# Thumbnail dimensions (YouTube recommended)
WIDTH, HEIGHT = 1280, 720

# Color palette
BG_COLOR = (10, 12, 20)          # Near-black
ACCENT_COLOR = (0, 200, 255)      # Cyan
HEADLINE_COLOR = (255, 255, 255)  # White
SUBTEXT_COLOR = (180, 180, 200)   # Light grey
DATE_COLOR = (0, 200, 255)        # Cyan
GRADIENT_TOP = (20, 30, 60)
GRADIENT_BOT = (5, 10, 20)


def _draw_gradient(draw, width: int, height: int) -> None:
    from PIL import ImageDraw  # noqa: F401

    for y in range(height):
        t = y / height
        r = int(GRADIENT_TOP[0] * (1 - t) + GRADIENT_BOT[0] * t)
        g = int(GRADIENT_TOP[1] * (1 - t) + GRADIENT_BOT[1] * t)
        b = int(GRADIENT_TOP[2] * (1 - t) + GRADIENT_BOT[2] * t)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _get_font(size: int, bold: bool = False):
    from PIL import ImageFont

    font_paths_bold = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    font_paths_regular = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    paths = font_paths_bold if bold else font_paths_regular
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def generate_thumbnail(
    metadata: VideoMetadata, date_str: str, output_dir: Path
) -> Path:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (WIDTH, HEIGHT), BG_COLOR)
    draw = ImageDraw.Draw(img)
    _draw_gradient(draw, WIDTH, HEIGHT)

    # Accent bar (left side)
    draw.rectangle([(40, 60), (52, HEIGHT - 60)], fill=ACCENT_COLOR)

    # Date badge
    date_font = _get_font(32)
    draw.rectangle([(68, 60), (320, 110)], fill=ACCENT_COLOR)
    draw.text((80, 68), date_str, font=date_font, fill=BG_COLOR)

    # Main headline
    headline_font = _get_font(88, bold=True)
    headline = metadata.thumbnail_headline
    # Wrap if too long
    if len(headline) > 12:
        mid = len(headline) // 2
        headline_lines = [headline[:mid], headline[mid:]]
    else:
        headline_lines = [headline]

    y_start = 150
    for line in headline_lines:
        draw.text((68, y_start), line, font=headline_font, fill=HEADLINE_COLOR)
        bbox = draw.textbbox((0, 0), line, font=headline_font)
        y_start += (bbox[3] - bbox[1]) + 12

    # Subtext (key topics)
    if metadata.thumbnail_subtext:
        subtext_font = _get_font(44)
        draw.text((68, y_start + 20), metadata.thumbnail_subtext, font=subtext_font, fill=SUBTEXT_COLOR)

    # Bottom accent bar
    draw.rectangle([(0, HEIGHT - 80), (WIDTH, HEIGHT)], fill=(0, 0, 0, 180))
    channel_font = _get_font(36)
    draw.text((68, HEIGHT - 60), "DailyCatchUp – AI & Game News", font=channel_font, fill=ACCENT_COLOR)

    # Grid lines for tech aesthetic
    for x in range(0, WIDTH, 120):
        draw.line([(x, 0), (x, HEIGHT)], fill=(255, 255, 255, 8), width=1)
    for y in range(0, HEIGHT, 120):
        draw.line([(0, y), (WIDTH, y)], fill=(255, 255, 255, 8), width=1)

    output_path = output_dir / "thumbnail.png"
    img.save(output_path, "PNG", optimize=True)
    log.info("Thumbnail saved to %s", output_path)
    return output_path
