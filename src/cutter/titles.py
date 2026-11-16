"""Titles, credits and end-text cards as transparent 1920x1080 PNG pictures, for editors other than FilmCraft.

FilmCraft draws the titles of filmcraft.json itself. Premiere, Final Cut Pro and Resolve get the same texts as
pictures on a video track of their own (export.py). The look follows filmcraft.json closely (font, size, colour,
position, alignment, shadow) but is not pixel-identical; the editor can replace them with its own titles.

Positions are FilmCraft's: x is the left edge (align left), the centre (center) or the right edge (right) of the text,
y its baseline, in pixels of the 1920x1080 frame.
"""
import functools
import pathlib
import shutil
import subprocess

from .util import CutterError, MISSING

W, H = 1920, 1080


@functools.lru_cache(maxsize=None)
def font_file(family, style=""):
    """The font file for a family and style (fontconfig), e.g. ("P052", "Roman"), ("Noto Sans", "SemiBold")."""
    if shutil.which("fc-match"):
        pattern = f"{family}:style={style}" if style else family
        r = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    for d in ("/usr/share/fonts", "/Library/Fonts", "/System/Library/Fonts"):
        for f in sorted(pathlib.Path(d).rglob("*")) if pathlib.Path(d).is_dir() else []:
            if f.suffix.lower() in (".ttf", ".otf") and family.replace(" ", "").lower() in f.stem.replace("-", "").lower():
                return str(f)
    return None


def _font(family, size, style=""):
    from PIL import ImageFont
    path = font_file(family or "DejaVu Sans", style or "")
    if not path:
        raise CutterError(f"Font not found: {family}", MISSING, "Install it or change the font in filmcraft.json")
    return ImageFont.truetype(path, round(size))


def _rgba(color, opacity=100):
    c = (color or "#ffffff").lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    a = int(c[6:8], 16) if len(c) >= 8 else 255
    return r, g, b, round(a * opacity / 100)


def _anchor(align):
    return {"left": "ls", "center": "ms", "right": "rs"}.get(align or "left", "ls")


def render(layers, path, band=None):
    """Draw text layers (and an optional band behind them) onto a transparent frame and save it as PNG.
    layer: {"text", "font", "fontStyle", "size", "position": [x, y], "align", "fill_color", "line_height",
            "shadow", "shadow_color", "shadow_opacity", "shadow_blur", "shadow_size", "stroke", "stroke_width",
            "stroke_color"}; band: {"center_y", "height", "color", "opacity"}."""
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if band:
        top = band["center_y"] - band["height"] / 2
        ImageDraw.Draw(img).rectangle([0, top, W, top + band["height"]], fill=_rgba(band.get("color", "#000000"),
                                                                                   band.get("opacity", 60)))
    for L in layers:
        font = _font(L.get("font"), L["size"], L.get("fontStyle", ""))
        x, y = L["position"]
        lines = str(L["text"]).split("\n")
        step = L.get("line_height") or round(L["size"] * 1.2)
        anchor = _anchor(L.get("align"))
        if L.get("shadow"):
            shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(shadow)
            off = L.get("shadow_distance", 0)
            for i, line in enumerate(lines):
                d.text((x + off, y + off + i * step), line, font=font, anchor=anchor,
                       fill=_rgba(L.get("shadow_color", "#000000"), L.get("shadow_opacity", 80)),
                       stroke_width=round(L.get("shadow_size", 0)), stroke_fill=_rgba(L.get("shadow_color", "#000000"),
                                                                                     L.get("shadow_opacity", 80)))
            if L.get("shadow_blur"):
                shadow = shadow.filter(ImageFilter.GaussianBlur(L["shadow_blur"] / 2))
            img = Image.alpha_composite(img, shadow)
        d = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            d.text((x, y + i * step), line, font=font, anchor=anchor, fill=_rgba(L.get("fill_color", "#ffffff")),
                   stroke_width=round(L.get("stroke_width", 0)) if L.get("stroke") else 0,
                   stroke_fill=_rgba(L.get("stroke_color", "#000000")))
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return path


def title_layers(t):
    """A filmcraft.json title as one layer: the style overrides the title's own font, size and position."""
    st = t.get("style") or {}
    return [dict(st, text=t["text"], font=st.get("font", t.get("font")), fontStyle=st.get("fontStyle", t.get("fontStyle", "")),
                 size=st.get("size", t["size"]), position=st.get("position", t["position"]),
                 align=st.get("align", t.get("align", "center")))]


def credit_layers(credits, music_lines):
    out = []
    styles = credits.get("styles", {})
    for L in credits["layers"]:
        st = styles[L["style"]]
        out.append(dict(st, text=L["text"].replace("{music_lines}", music_lines), size=L["size"],
                        position=L["position"], line_height=L.get("line_height"), align=L.get("align", st.get("align", "left"))))
    return out


def end_card_layers(card, style, shadow=None):
    """An end-text card: text on a band (see filmcraft.finish)."""
    lines = card["text"].count("\n") + 1
    if lines > 2:
        raise CutterError(f"End-text card with {lines} lines (at most two): {card['text'][:40]}…")
    height = style["band"]["height"][str(lines)]
    base_y = style["center_y"] - 18 if lines == 2 else style["center_y"] + 20
    band = {"center_y": style["center_y"], "height": height, "color": style["band"]["color"],
            "opacity": style["band"]["opacity"]}
    layer = dict(shadow or {}, text=card["text"], font=style["font"], fontStyle=style.get("fontStyle", ""),
                 size=style["size"], position=[960, base_y], align="center", fill_color=style["fill_color"],
                 line_height=round(style["size"] * 1.15))
    return [layer], band
