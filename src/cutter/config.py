"""Project configuration (cutter.toml): footage folders, music licenses, review app, timeline and retime settings.

The file lives in the project folder (never in the footage). Missing keys come from DEFAULTS. Relative paths are
relative to the project folder; media paths in edit lists are relative to the first footage folder.
Project layout (see README):

    cutter.toml, AGENTS.md, CLAUDE.md
    analysis/            clips.json, transcripts/, sheets/, strips/, image/, proxies/
    music/               music files and music.json (beats, bars, loudness)
    cuts/cut<n>/         edit.json, decisions.json, plan.py, cut<n>.otio/.fcp7.xml/.srt, cut<n>.fcproj, work/
    exports/             rendered films (cut<n>.mp4)
    .cutter/review/      review data (data/) and passwords (htpasswd) – private

A cutter.json with the same keys is accepted as well, and so are older German-keyed configurations
(see docs/formats.md).
"""
import copy
import json
import os
import pathlib
import re

from .util import CutterError, MISSING, USAGE

FILENAMES = ("cutter.toml", "cutter.json")

DEFAULTS = {
    "title": "",
    "ui_language": "",
    "language": "",
    "media": {},
    "project_license": "",
    "paths": {
        "clips": "analysis/clips.json",
        "transcripts": "analysis/transcripts",
        "image_analysis": "analysis/image",
        "proxies": "analysis/proxies",
        "music": "music",
        "music_analysis": "music/music.json",
        "cut": "cuts/{cut}",
        "edit": "cuts/{cut}/edit.json",
        "decisions": "cuts/{cut}/decisions.json",
        "plan": "cuts/{cut}/plan.py",
        "timeline": "cuts/{cut}/{cut}",
        "work": "cuts/{cut}/work",
        "filmcraft_project": "cuts/{cut}/{cut}.fcproj",
        "feedback": "cuts/{cut}/feedback.json",
        "exports": "exports",
        "review": ".cutter/review/data",
        "htpasswd": ".cutter/review/htpasswd",
    },
    "timeline": {
        "otio_namespace": "cutter",
        "music_dirs": ["music"],
        "external_clips": {},
        "track_names": {},
        "texts": {},
    },
    "retime": {
        "fps": 25,
        "cues": {},
        "required_downbeats": None,
        "limits": [],
        "window_s": 1.7,
        "min_duration_s": 0.8,
        "analysis_weight": 0.4,
        "penalty_off_downbeat": 50.0,
        "downbeat_bonus": 0.25,
        "cutaway_snap_s": 0.3,
    },
    "audio": {
        "under_music_db": -10.0,
        "overlap_s": 0.3,
        "ambience_target_lufs": -28.0,
        "ambience_min_db": -12.0,
        "ambience_max_db": 10.0,
        "muted_below_db": -20.0,
        "note": None,
    },
    "music": {
        # CC0 and CC BY by default; add "CC BY-NC" / "CC BY-NC-SA" only for non-commercial films.
        "allowed_licenses": ["CC0", "CC BY"],
        "base_lufs": -16.0,
        "under_dialogue_lufs": -35.0,
        "low_lufs": -40.0,
        "fade_out_s": {},
    },
    "review": {
        "title": "",
        "people": [],
        "agent": "Claude",
        "url": "",
        "ui_language": "",
    },
    "proxies": {
        "make": False,          # true: every cutter analyze makes the missing proxies (as --proxies does)
        "height": 720,
    },
    "filmcraft": {
        "config": "",
        "port": 9876,
        "token_file": "~/.config/cutter/filmcraft_token",
        "export_preset": "Match Source – Adaptive Low Bitrate",
    },
}

LEGACY_KEYS = {"titel": "title", "projekt_lizenz": "project_license", "pfade": "paths", "takt": "retime", "ton": "audio",
               "musik": "music", "material": "media"}


def _merge(base, new):
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def media_name(path, taken=()):
    """Name of a footage root, derived from the folder name: '/x/My Footage' -> 'my-footage'."""
    base = re.sub(r"[^a-z0-9]+", "-", pathlib.Path(str(path)).name.lower()).strip("-") or "media"
    name, i = base, 2
    while name in taken:
        name, i = f"{base}-{i}", i + 1
    return name


def media_table(m):
    """[media] as {name: path}; a plain list (or one path) gets names from the folder names."""
    if isinstance(m, dict):
        return {str(k): str(v) for k, v in m.items()}
    items = [m] if isinstance(m, str) else list(m or [])
    out = {}
    for path in items:
        out[media_name(path, out)] = str(path)
    return out


