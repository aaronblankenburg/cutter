"""Synthetic demo: test-pattern clips with tones as footage and a click track with a known tempo as music.

media(folder):      footage/  five clips (ffmpeg test sources, 1280x720 and 1920x1080, one at 29.97 fps), each with
                    its own tone; recording times are in the file names
first_cut(project): music/click_120bpm.wav (120 BPM drum-and-pad loop, downbeat every four beats, CC BY 4.0),
                    cuts/<cut>/edit.json and decisions.json – a first cut as an agent would write it – plus
                    filmcraft.json (a title and end credits) and the retime rules in cutter.toml
project(folder):    footage plus a project folder with all of the above
Everything is generated; nothing is downloaded. Needs ffmpeg.
"""
import json
import math
import random
import re
import pathlib
import struct
import subprocess

from .config import Project, cut_name
from .util import CutterError, MISSING, read_json, require_program

CLIPS = [  # file name, ffmpeg video source, size, rate, seconds, tone Hz
    ("2024-05-01 09-00-00 clip01.mp4", "testsrc2", "1280x720", "25", 16, 220),
    ("2024-05-01 09-05-00 clip02.mp4", "smptebars", "1920x1080", "25", 14, 330),
    ("2024-05-01 09-10-00 clip03.mp4", "rgbtestsrc", "1280x720", "25", 12, 440),
    ("2024-05-01 09-15-00 clip04.mp4", "testsrc", "1920x1080", "25", 20, 550),
    ("2024-05-01 09-20-00 clip05.mp4", "testsrc2", "1280x720", "30000/1001", 15, 660),
]
BPM, MUSIC_S, SR = 120, 40, 44100
MUSIC_FILE = "click_120bpm.wav"


def media(folder, dry_run=False):
    folder = pathlib.Path(folder).resolve()
    target = folder / "camera"
    files = [str(target / c[0]) for c in CLIPS]
    if dry_run:
        return {"dry_run": True, "files": files, "text": "Dry run – would create:\n  " + "\n  ".join(files)}
    require_program("ffmpeg")
    target.mkdir(parents=True, exist_ok=True)
    for name, src, size, rate, sec, hz in CLIPS:
        out = target / name
        if out.exists():
            continue
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"{src}=size={size}:rate={rate}",
                        "-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000", "-t", str(sec), "-pix_fmt", "yuv420p",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-g", "25", "-c:a", "aac", "-b:a", "64k",
                        "-shortest", str(out)], check=True)
    return {"folder": str(folder), "files": files, "text": f"Demo footage: {len(files)} clips in {target}"}


def click_track(path, bpm=BPM, seconds=MUSIC_S):
    """A small drum-and-pad loop with a known tempo: a kick and a chord change on every downbeat, a snare on beats two
    and four, a click on three. The first beat (a downbeat) comes after 0.5 s of silence. Deterministic."""
    rnd = random.Random(7)
    n, beat, t0 = int(seconds * SR), 60.0 / bpm, 0.5
    samples = [0.0] * n

    def add(start, length, fn):
        a = int(start * SR)
        for i in range(min(int(length * SR), n - a)):
            samples[a + i] += fn(i / SR)

    chords = [(130.81, 164.81, 196.0), (174.61, 220.0, 261.63), (196.0, 246.94, 293.66), (220.0, 261.63, 329.63)]  # C F G Am
    k = 0
    while t0 + k * beat < seconds - 0.6:
        t, pos = t0 + k * beat, k % 4
        if pos == 0:   # kick with a falling pitch, and the next chord
            add(t, 0.25, lambda x: 0.9 * math.sin(2 * math.pi * (50 * x + 2 * (1 - math.exp(-30 * x)))) * math.exp(-12 * x))
            chord = chords[(k // 4) % 4]
            add(t, 4 * beat - 0.02, lambda x, c=chord: 0.08 * sum(math.sin(2 * math.pi * f * x) for f in c)
                * min(1.0, 50 * x) * math.exp(-0.6 * x))
        elif pos in (1, 3):   # snare: a short noise burst
            add(t, 0.12, lambda x: 0.5 * (2 * rnd.random() - 1) * math.exp(-35 * x))
        else:
            add(t, 0.05, lambda x: 0.3 * math.sin(2 * math.pi * 1000 * x) * math.exp(-80 * x))
        k += 1
    peak = max(abs(x) for x in samples) or 1.0
    raw = b"".join(struct.pack("<f", 0.9 * x / peak) for x in samples)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
                    "-metadata", "title=Click 120", "-metadata", "artist=cutter demo",
                    "-metadata", "copyright=CC BY 4.0 https://creativecommons.org/licenses/by/4.0/",
                    "-c:a", "pcm_s16le", str(path)], input=raw, check=True)
    return {"file": str(path), "bpm": bpm, "first_beat": t0, "beats": k}


