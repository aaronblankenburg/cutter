"""Music: beats, downbeats and loudness (analyze), and the license check (license).

analyze: librosa beat tracking (median onset envelope, hop 256, tightness 200) on the whole track, plus the beats of
  an untrimmed pass in the intro; the bar phase (which beat is the "one") from kick/bass energy and harmonic change
  at the beats, against snare energy; loudness by EBU R128. Results go to music/music.json, keyed by file name:
    {"track.mp3": {"file", "duration", "bpm", "beats", "downbeats", "phase", "scores", "conf", "lufs"}}
  The retime step finds a cue's beats by the cue name (older files) or by the cue's file name.
license: reads license hints from the file tags (ffprobe: copyright, license, comment, TCOP/WCOP, URLs) and from the
  "license" fields of an edit list, and checks them against music.allowed_licenses in cutter.toml. By default only
  CC0 and CC BY are allowed; NC variants are opt-in. ND and BY-SA are always reported as traps. Missing or unclear
  licenses are reported as unknown – then the agent asks the user instead of guessing.
"""
import json
import pathlib
import re
import subprocess

from .legacy import music_from_legacy
from .util import CutterError, MISSING, log, lufs, read_json, write_json

AUDIO_EXT = {".ogg", ".mp3", ".wav", ".flac", ".m4a", ".opus", ".aac"}

# ---------------------------------------------------------------- license check

CANON = ["CC0", "CC BY", "CC BY-SA", "CC BY-NC", "CC BY-NC-SA", "CC BY-ND", "CC BY-NC-ND"]
TRAPS = {"CC BY-SA": "share-alike forces its license onto the film and clashes with other licenses (e.g. BY-NC-SA)",
         "CC BY-ND": "no derivatives: laying music under a film counts as adapting it",
         "CC BY-NC-ND": "no derivatives: laying music under a film counts as adapting it"}
URL = re.compile(r"creativecommons\.org/(?:licenses/(by(?:-nc)?(?:-sa|-nd)?)/|publicdomain/zero)", re.I)
SHORT = re.compile(r"\bcc[\s_-]*(0|zero|by(?:[\s_-]*(?:nc|sa|nd)){0,2})\b", re.I)
WORDS = [(re.compile(r"attribution[\s-]*non[\s-]*commercial[\s-]*share[\s-]*alike", re.I), "CC BY-NC-SA"),
         (re.compile(r"attribution[\s-]*non[\s-]*commercial[\s-]*no[\s-]*deriv", re.I), "CC BY-NC-ND"),
         (re.compile(r"attribution[\s-]*non[\s-]*commercial", re.I), "CC BY-NC"),
         (re.compile(r"attribution[\s-]*share[\s-]*alike", re.I), "CC BY-SA"),
         (re.compile(r"attribution[\s-]*no[\s-]*deriv", re.I), "CC BY-ND"),
         (re.compile(r"public\s*domain\s*dedication|\bcc0\b", re.I), "CC0")]


def canonical(text):
    """Canonical CC license name found in a text, or None."""
    if not text:
        return None
    m = URL.search(text)
    if m:
        return "CC0" if not m.group(1) else "CC " + m.group(1).upper().replace("BY-", "BY-").replace("-", "-")
    m = SHORT.search(text)
    if m:
        t = re.sub(r"[\s_-]+", "-", m.group(1).upper())
        if t in ("0", "ZERO"):
            return "CC0"
        parts = [p for p in t.split("-") if p]
        order = [p for p in ("BY", "NC", "SA", "ND") if p in parts]
        name = "CC " + "-".join(order)
        return name if name in CANON else None
    for rx, name in WORDS:
        if rx.search(text):
            return name
    return None


def verdict(lic, allowed):
    if lic is None:
        return "unknown", "no license found – ask the user, do not guess"
    allowed = [canonical(a) or a for a in allowed]
    if lic in TRAPS:
        return "forbidden", TRAPS[lic]
    if lic in allowed:
        return "allowed", ""
    if "NC" in lic:
        return "not_allowed", "non-commercial license: add it to music.allowed_licenses only if the film is non-commercial"
    return "not_allowed", "not in music.allowed_licenses"


