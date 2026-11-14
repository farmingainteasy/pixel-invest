"""Pixel Invest logo - the sibling of Pixel Ag's mark.

Pixel Ag: a green sprout grows from soil that dissolves into pixels.
Pixel Invest: a green arrow climbs from a gold coin bar that dissolves into pixels.
Same dark tile, same greens, same pixel trail; gold stands in for the soil.

Run `python logo.py` to regenerate the files in docs/ (SVG + PNG icons).
"""

import os

INK, GREEN_STEM, GREEN, GREEN_LIGHT = "#1C1A14", "#4CA84D", "#57B24A", "#6ECB5E"
GOLD, GOLD_DARK = "#C8A951", "#8A7A46"

# Geometry on a 64 x 64 tile
LINE = [(9, 41), (21, 30), (29, 35), (44, 20)]         # the climb, with one dip
HEAD = [(53, 11), (48.6, 25.2), (38.8, 15.4)]          # arrowhead at the end of the climb
BAR = (6, 48, 16, 9)                                   # x, y, w, h: the gold "coin" bar
PIXELS = [(24, 48, 5, GOLD), (30, 53, 4, GOLD), (36, 48, 4, GOLD), (42, 53, 3, GOLD),
          (47, 49, 3, GOLD), (42, 48, 3, GOLD_DARK), (52, 52, 2.5, GOLD_DARK)]

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")


def svg(tile=True, size=64):
    pts = " ".join(f"{x},{y}" for x, y in LINE)
    head = " ".join(f"{x},{y}" for x, y in HEAD)
    bx, by, bw, bh = BAR
    px = "".join(f'<rect x="{x}" y="{y}" width="{s}" height="{s}" fill="{c}"/>' for x, y, s, c in PIXELS)
    bg = f'<rect width="64" height="64" rx="14" fill="{INK}"/>' if tile else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 64 64" '
            f'role="img" aria-label="Pixel Invest logo: a green arrow climbing from gold coins that dissolve into pixels">'
            f'{bg}<polyline points="{pts}" fill="none" stroke="{GREEN}" stroke-width="5" '
            f'stroke-linecap="round" stroke-linejoin="round"/>'
            f'<polygon points="{head}" fill="{GREEN_LIGHT}" stroke="{GREEN_LIGHT}" stroke-width="1.5" stroke-linejoin="round"/>'
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="1.5" fill="{GOLD}"/>{px}</svg>')


def png(path, size, rounded=False):
    """Draw the same geometry with Pillow (supersampled, then shrunk for smooth edges).
    Phone icons are full-bleed squares: iOS and Android round the corners themselves."""
    from PIL import Image, ImageDraw
    k = size * 4 / 64
    big = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    if rounded:
        d.rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1], radius=14 * k, fill=INK)
    else:
        d.rectangle([0, 0, size * 4, size * 4], fill=INK)
    s = lambda p: [(x * k, y * k) for x, y in p]
    d.line(s(LINE), fill=GREEN, width=round(5 * k), joint="curve")
    for x, y in s([LINE[0], LINE[-1]]):                 # round caps
        r = 2.5 * k
        d.ellipse([x - r, y - r, x + r, y + r], fill=GREEN)
    d.polygon(s(HEAD), fill=GREEN_LIGHT)
    bx, by, bw, bh = BAR
    d.rounded_rectangle([bx * k, by * k, (bx + bw) * k, (by + bh) * k], radius=1.5 * k, fill=GOLD)
    for x, y, w, c in PIXELS:
        d.rectangle([x * k, y * k, (x + w) * k, (y + w) * k], fill=c)
    big.resize((size, size), Image.LANCZOS).save(path)


if __name__ == "__main__":
    os.makedirs(DOCS, exist_ok=True)
    open(os.path.join(DOCS, "logo.svg"), "w").write(svg())
    png(os.path.join(DOCS, "apple-touch-icon.png"), 180)
    png(os.path.join(DOCS, "icon-192.png"), 192)
    png(os.path.join(DOCS, "icon-512.png"), 512)
    print("Wrote docs/logo.svg, apple-touch-icon.png, icon-192.png, icon-512.png")