def edit_list(cut):
    """The demo's first cut, as an agent would write it (English format, see docs/formats.md)."""
    shot = lambda nr, clip, a, b, kind, note, **kw: dict({"nr": nr, "clip": clip, "in": a, "out": b, "kind": kind, "note": note}, **kw)
    return {
        "title": f"Demo ({cut})", "fps": 25,
        "sections": [
            {"name": "Opening", "shots": [
                shot(1, "C001", 1.0, 4.9, "picture", "test pattern, slow start"),
                shot(2, "C002", 2.0, 5.3, "picture", "colour bars"),
                shot(3, "C003", 0.5, 3.6, "picture", "RGB pattern")]},
            {"name": "Talk", "shots": [
                shot(4, "C004", 2.0, 10.0, "dialogue", "the tone that speaks",
                     audio={"chain": "mild", "highpass_hz": 80, "noise_reduction": 20, "speech_mix": 0, "compressor": True},
                     subtitles=[{"from": 0.3, "to": 3.5, "text": "This is the demo talking."},
                                {"from": 4.0, "to": 7.6, "text": "Subtitles are burned in on export."}],
                     cutaways=[{"clip": "C005", "in": 2.0, "out": 4.0, "at": 3.0, "note": "cutaway over the talk"}])]},
            {"name": "Montage", "shots": [
                shot(5, "C005", 6.0, 8.1, "picture", "moving pattern", block="fast cuts"),
                shot(6, "C001", 8.0, 10.2, "picture", "pattern again", block="fast cuts"),
                shot(7, "C002", 7.0, 10.9, "picture", "bars to the end", tag="last_picture")]},
            {"name": "End", "shots": [
                shot(8, "TXT", 0.0, 3.0, "black", "black with the end card")]},
        ],
        "music": [{"cue": "M1", "title": "Click 120", "artist": "cutter demo", "license": "CC BY 4.0", "file": f"music/{MUSIC_FILE}",
                   "from": {"section": "Opening", "edge": "start"}, "to": {"section": "End", "edge": "end"},
                   "source_start": 0.5, "fade_out_s": 2.0, "note": "drum loop with a known tempo, also under the end card"}],
        "credits_music_lines": ["Click 120 – cutter demo – CC BY 4.0"],
        "markers": [{"name": "Talk starts", "section": "Talk", "edge": "start", "color": "GREEN"},
                    {"name": "End card", "section": "End", "edge": "start", "duration": 3.0, "color": "YELLOW"}],
    }


def decisions(cut):
    return {"format": 2, "version": cut, "compared_to": None, "date": "2024-05-02", "title": f"Changes and decisions in {cut}",
            "intro": "The first cut of the demo.",
            "entries": [{"id": "overview", "title": f"{cut} at a glance", "text": "Three short sections on a click track."},
                        {"id": "talk", "nr": 4, "title": "Talk with subtitles", "text": "The dialogue is subtitled.",
                         "why": "Dialogue is always subtitled.", "question": "Is the cutaway too early?"}]}


DEMO_TOML = {
    "retime": '[retime]\nrequired_downbeats = [{ last_of = "Opening" }, { last_with_block = "fast cuts" }]\n\n'
              '[retime.cues.M1]\nphase = 0\n',
    "filmcraft": '[filmcraft]\nconfig = "filmcraft.json"   # titles and credits of the demo\n',
}

