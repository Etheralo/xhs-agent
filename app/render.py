from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont

from .config import Settings


COLORS = [
    (50, 77, 210), (12, 126, 117), (119, 67, 219),
    (196, 79, 34), (25, 105, 160), (70, 83, 108),
]


def _clear_publication_images(target: Path) -> None:
    """Remove only generated publication images from a draft directory."""
    for path in target.glob("xhs-[0-9][0-9].png"):
        if path.is_file():
            path.unlink()


def _font(settings: Settings, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in settings.font_candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for character in paragraph:
            trial = current + character
            if current and draw.textlength(trial, font=font) > width:
                lines.append(current)
                current = character
            else:
                current = trial
        lines.append(current)
    return lines


def _display_text(value: str) -> str:
    # The configured CJK fonts do not all contain color emoji. Keep emoji in the
    # social caption while removing unsupported glyphs from raster cards.
    return "".join(character for character in value if ord(character) <= 0xFFFF and ord(character) != 0xFE0F)


def render_cards(slides: list[dict[str, str]], target: Path, settings: Settings) -> list[Path]:
    target.mkdir(parents=True, exist_ok=True)
    _clear_publication_images(target)
    outputs: list[Path] = []
    for index, slide in enumerate(slides, 1):
        accent = COLORS[(index - 1) % len(COLORS)]
        image = Image.new("RGB", (settings.canvas_width, settings.canvas_height), (247, 248, 252))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((72, 72, settings.canvas_width - 72, settings.canvas_height - 72),
                               radius=42, fill=(255, 255, 255))
        draw.rectangle((72, 72, 94, settings.canvas_height - 72), fill=accent)
        eyebrow_font = _font(settings, 34)
        title_font = _font(settings, 66)
        body_font = _font(settings, 43)
        small_font = _font(settings, 28)
        eyebrow = _display_text(slide["eyebrow"])
        title = _display_text(slide["title"])
        body = _display_text(slide["body"])
        draw.text((130, 145), eyebrow, fill=accent, font=eyebrow_font)
        y = 245
        for line in _wrap(draw, title, title_font, settings.canvas_width - 260):
            draw.text((130, y), line, fill=(23, 32, 51), font=title_font)
            y += 88
        y += 40
        draw.line((130, y, settings.canvas_width - 130, y), fill=(224, 228, 238), width=3)
        y += 55
        for line in _wrap(draw, body, body_font, settings.canvas_width - 260):
            draw.text((130, y), line, fill=(58, 68, 88), font=body_font)
            y += 65
        draw.text((130, settings.canvas_height - 145), "AI Safety Paper Agent",
                  fill=(120, 128, 148), font=small_font)
        draw.text((settings.canvas_width - 205, settings.canvas_height - 145),
                  f"{index:02d} / {len(slides):02d}", fill=accent, font=small_font)
        output = target / f"xhs-{index:02d}.png"
        image.save(output, format="PNG", optimize=True)
        outputs.append(output)
    return outputs


def render_pdf_pages(pdf_path: Path, target: Path, *, page_count: int = 3) -> list[Path]:
    """Render the first PDF pages directly as publication-ready PNG images."""
    if page_count < 1:
        raise ValueError("page_count must be positive")
    target.mkdir(parents=True, exist_ok=True)
    _clear_publication_images(target)
    document = fitz.open(pdf_path)
    try:
        if document.page_count == 0:
            raise ValueError(f"PDF contains no pages: {pdf_path}")
        outputs: list[Path] = []
        for index in range(min(page_count, document.page_count)):
            page = document.load_page(index)
            pixmap = page.get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
            output = target / f"xhs-{index + 1:02d}.png"
            pixmap.save(output)
            outputs.append(output)
        return outputs
    finally:
        document.close()
