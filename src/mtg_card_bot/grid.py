"""Server-side card grid image compositor for multi-card lookups."""

import asyncio
import io
from typing import TYPE_CHECKING

import httpx
from PIL import Image, ImageDraw, ImageFont

from . import logging
from .scryfall import ScryfallClient

if TYPE_CHECKING:
    from .scryfall import Card

logger = logging.with_component("card_grid")

# Card image dimensions for "normal" Scryfall format
CARD_WIDTH = 488
CARD_HEIGHT = 680

# Grid styling
PADDING = 20
BG_COLOR = (30, 31, 34)  # Discord dark theme (#1e1f22)
CORNER_RADIUS = 16
PLACEHOLDER_COLOR = (55, 57, 63)  # Slightly lighter than background
PLACEHOLDER_TEXT_COLOR = (180, 180, 180)
IMAGE_ACCEPT = "image/jpeg,image/png,image/*;q=0.8,*/*;q=0.5"
GRID_IMAGE_FORMATS = ("normal", "large", "small")
GRID_FILENAME = "cards.jpg"


def calculate_grid_layout(count: int) -> tuple[int, int]:
    """Return (columns, rows) for a given card count.

    Optimized for visual balance in Discord embeds on desktop and mobile.
    """
    layouts = {
        1: (1, 1),
        2: (2, 1),
        3: (3, 1),
        4: (2, 2),
        5: (3, 2),
        6: (3, 2),
        7: (4, 2),
        8: (4, 2),
        9: (3, 3),
        10: (5, 2),
    }
    if count in layouts:
        return layouts[count]
    # Fallback for >10: use 5 columns
    rows = (count + 4) // 5
    return (5, rows)


async def _download_image(client: httpx.AsyncClient, url: str) -> Image.Image | None:
    """Download a single card image. Returns None on failure."""
    if not url:
        return None
    try:
        response = await client.get(url)
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        image.load()
        return image.convert("RGBA")
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Card image download failed",
            url=url,
            status=exc.response.status_code,
            response=exc.response.text[:160].replace("\n", " "),
        )
        return None
    except Exception as exc:
        logger.warning("Card image download failed", url=url, error=str(exc))
        return None


def _make_rounded_mask(width: int, height: int, radius: int) -> Image.Image:
    """Create a rounded-rectangle alpha mask."""
    mask = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (width - 1, height - 1)], radius, fill=255)
    return mask


def _make_placeholder(width: int, height: int, card_name: str) -> Image.Image:
    """Create a placeholder image for a card whose image failed to download."""
    img = Image.new("RGBA", (width, height), PLACEHOLDER_COLOR)
    draw = ImageDraw.Draw(img)

    # Draw card name centered
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except OSError:
        font = ImageFont.load_default()

    # Word-wrap the name to fit
    text = card_name if len(card_name) <= 30 else card_name[:27] + "..."
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = (width - text_width) // 2
    y = (height - text_height) // 2
    draw.text((x, y), text, fill=PLACEHOLDER_TEXT_COLOR, font=font)

    return img


def _apply_rounded_corners(img: Image.Image, radius: int) -> Image.Image:
    """Apply rounded corners to an image."""
    # Ensure RGBA
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Resize to expected card dimensions if needed
    if img.size != (CARD_WIDTH, CARD_HEIGHT):
        img = img.resize((CARD_WIDTH, CARD_HEIGHT), Image.Resampling.LANCZOS)

    mask = _make_rounded_mask(CARD_WIDTH, CARD_HEIGHT, radius)
    output = Image.new("RGBA", (CARD_WIDTH, CARD_HEIGHT), (0, 0, 0, 0))
    output.paste(img, (0, 0), mask)
    return output


async def compose_card_grid(cards: list["Card"]) -> io.BytesIO:
    """Compose the front image of each card into one grid image (JPEG bytes)."""
    if not cards:
        raise ValueError("No cards to compose")
    return await compose_images(
        [
            (card.get_display_name(), card.get_best_image_url(GRID_IMAGE_FORMATS))
            for card in cards
        ]
    )


async def compose_card_faces(card: "Card") -> io.BytesIO:
    """Compose both faces of a double-faced card side by side (JPEG bytes)."""
    urls = card.get_face_image_urls(GRID_IMAGE_FORMATS)
    if len(urls) < 2:
        raise ValueError("Card has fewer than two face images")
    names = [face.name for face in card.card_faces] or [card.get_display_name()]
    labels = [names[i] if i < len(names) else names[0] for i in range(len(urls))]
    return await compose_images(list(zip(labels, urls, strict=True)))


async def compose_images(items: list[tuple[str, str]]) -> io.BytesIO:
    """Compose labelled card images into a single grid image.

    Downloads the images concurrently, arranges them in an adaptive grid with
    rounded corners on a dark background, and returns JPEG bytes. Images that
    fail to download become labelled placeholders; if every download fails a
    RuntimeError is raised so callers can fall back to plain embeds.
    """
    if not items:
        raise ValueError("No cards to compose")

    cols, rows = calculate_grid_layout(len(items))

    # Image CDN requests use a dedicated client: cards.scryfall.io has no rate
    # limit and must not queue behind the API client's request spacing.
    async with httpx.AsyncClient(
        timeout=15.0,
        follow_redirects=True,
        headers={
            "User-Agent": ScryfallClient.USER_AGENT,
            "Accept": IMAGE_ACCEPT,
        },
    ) as img_client:
        raw_images = await asyncio.gather(
            *[_download_image(img_client, url) for _, url in items]
        )

    if not any(image is not None for image in raw_images):
        raise RuntimeError("All card image downloads failed")

    return await asyncio.to_thread(_render_grid, items, raw_images, cols, rows)


def _render_grid(
    items: list[tuple[str, str]],
    raw_images: list[Image.Image | None],
    cols: int,
    rows: int,
) -> io.BytesIO:
    """CPU-bound part of grid composition, run off the event loop."""
    card_images: list[Image.Image] = []
    for (label, _), raw in zip(items, raw_images, strict=True):
        source = (
            raw
            if raw is not None
            else _make_placeholder(CARD_WIDTH, CARD_HEIGHT, label)
        )
        card_images.append(_apply_rounded_corners(source, CORNER_RADIUS))

    canvas_width = cols * CARD_WIDTH + (cols + 1) * PADDING
    canvas_height = rows * CARD_HEIGHT + (rows + 1) * PADDING
    canvas = Image.new("RGB", (canvas_width, canvas_height), BG_COLOR)

    cards_in_last_row = len(card_images) % cols
    for i, card_img in enumerate(card_images):
        row, col = divmod(i, cols)
        if row == rows - 1 and cards_in_last_row > 0:
            # Center an incomplete last row
            row_width = (
                cards_in_last_row * CARD_WIDTH + (cards_in_last_row - 1) * PADDING
            )
            x = (canvas_width - row_width) // 2 + col * (CARD_WIDTH + PADDING)
        else:
            x = PADDING + col * (CARD_WIDTH + PADDING)
        y = PADDING + row * (CARD_HEIGHT + PADDING)
        canvas.paste(card_img, (x, y), card_img)

    # JPEG keeps a 10-card grid well under Discord's upload limit and encodes
    # far faster than optimized PNG; the solid background hides artifacts.
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=90, optimize=True)
    buffer.seek(0)
    return buffer
