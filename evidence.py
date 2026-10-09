#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Make the evidence image: mark the camera position, heading wedge and key features on satellite imagery, with street-view comparison panels below.

Input is a JSON spec file:
{
  "map": "area.jpg",                         # output of tiles.py fetch (needs the same-name .json next to it)
  "crop": [x0, y0, x1, y1],                  # optional, crop in original-image pixels
  "width": 1280,                             # output width
  "camera": [lat, lon],
  "heading": 52, "hfov": 54, "range_m": 560, # heading wedge
  "labels": [{"at": [lat, lon], "text": "Xinghe Twin Towers", "color": "#ffdd55", "dx": 0, "dy": 0}],
  "lines":  [{"from": [lat, lon], "bearing": 47, "length_m": 75, "color": "#00ffff"}],
  "panels": [{"image": "sv1.jpg", "caption": "Street view: same road, looking northeast"}]
}

Example: evidence.py spec.json --out evidence.jpg

With --match, a different proof: two images side by side with colour-coded matching lines (photo vs Street View /
reference), to show the same structure feature by feature (balconies, painted stripes, a roof line). Spec:
{
  "left":  {"image": "photo.jpg", "caption": "photo"},
  "right": {"image": "sv.jpg",    "caption": "Street View, closest capture in time"},
  "pairs": [{"color": "#ff5bbf", "label": "balcony", "a": [x,y], "b": [x,y]}, ...],   # a=left px, b=right px
  "height": 700
}
Example: evidence.py match_spec.json --out match.jpg --match
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
import geo  # noqa: E402
from baidu_pano import _font  # noqa: E402
from tiles import Mosaic  # noqa: E402


