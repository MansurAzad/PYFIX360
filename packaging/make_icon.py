"""Build the PYFIX 360 application icon from the committed master artwork.

``packaging/logo_master.png`` is the source of truth: a 512x512 transparent
PNG of the mark (a dark rounded tile, a blue orbit ring and a white check).
This script turns it into the multi-resolution ``pyfix360.ico`` that Windows
uses for the executable, the shortcuts, the taskbar and the notification area,
plus the preview images used in the README.

Everything here is deterministic and needs nothing but Pillow, so anyone who
checks out the repository can rebuild the icon byte for byte:

    python packaging/make_icon.py

The master is drawn by hand and stored as a raster; it is not generated at
build time, because a build that depends on an image model is not a build
anyone else can repeat.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

OUT_DIR = Path(__file__).resolve().parent
MASTER = OUT_DIR / "logo_master.png"

#: sizes Windows expects in a multi-resolution .ico
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

#: extra-large render for documentation
PREVIEW_SIZE = 512


def _sharpen(img: Image.Image, size: int) -> Image.Image:
    """Crisp up the small renders.

    Downscaling averages the blue ring and the dark tile together, which turns
    the edge muddy at 16-32 pixels. A light unsharp mask brings the contrast
    back without inventing detail.
    """
    if size > 48:
        return img
    return img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=110, threshold=2))


def render(master: Image.Image, size: int) -> Image.Image:
    """The icon at one square size."""
    return _sharpen(master.resize((size, size), Image.LANCZOS), size)


def main() -> None:
    master = Image.open(MASTER).convert("RGBA")

    frames = [render(master, n) for n in ICO_SIZES if n < 256]
    big = render(master, 256)
    ico_path = OUT_DIR / "pyfix360.ico"
    # Pillow writes every provided size into one .ico
    big.save(ico_path, format="ICO",
             sizes=[(n, n) for n in ICO_SIZES], append_images=frames)

    render(master, PREVIEW_SIZE).save(OUT_DIR / "pyfix360_icon_preview.png",
                                      format="PNG")
    # a strip showing the small sizes, to prove legibility
    strip = Image.new("RGBA", (sum(ICO_SIZES[:5]) + 4 * 6, 64), (0, 0, 0, 0))
    x = 0
    for n in ICO_SIZES[:5]:
        frame = render(master, n)
        strip.paste(frame, (x, 64 - n - 8), frame)
        x += n + 6
    strip.save(OUT_DIR / "pyfix360_icon_sizes.png", format="PNG")

    print(f"wrote {ico_path}")
    print(f"wrote {OUT_DIR / 'pyfix360_icon_preview.png'}")
    print(f"wrote {OUT_DIR / 'pyfix360_icon_sizes.png'}")


if __name__ == "__main__":
    main()