def cut_name(value):
    """'3' -> 'cut3'. Other names (letters, digits, - _, e.g. cut3 or F7) stay as they are."""
    w = str(value).strip()
    if re.fullmatch(r"\d+", w):
        return f"cut{int(w)}"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", w):
        raise CutterError(f"Invalid cut name: {value!r} (for example cut3)", USAGE)
    return w


def previous_cut(cut):
    """cut3 -> cut2 (also F7 -> F6); None for the first cut or names without a number."""
    m = re.fullmatch(r"(.*?)(\d+)", cut)
    if not m or int(m.group(2)) <= 1:
        return None
    return f"{m.group(1)}{int(m.group(2)) - 1}"


def latest(names):
    """Natural sort order: cut2 < cut10."""
    def key(n):
        return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)]
    return sorted(names, key=key)[-1] if names else None


def read_file(p):
    p = pathlib.Path(p)
    text = p.read_text(encoding="utf-8")
    try:
        if p.suffix == ".toml":
            import tomllib
            return tomllib.loads(text)
        return json.loads(text)
    except Exception as ex:          # tomllib.TOMLDecodeError or ValueError
        raise CutterError(f"{p}: cannot be read ({ex})", MISSING)


class Project:
    def __init__(self, folder, data, file=None):
        self.folder = pathlib.Path(folder).resolve()
        self.file = file
        data = {LEGACY_KEYS.get(k, k): v for k, v in (data or {}).items()}
        self.c = _merge(DEFAULTS, data)
        self.c["media"] = media_table(self.c.get("media"))

    @classmethod
    def load(cls, folder=None, required=False):
        """Find cutter.toml (or cutter.json) in the given folder, in CUTTER_PROJECT, or in the current folder and above."""
        start = pathlib.Path(folder or os.environ.get("CUTTER_PROJECT") or os.getcwd()).expanduser().resolve()
        candidates = [start] if folder else [start, *start.parents]
        for d in candidates:
            for name in FILENAMES:
                p = d / name
                if p.is_file():
                    return cls(d, read_file(p), p)
        if required:
            raise CutterError(f"No cutter.toml found in {start} or above.", MISSING,
                              "Create a project first: cutter new <folder> --media <footage folder>")
        return cls(start, {}, None)

    def __getitem__(self, k):
        return self.c[k]

    def get(self, k, default=None):
        return self.c.get(k, default)

    def path(self, key, cut=None):
        """Path from the configuration (a key in 'paths' or a path), with {cut} replaced."""
        raw = self.c["paths"].get(key, key)
        if cut is not None:
            raw = raw.replace("{cut}", cut_name(cut))
        p = pathlib.Path(os.path.expanduser(raw))
        return p if p.is_absolute() else self.folder / p

    def media_roots(self):
        """Named footage folders {name: path} from cutter.toml → [media]."""
        return {k: os.path.expanduser(v) for k, v in self.c["media"].items()}

    @property
    def media_base(self):
        """First footage folder: base for relative media paths in edit lists."""
        roots = list(self.media_roots().values())
        return pathlib.Path(roots[0]) if roots else self.folder

    def music_dirs(self):
        out = []
        for d in self.c["timeline"]["music_dirs"]:
            p = pathlib.Path(os.path.expanduser(d))
            out.append(p if p.is_absolute() else self.folder / p)
        return out

    @property
    def otio_namespace(self):
        return self.c["timeline"]["otio_namespace"]

    def external_clip_ids(self, edit=None):
        """Clips that do not come from clips.json (e.g. a finished intro): no retiming, no level, no transcript."""
        ids = set(self.c["timeline"].get("external_clips", {}))
        if edit:
            ids |= set(edit.get("external_clips", {}))
        return ids | {"TXT"}

    def ui_language(self):
        return self.c["review"].get("ui_language") or self.c.get("ui_language") or ""

    def app_config(self):
        """Settings for the review app (data/project.json)."""
        r = self.c["review"]
        return {"title": r.get("title") or self.c.get("title") or "", "people": r.get("people") or [],
                "agent": r.get("agent") or "Claude", "ui_language": self.ui_language()}

    def cuts(self):
        """Names of the cuts that have a folder under cuts/, naturally sorted."""
        d = self.path("cut", "x").parent
        if not d.is_dir():
            return []
        names = [p.name for p in d.iterdir() if p.is_dir() and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", p.name)]
        return sorted(names, key=lambda n: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)])


def toml_string(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_list(items):
    return "[" + ", ".join(toml_string(x) for x in items) + "]"