SHADOW = {"shadow": True, "shadow_color": "#000000", "shadow_distance": 0, "shadow_opacity": 90, "shadow_blur": 12, "shadow_size": 6}
FILMCRAFT = {
    "titles": [{"text": "cutter demo", "font": "P052", "size": 150, "position": [960, 560], "time": 0.5, "duration": 3.0,
                "style": dict(SHADOW, align="center", size=150, fill_color="#f2efe6", position=[960, 560]),
                "opacity": [[0.5, 0], [1.0, 100], [3.0, 100], [3.5, 0]]}],
    "credits": {"duration": 3.0,
                "styles": {"large": dict(SHADOW, font="P052", fill_color="#e6e2d6", align="left"),
                           "small": dict(SHADOW, font="Liberation Sans Narrow", fill_color="#e6e2d6", align="left")},
                "layers": [{"text": "Music:", "style": "large", "size": 86, "position": [160, 480]},
                           {"text": "{music_lines}", "style": "small", "size": 44, "position": [480, 452], "line_height": 50},
                           {"text": "Made with cutter", "style": "large", "size": 92, "position": [160, 700]}]},
}


def _add_demo_settings(p):
    """The demo's settings (once): retime rules in cutter.toml, titles and credits in filmcraft.json."""
    if p.file is None or p.file.suffix != ".toml":
        return False
    fc = p.folder / "filmcraft.json"
    if not fc.exists():
        fc.write_text(json.dumps(FILMCRAFT, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    text = p.file.read_text(encoding="utf-8")
    missing = [block for name, block in DEMO_TOML.items() if not re.search(rf"^\[{name}[\].]", text, re.M)]
    if missing:
        p.file.write_text(text.rstrip("\n") + "\n\n" + "\n".join(missing), encoding="utf-8")
    return bool(missing)


def first_cut(p, cut="cut1", dry_run=False):
    cut = cut_name(cut)
    music_dir = p.path("music")
    edit_p, dec_p = p.path("edit", cut), p.path("decisions", cut)
    if dry_run:
        return {"dry_run": True, "files": [str(music_dir / MUSIC_FILE), str(edit_p), str(dec_p)],
                "text": f"Dry run – would write {music_dir / MUSIC_FILE}, {edit_p}, {dec_p}"}
    if not p.path("clips").exists():
        raise CutterError(f"clips.json missing: {p.path('clips')}", MISSING, "First: cutter analyze")
    clips = {c["id"] for c in read_json(p.path("clips"))}
    need = {"C001", "C002", "C003", "C004", "C005"}
    if not need <= clips:
        raise CutterError(f"The demo cut needs the five demo clips, found {sorted(clips)}", MISSING, "cutter demo media <folder>")
    music_dir.mkdir(parents=True, exist_ok=True)
    if not (music_dir / MUSIC_FILE).exists():
        require_program("ffmpeg")
        click_track(music_dir / MUSIC_FILE)
    edit_p.parent.mkdir(parents=True, exist_ok=True)
    edit_p.write_text(json.dumps(edit_list(cut), ensure_ascii=False, indent=1), encoding="utf-8")
    dec_p.write_text(json.dumps(decisions(cut), ensure_ascii=False, indent=1), encoding="utf-8")
    _add_demo_settings(p)
    return {"cut": cut, "edit": str(edit_p), "decisions": str(dec_p), "music": str(music_dir / MUSIC_FILE),
            "text": f"Demo cut written: {edit_p} (+ decisions.json, music/{MUSIC_FILE})"}


def project(folder, dry_run=False):
    from .new import create
    folder = pathlib.Path(folder).resolve()
    if dry_run:
        return {"dry_run": True, "text": f"Dry run – would create {folder}/footage and {folder}/film"}
    m = media(folder / "footage")
    create(folder / "film", media=[str(folder / "footage")], title="Demo")
    _add_demo_settings(Project.load(str(folder / "film")))
    return {"footage": m["folder"], "project": str(folder / "film"),
            "text": f"Demo footage in {folder / 'footage'}, project in {folder / 'film'}.\n"
                    "Next: cd film && cutter analyze && cutter demo cut && cutter music analyze && cutter retime cut1 && cutter timeline cut1"}
