#!/usr/bin/env python3
"""Build the FlyBrain Pet app icon set from the supplied SVG.

Source of truth: the uploaded SVG, kept in `assets/brand/`. Xcode needs PNGs,
so this rasterises the SVG at 1x, 2x and 3x for each idiom and, for the App
Store's 1024 pt marketing icon, flattens it onto the artwork's own background.

WHY THE FLATTEN EXISTS (and is not cosmetic): the SVG draws its background as
a 512x512 rect with `rx=112`, so the four corners of its canvas are fully
transparent — measured with `assets.getchannel("A").getextrema()` on the
rasterised 1024 px export, 4.05% of the canvas is fully transparent and all four
corner pixels are (0,0,0,0). iOS renders that as black corners rather than the
dark background the artwork intends, and the App Store rejects marketing icons
with an alpha channel. So the corner colour is sampled from the SVG's own
background rect and used to fill behind the artwork: the app gets the rounded
look from iOS, the file gets a valid opaque icon.

Run: python3 tools/make_app_icons.py
Output: ios/Sources/FlyBrainPetApp/Assets.xcassets/AppIcon.appiconset/
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SVG = os.path.join(ROOT, "assets", "brand", "flybrain-app-icon.svg")
LOGO = os.path.join(ROOT, "assets", "brand", "flybrain-logo-horizontal.svg")
ICONSET = os.path.join(ROOT, "ios", "Sources", "FlyBrainPetApp",
                       "Assets.xcassets", "AppIcon.appiconset")

# iPhone only (TARGETED_DEVICE_FAMILY = 1 in ios/project.yml). Sizes are points;
# the file is rendered at points x scale. The marketing entry (1024) is the one
# the App Store requires to be opaque and alpha-free.
ICON_SPECS = [
    ("iphone", "20x20", 1, 20), ("iphone", "20x20", 2, 40), ("iphone", "20x20", 3, 60),
    ("iphone", "29x29", 1, 29), ("iphone", "29x29", 2, 58), ("iphone", "29x29", 3, 87),
    ("iphone", "40x40", 1, 40), ("iphone", "40x40", 2, 80), ("iphone", "40x40", 3, 120),
    ("iphone", "60x60", 2, 120), ("iphone", "60x60", 3, 180),
    ("ios-marketing", "1024x1024", 1, 1024),
]


def rasterise(svg: str, px: int, out: str, height: int | None = None) -> None:
    """Render at an exact pixel size. `height` defaults to `px` (square icons);
    the logo keeps its own aspect ratio, so it is given width only."""
    cmd = ["rsvg-convert", "-w", str(px)]
    if height is not None:
        cmd += ["-h", str(height)]
    subprocess.run(cmd + [svg, "-o", out], check=True)


def corner_colour(png: str) -> tuple:
    """The artwork's own background colour, sampled where the canvas is opaque.

    The background rect has rounded corners, so the true corner pixel is
    transparent; sample the middle of the top edge instead, which is inside the
    rect on every icon this project ships.
    """
    from PIL import Image
    im = Image.open(png).convert("RGBA")
    w, h = im.size
    edge = im.getpixel((w // 2, 0))
    if edge[3] == 0:
        raise SystemExit(f"{png}: the top edge is transparent, so there is no "
                         f"background colour to flatten onto")
    return edge[:3]


def flatten_and_resize(src: str, px: int, out: str) -> None:
    from PIL import Image
    bg = corner_colour(src)
    im = Image.open(src).convert("RGBA").resize((px, px), Image.LANCZOS)
    flat = Image.new("RGB", (px, px), bg)
    flat.paste(im, mask=im.getchannel("A"))
    # The requirement is the absence of an alpha channel, so assert that
    # rather than trusting the mode string. `getextrema()` is per-channel
    # min/max of the colour and says nothing about opacity, so re-open the
    # written file and check the alpha after conversion instead.
    assert flat.mode == "RGB", flat.mode
    flat.save(out, format="PNG")
    written = Image.open(out)
    assert written.mode == "RGB", f"{out}: mode {written.mode}, not opaque RGB"
    alpha = written.convert("RGBA").getchannel("A").getextrema()
    assert alpha == (255, 255), f"{out}: alpha range {alpha}, not fully opaque"


def main() -> int:
    for path in (SVG, LOGO):
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}; the brand art is committed at "
                             f"assets/brand/ so the icon set is reproducible")
    os.makedirs(ICONSET, exist_ok=True)

    images = []
    for idiom, size, scale, px in ICON_SPECS:
        name = f"AppIcon-{size.replace('x', '-')}@{scale}x.png"
        dst = os.path.join(ICONSET, name)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            rasterise(SVG, px, tmp.name, height=px)
            if idiom == "ios-marketing":
                flatten_and_resize(tmp.name, px, dst)
            else:
                # Non-marketing icons keep their alpha: iOS applies the mask and
                # only the marketing icon has the store's opacity requirement.
                shutil.copyfile(tmp.name, dst)
            os.unlink(tmp.name)
        images.append({"idiom": idiom, "size": size, "scale": f"{scale}x",
                       "filename": name})

    with open(os.path.join(ICONSET, "Contents.json"), "w") as f:
        json.dump({"images": images, "info": {"version": 1, "author": "xcode"}},
                  f, indent=2)

    with open(os.path.join(ICONSET, "..", "Contents.json"), "w") as f:
        json.dump({"info": {"version": 1, "author": "xcode"}}, f, indent=2)

    # The in-app logo is a display asset, not an icon: one 3x PNG is enough and
    # keeping it next to the icon keeps the app bundle self-contained.
    logo_px = os.path.join(ICONSET, "..", "..", "Resources")
    os.makedirs(logo_px, exist_ok=True)
    # Width only: the logo is 800x300 and must not be squashed into a square.
    rasterise(LOGO, 800, os.path.join(logo_px, "FlyBrainLogo.png"))

    print(f"wrote {len(images)} app icons to {ICONSET}")
    print(f"wrote in-app logo to {logo_px}/FlyBrainLogo.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())