def build(spec: dict, base: Path, out: Path) -> None:
    map_path = base / spec["map"]
    m = Mosaic(map_path)
    im = Image.open(map_path).convert("RGBA")
    scale_font = max(im.size) / 1400

    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    if "camera" in spec:
        cam = tuple(spec["camera"])
        if "heading" in spec:
            h, fov, rng = spec["heading"], spec.get("hfov", 54), spec.get("range_m", 400)
            pts = [m.to_px(*cam)] + [m.to_px(*geo.dest(cam, h - fov / 2 + k * fov / 24, rng)) for k in range(25)]
            d.polygon(pts, fill=(255, 220, 0, 55), outline=(255, 220, 0, 210))
    for ln in spec.get("lines", []):
        a = m.to_px(*ln["from"])
        b = m.to_px(*geo.dest(tuple(ln["from"]), ln["bearing"], ln["length_m"]))
        d.line([a, b], fill=ln.get("color", "#00ffff"), width=int(8 * scale_font) or 3)
    if "camera" in spec:
        x, y = m.to_px(*spec["camera"])
        r = 18 * scale_font
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 40, 40, 255), outline="white", width=4)
    im = Image.alpha_composite(im, ov).convert("RGB")

    d = ImageDraw.Draw(im)
    f = _font(int(42 * scale_font))
    for lb in spec.get("labels", []):
        ax, ay = m.to_px(*lb["at"])
        r = 8 * scale_font
        d.ellipse([ax - r, ay - r, ax + r, ay + r], fill=lb.get("color", "white"), outline="black")   # anchor point
        x, y = ax + lb.get("dx", 12), ay + lb.get("dy", -12)
        bb = d.textbbox((0, 0), lb["text"], font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        x = min(max(8, x), im.size[0] - tw - 16)                                                   # stay inside the image edges
        y = min(max(8, y), im.size[1] - th - 16)
        d.rectangle([x - 8, y - 6, x + tw + 8, y + th + 10], fill="black")
        d.text((x, y), lb["text"], font=f, fill=lb.get("color", "white"))

    if spec.get("crop"):
        im = im.crop(tuple(spec["crop"]))
    W = spec.get("width", 1280)
    im = im.resize((W, int(im.size[1] * W / im.size[0])))

    panels = spec.get("panels", [])
    if panels:
        pw = W // len(panels)
        ph = int(pw * 3 / 4)
        cf = _font(20)
        lines_per = []
        for p in panels:                                   # wrap captions to the panel width so they don't overlap the neighboring panel
            cap, cur, lines = p.get("caption", ""), "", []
            for ch in cap:
                if cf.getlength(cur + ch) > pw - 12:
                    lines.append(cur)
                    cur = ch
                else:
                    cur += ch
            lines.append(cur)
            lines_per.append(lines[:3])
        cap_h = 10 + 26 * max(len(x) for x in lines_per)
        S = Image.new("RGB", (W, im.size[1] + ph + cap_h), "black")
        S.paste(im, (0, 0))
        d = ImageDraw.Draw(S)
        for k, (p, lines) in enumerate(zip(panels, lines_per)):
            pim = Image.open(base / p["image"]).convert("RGB")
            pim.thumbnail((pw, ph))                        # keep aspect ratio, centered with black borders
            S.paste(pim, (k * pw + (pw - pim.width) // 2, im.size[1] + cap_h + (ph - pim.height) // 2))
            for li, text in enumerate(lines):
                d.text((k * pw + 6, im.size[1] + 6 + 26 * li), text, font=cf, fill="white")
        im = S
    im.save(out, quality=88)



def build_match(spec: dict, base: Path, out: Path) -> None:
    """Two images side by side with colour-coded correspondence lines (the photo vs a Street View / reference): prove the
    same structure with matched features. Each pair draws a dot in its colour on each image and a connector between them,
    so "these balconies, these painted stripes, this roof line are the same" is visible at a glance.

    spec = {"left": {"image": p, "caption": c}, "right": {...},
            "pairs": [{"color": "#ff5bbf", "label": "balcony", "a": [x,y], "b": [x,y]}, ...], "width": 1600}
    a is a pixel in the left image, b a pixel in the right image (original pixels, before scaling)."""
    def load(side):
        im = Image.open(base / side["image"]).convert("RGB")
        return im, im.size
    lim, (lw, lh) = load(spec["left"])
    rim, (rw, rh) = load(spec["right"])
    H = spec.get("height", 700)
    lsc, rsc = H / lh, H / rh
    lim = lim.resize((int(lw * lsc), H)); rim = rim.resize((int(rw * rsc), H))
    gap, cap = 40, 34
    W = lim.width + gap + rim.width
    S = Image.new("RGB", (W, H + cap), (16, 16, 16))
    S.paste(lim, (0, cap)); S.paste(rim, (lim.width + gap, cap))
    d = ImageDraw.Draw(S)
    f = _font(22)
    d.text((6, 6), spec["left"].get("caption", "photo"), font=f, fill="white")
    d.text((lim.width + gap + 6, 6), spec["right"].get("caption", "reference"), font=f, fill="white")
    xoff = lim.width + gap
    r = 6
    for p in spec.get("pairs", []):
        col = p.get("color", "#ffdd33")
        ax, ay = p["a"][0] * lsc, p["a"][1] * lsc + cap
        bx, by = p["b"][0] * rsc + xoff, p["b"][1] * rsc + cap
        d.line([(ax, ay), (bx, by)], fill=col, width=2)
        for cx, cy in ((ax, ay), (bx, by)):
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=col, width=3)
        if p.get("label"):
            d.text((ax + 8, ay - 20), p["label"], font=_font(18), fill=col)
    out.parent.mkdir(parents=True, exist_ok=True)
    S.save(out, quality=90)


def _neg_coords(argv: list[str]) -> list[str]:
    """argparse takes negative coordinates like -1.45,-48.5 for option names; prefixing a space makes them plain values (float ignores the space). Needed for any case in the southern or western hemisphere."""
    return [" " + a if re.match(r"^-\d[\d.]*(,-?[\d.]+)+$", a) else a for a in argv]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--match", action="store_true",
                    help="two images + colour-coded matching lines (photo vs reference) instead of the satellite evidence image")
    args = ap.parse_args(_neg_coords(sys.argv[1:]))
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    (build_match if args.match else build)(spec, args.spec.parent, args.out)
    print(args.out)


if __name__ == "__main__":
    # Chinese Windows outputs GBK by default: it crashes on m², ñ, and Chinese text the agent reads comes out garbled
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    main()
