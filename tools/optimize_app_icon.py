"""Optically tune the original rainbow-puzzle app icon without redesigning it.

The source artwork, gloss, shadow, colors, and puzzle silhouette are preserved.
Only canvas placement and size-specific raster clarity are adjusted.
"""

from __future__ import annotations

import io
import shutil
import struct
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PNG_PATH = ASSETS / "photo_manager_icon.png"
ICO_PATH = ASSETS / "photo_manager_icon.ico"
ORIGINAL_PNG_PATH = ASSETS / "photo_manager_icon_original.png"
ORIGINAL_ICO_PATH = ASSETS / "photo_manager_icon_original.ico"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def preserve_originals() -> None:
    """Create immutable source backups once, before the first optimization."""
    if not ORIGINAL_PNG_PATH.exists():
        shutil.copy2(PNG_PATH, ORIGINAL_PNG_PATH)
    if not ORIGINAL_ICO_PATH.exists():
        shutil.copy2(ICO_PATH, ORIGINAL_ICO_PATH)


def build_master(source: Image.Image) -> Image.Image:
    """Place the existing art on a square canvas with mild optical correction."""
    source = source.convert("RGBA")
    alpha_bbox = source.getchannel("A").getbbox()
    if alpha_bbox is None:
        raise ValueError("The source icon has no visible pixels")

    art = source.crop(alpha_bbox)
    # Leave enough transparent air for Windows' taskbar and avoid clipping the
    # original soft shadow.  The artwork remains deliberately large and retro.
    scale = min(920.0 / art.width, 968.0 / art.height)
    art_size = (
        max(1, round(art.width * scale)),
        max(1, round(art.height * scale)),
    )
    art = art.resize(art_size, Image.Resampling.LANCZOS)

    master = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    # Half-correct the measured right/down visual weight.  A full centroid
    # correction would make the asymmetric puzzle bounding box look misplaced.
    x = (1024 - art.width) // 2 - 12
    y = (1024 - art.height) // 2 - 12
    master.alpha_composite(art, (x, y))
    return master


def strengthen_alpha(image: Image.Image, factor: float, cutoff: int) -> Image.Image:
    """Reduce wispy downsampled shadow pixels while retaining antialiasing."""
    image = image.copy().convert("RGBA")
    alpha = image.getchannel("A")

    def tune(value: int) -> int:
        if value <= cutoff:
            return 0
        return max(0, min(255, round(128 + (value - 128) * factor)))

    image.putalpha(alpha.point(tune))
    return image


def build_frame(master: Image.Image, size: int) -> Image.Image:
    """Produce a separately tuned frame instead of blindly scaling the 256 px one."""
    render_size = size - 2 if size <= 48 else size
    rendered = master.resize((render_size, render_size), Image.Resampling.LANCZOS)
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset = (size - render_size) // 2
    frame.alpha_composite(rendered, (inset, inset))
    if size <= 64:
        settings = {
            64: (1.10, 4, 58),
            48: (1.15, 5, 68),
            32: (1.22, 7, 82),
            24: (1.28, 9, 92),
            16: (1.34, 11, 104),
        }
        alpha_factor, cutoff, sharpness = settings[size]
        frame = ImageEnhance.Color(frame).enhance(1.025 if size >= 32 else 1.04)
        frame = ImageEnhance.Contrast(frame).enhance(1.015 if size >= 32 else 1.03)
        frame = frame.filter(
            ImageFilter.UnsharpMask(
                radius=0.55 if size >= 32 else 0.45,
                percent=sharpness,
                threshold=2,
            )
        )
        # Run alpha cleanup last so sharpening cannot recreate a fuzzy one-pixel
        # fringe on the ICO canvas boundary.
        frame = strengthen_alpha(frame, alpha_factor, cutoff)
    return frame


def png_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def write_ico(path: Path, frames: list[tuple[int, Image.Image]]) -> None:
    """Write an ICO with custom PNG data for every optical size."""
    payloads = [(size, png_bytes(image)) for size, image in frames]
    header_size = 6 + 16 * len(payloads)
    offset = header_size
    entries = []
    for size, payload in payloads:
        dimension = 0 if size >= 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)

    with path.open("wb") as stream:
        stream.write(struct.pack("<HHH", 0, 1, len(payloads)))
        for entry in entries:
            stream.write(entry)
        for _, payload in payloads:
            stream.write(payload)


def main() -> None:
    preserve_originals()
    with Image.open(ORIGINAL_PNG_PATH) as source:
        master = build_master(source)

    master.save(PNG_PATH, format="PNG", optimize=True)
    frames = [(size, build_frame(master, size)) for size in ICO_SIZES]
    write_ico(ICO_PATH, frames)

    print(f"PNG: {PNG_PATH} ({master.width}x{master.height})")
    print(f"ICO: {ICO_PATH} ({', '.join(str(size) for size in ICO_SIZES)} px)")
    print(f"Originals: {ORIGINAL_PNG_PATH.name}, {ORIGINAL_ICO_PATH.name}")


if __name__ == "__main__":
    main()
