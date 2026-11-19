"""The picture format of a cut: 16:9 for a film, 9:16 for Reels, Shorts and TikTok, and the other common shapes.

A cut's format is "frame" in edit.json, else [timeline] frame in cutter.toml, else 16:9. Either a name from FORMATS
or a size "WIDTHxHEIGHT" (for example "3840x2160"):

    16:9  1920x1080  landscape: YouTube, TV, presentations
    9:16  1080x1920  vertical full screen: Instagram Reels and Stories, TikTok, YouTube Shorts
    4:5   1080x1350  portrait feed post: Instagram, Facebook
    3:4   1080x1440  portrait: Instagram profile grid, phone photos
    1:1   1080x1080  square feed post
    2:3   1080x1620  tall pin: Pinterest
    4:3   1440x1080  classic TV, old footage
    21:9  2560x1080  cinema widescreen

The footage fills the frame (cut off at the sides or top and bottom, see reframe.py for where). Titles, credits and
end-text cards are laid out for 1920x1080 in filmcraft.json and edit.json; Frame.point() and Frame.size() move them
into another format: positions keep their share of the width and height, sizes are relative to the shorter side, and
in 9:16 nothing is placed where the apps put their buttons and captions (SAFE).
"""
import re

from .util import CutterError, USAGE

FORMATS = {
    "16:9": (1920, 1080, "landscape: YouTube, TV, presentations"),
    "9:16": (1080, 1920, "vertical full screen: Instagram Reels and Stories, TikTok, YouTube Shorts"),
    "4:5": (1080, 1350, "portrait feed post: Instagram, Facebook"),
    "3:4": (1080, 1440, "portrait: Instagram profile grid, phone photos"),
    "1:1": (1080, 1080, "square feed post"),
    "2:3": (1080, 1620, "tall pin: Pinterest"),
    "4:3": (1440, 1080, "classic TV, old footage"),
    "21:9": (2560, 1080, "cinema widescreen"),
}
DEFAULT = "16:9"
DESIGN_W, DESIGN_H = 1920, 1080          # the frame titles and cards are laid out for
# share of the height at the top and bottom that the apps cover (profile, buttons, caption) in full-screen vertical
# video: titles, cards and subtitles stay out of it
SAFE = {"9:16": (0.14, 0.22)}
SIZE_RE = re.compile(r"^(\d{2,4})x(\d{2,4})$")


class Frame:
    def __init__(self, name, width, height):
        self.name, self.w, self.h = name, int(width), int(height)

    def __repr__(self):
        return f"Frame({self.name!r}, {self.w}x{self.h})"

    def __eq__(self, other):
        return isinstance(other, Frame) and (self.w, self.h) == (other.w, other.h)

    @property
    def size_text(self):
        return f"{self.w}x{self.h}"

    @property
    def designed(self):
        """True for the frame the titles are laid out for: nothing is moved."""
        return (self.w, self.h) == (DESIGN_W, DESIGN_H)

    @property
    def tall(self):
        return self.h > self.w

    @property
    def safe(self):
        """(top, bottom) in pixels that titles and subtitles leave free."""
        top, bottom = SAFE.get(self.name, (0.0, 0.0))
        return round(top * self.h), round(bottom * self.h)

    @property
    def k(self):
        """Text size relative to the 1920x1080 layout: the shorter side decides."""
        return min(self.w, self.h) / min(DESIGN_W, DESIGN_H)

    def point(self, xy):
        """A position laid out for 1920x1080 in this frame (outside the covered parts in 9:16)."""
        x, y = xy
        if self.designed:
            return [x, y]
        top, bottom = self.safe
        y = min(max(y * self.h / DESIGN_H, top), self.h - bottom)
        return [round(x * self.w / DESIGN_W, 1), round(y, 1)]

    def size(self, s):
        return s if self.designed else round(s * self.k, 1)

    def centre(self):
        return [self.w / 2, self.h / 2] if self.w % 2 or self.h % 2 else [self.w // 2, self.h // 2]

    def review_size(self, short=720):
        """Width and height of a review render: the shorter side `short` lines (even numbers)."""
        f = short / min(self.w, self.h)
        return max(2, round(self.w * f / 2) * 2), max(2, round(self.h * f / 2) * 2)

    def to_json(self):
        return {"name": self.name, "width": self.w, "height": self.h}


def get(spec=None):
    """A Frame from a name ("9:16"), a size ("1080x1920", [1080, 1920], {"width", "height"}) or None (16:9)."""
    if isinstance(spec, Frame):
        return spec
    if spec is None or spec == "":
        spec = DEFAULT
    if isinstance(spec, dict):
        spec = [spec.get("width"), spec.get("height")]
    if isinstance(spec, (list, tuple)) and len(spec) == 2:
        spec = f"{spec[0]}x{spec[1]}"
    spec = str(spec).strip()
    if spec in FORMATS:
        w, h, _ = FORMATS[spec]
        return Frame(spec, w, h)
    m = SIZE_RE.match(spec)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if w % 2 or h % 2 or not (128 <= w <= 8192 and 128 <= h <= 8192):
            raise CutterError(f"frame {spec}: even width and height between 128 and 8192 expected", USAGE)
        name = next((n for n, (fw, fh, _) in FORMATS.items() if (fw, fh) == (w, h)), spec)
        return Frame(name, w, h)
    raise CutterError(f"Unknown frame {spec!r}: one of {', '.join(FORMATS)} or WIDTHxHEIGHT", USAGE)


def of(edit, default=None):
    """The frame of a cut: edit.json "frame", else the project's default."""
    return get((edit or {}).get("frame") or default)