def tags(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode:
        raise CutterError(f"ffprobe cannot read {path}: {r.stderr.strip()[:200]}", MISSING)
    d = json.loads(r.stdout or "{}")
    out = {}
    for src in [d.get("format", {}).get("tags", {})] + [s.get("tags", {}) for s in d.get("streams", [])]:
        for k, v in (src or {}).items():
            out.setdefault(k.lower(), v)
    return out


def license_check(project, files=(), edit_path=None):
    allowed = project["music"]["allowed_licenses"]
    items = []
    paths = [pathlib.Path(f) for f in files]
    if not paths and not edit_path:
        mdir = project.path("music")
        paths = sorted(p for p in mdir.iterdir() if p.suffix.lower() in AUDIO_EXT) if mdir.is_dir() else []
    for p in paths:
        if not p.exists():
            raise CutterError(f"File not found: {p}", MISSING)
        t = tags(p)
        text = " | ".join(f"{k}={v}" for k, v in t.items() if k in ("copyright", "license", "licence", "comment", "tcop", "wcop",
                                                                      "url", "www", "wxxx", "description", "artist", "title"))
        lic = canonical(text)
        v, why = verdict(lic, allowed)
        items.append({"source": str(p), "license": lic, "verdict": v, "reason": why, "tags": {k: t[k] for k in list(t)[:12]}})
    if edit_path:
        from .legacy import edit_from_legacy
        e = edit_from_legacy(read_json(edit_path))[0]
        for m in e.get("music", []):
            lic = canonical(m.get("license", ""))
            v, why = verdict(lic, allowed)
            items.append({"source": f"{edit_path}: {m.get('cue')} {m.get('title')}", "license": lic, "verdict": v, "reason": why})
    ok = all(i["verdict"] == "allowed" for i in items)
    lines = [f"Allowed licenses (cutter.toml → music.allowed_licenses): {', '.join(allowed)}"]
    for i in items:
        lines.append(f"  {i['verdict']:<11} {i['license'] or '?':<12} {i['source']}" + (f"  – {i['reason']}" if i["reason"] else ""))
    if not items:
        lines.append("  no music files found")
    return {"ok": ok, "allowed": allowed, "items": items, "text": "\n".join(lines)}


# ---------------------------------------------------------------- beat analysis

def analyze_file(path, start_bpm=None):
    """Beats, downbeats (bar phase) and loudness of one track."""
    import numpy as np
    import librosa
    sr, hop = 22050, 256
    y, _ = librosa.load(str(path), sr=sr, mono=True)
    oenv = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop, aggregate=np.median)
    kw = {"start_bpm": start_bpm} if start_bpm else {}
    _, beats = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, tightness=200, units="time", **kw)
    _, untrimmed = librosa.beat.beat_track(onset_envelope=oenv, sr=sr, hop_length=hop, tightness=200, trim=False, units="time", **kw)
    b = np.asarray(beats)
    if len(b) < 8:
        raise CutterError(f"{path}: too few beats found ({len(b)})")
    bu = np.asarray(untrimmed)
    pre = bu[bu < b[0] - 0.3 * np.median(np.diff(b))]          # beats in the intro (untrimmed) before the first one
    b = np.r_[pre, b]
    yp = librosa.effects.percussive(y, margin=2.0)
    Sp = np.abs(librosa.stft(yp, n_fft=2048, hop_length=hop)); fr = librosa.fft_frequencies(sr=sr, n_fft=2048)

    def band(X, lo, hi):
        e = np.log1p(X[(fr >= lo) & (fr < hi)].sum(0)); x = np.maximum(0, np.diff(e, prepend=e[0])); return x / (x.std() + 1e-9)
    kick, snare = band(Sp, 30, 150), band(Sp, 1500, 5000)
    Sa = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop)); bass = band(Sa, 40, 200)
    f = librosa.time_to_frames(b, sr=sr, hop_length=hop)
    w = lambda env: np.array([env[max(0, i - 2):i + 3].max() for i in f])
    K, SN, BA = w(kick), w(snare), w(bass)
    ch = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop)
    chs = librosa.util.sync(ch, f, aggregate=np.median)[:, :len(b)]
    nov = np.r_[0, np.linalg.norm(np.diff(chs, axis=1), axis=0)]
    nov = nov / (nov.mean() + 1e-9)
    scores = []
    for ph in range(4):
        i1 = np.arange(ph, len(b), 4)
        # downbeat: harmonic change + kick/bass on the one, snare rather on two and four
        s = nov[i1].mean() + 0.5 * (K[i1].mean() + BA[i1].mean()) / (K.mean() + BA.mean()) * 2 - 0.5 * SN[i1].mean() / (SN.mean() + 1e-9)
        scores.append(float(s))
    ph = int(np.argmax(scores)); srt = sorted(scores)
    conf = float((srt[-1] - srt[-2]) / (np.std(scores) + 1e-9))
    return {"file": str(path), "duration": round(len(y) / sr, 3), "bpm": round(60 / float(np.median(np.diff(b))), 3),
            "beats": [round(float(x), 4) for x in b], "downbeats": [round(float(x), 4) for x in b[ph::4]], "phase": ph,
            "scores": [round(x, 3) for x in scores], "conf": round(conf, 3), "lufs": lufs(path)}


def find_files(project, files=()):
    if files:
        return [pathlib.Path(f) for f in files]
    mdir = project.path("music")
    return sorted(p for p in mdir.iterdir() if p.suffix.lower() in AUDIO_EXT) if mdir.is_dir() else []


def analyze(files, target, start_bpm=None):
    target = pathlib.Path(target)
    old = music_from_legacy(read_json(target)) if target.exists() else {}
    lines = []
    for p in files:
        if not p.exists():
            raise CutterError(f"File not found: {p}", MISSING)
        log(f"Analyzing {p.name} …")
        d = analyze_file(p, start_bpm)
        try:                                   # relative to music.json, so that the project can move
            d["file"] = pathlib.Path(p).resolve().relative_to(target.resolve().parent).as_posix()
        except ValueError:
            d["file"] = str(pathlib.Path(p).resolve())
        old[p.name] = d
        lines.append(f"  {p.name}: {d['bpm']:.1f} BPM, {len(d['beats'])} beats, first downbeat {d['downbeats'][0]:.3f} s "
                     f"(phase {d['phase']}, confidence {d['conf']:.2f}), {d['lufs']} LUFS")
    write_json(target, old)
    return {"target": str(target), "tracks": {p.name: {k: old[p.name][k] for k in ("bpm", "phase", "lufs", "duration")} for p in files},
            "text": "\n".join(lines + [f"-> {target}"])}


def beats_for(music, cue, file_name=None):
    """Analysis entry for a cue: by cue name (older files) or by file name."""
    if cue in music:
        return music[cue]
    if file_name and pathlib.Path(file_name).name in music:
        return music[pathlib.Path(file_name).name]
    return None
