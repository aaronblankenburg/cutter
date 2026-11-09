"""Helpers for looking at footage: contact sheets of time windows, single frames, Whisper on short excerpts."""
import io
import json
import os
import pathlib
import subprocess
import tempfile

from .util import CutterError, MISSING, read_json

# "full" audio chain for intelligibility tests (high-pass, noise reduction, presence, compressor, -20 LUFS)
FULL_CHAIN = "highpass=f=100,afftdn=nf=-25,equalizer=f=3000:t=q:w=1:g=4,acompressor=threshold=-24dB:ratio=3:attack=5:release=80,loudnorm=I=-20"


def _clips(project):
    p = project.path("clips")
    if not p.exists():
        raise CutterError(f"clips.json not found: {p}", MISSING, "Run first: cutter analyze")
    from .timeline import resolve_clips
    return resolve_clips({c["id"]: c for c in read_json(p)}, project.media_roots())


def window_sheet(project, output, specs):
    """Contact sheet for time windows: CLIP:START[:DURATION] … (2 frames per second, one row per window)."""
    C = _clips(project)
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with tempfile.TemporaryDirectory(prefix="cutter_window_") as tmp:
        for i, spec in enumerate(specs):
            p = spec.split(":"); cid, a = p[0], float(p[1]); d = float(p[2]) if len(p) > 2 else 5.0
            if cid not in C:
                raise CutterError(f"Unknown clip: {cid}", MISSING)
            f = f"{tmp}/r{i:02d}.jpg"
            vf = (f"fps=2,scale=200:-2,drawtext=text='{cid} %{{eif\\:t+{a}\\:d}}.%{{eif\\:mod((t+{a})*10\\,10)\\:d}}':"
                  f"x=3:y=3:fontsize=14:fontcolor=yellow:box=1:boxcolor=black@0.6,tile={int(d * 2)}x1")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(a), "-i", C[cid]["path"], "-t", str(d), "-vf", vf,
                            "-frames:v", "1", f], check=True)
            rows.append(f)
        inp = sum((["-i", f] for f in rows), [])
        flt = "".join(f"[{i}]" for i in range(len(rows))) + f"vstack={len(rows)}" if len(rows) > 1 else "null"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inp, "-filter_complex", flt, str(output)], check=True)
    return {"output": str(output), "windows": len(rows), "text": str(output)}


def frame_sheet(project, output, spec):
    """Single frames as one sheet: "C058:5,15,25;C057:58,62"."""
    from PIL import Image, ImageDraw
    C = _clips(project)
    tiles = []
    for part in spec.split(";"):
        cid, ts = part.split(":")
        for t in ts.split(","):
            r = subprocess.run(["ffmpeg", "-v", "error", "-ss", t, "-i", C[cid]["path"], "-frames:v", "1", "-vf", "scale=320:-2",
                                "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
            if not r.stdout:
                continue
            im = Image.open(io.BytesIO(r.stdout)).convert("RGB"); d = ImageDraw.Draw(im)
            d.rectangle((0, 0, 92, 16), fill=(0, 0, 0)); d.text((3, 3), f"{cid} {t}", fill=(255, 255, 0))
            tiles.append(im)
    if not tiles:
        raise CutterError("No frames could be read", MISSING)
    cols = 6; w, h = 320, max(t.size[1] for t in tiles); rows = (len(tiles) + cols - 1) // cols
    o = Image.new("RGB", (w * cols, h * rows), "black")
    for i, t in enumerate(tiles):
        o.paste(t, ((i % cols) * w, (i // cols) * h))
    output = pathlib.Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    o.save(output, quality=85)
    return {"output": str(output), "frames": len(tiles), "text": f"{output} ({len(tiles)} frames)"}


def asr(project, specs, model="large-v3-turbo", prompt=None, language=None, threads=None):
    """Whisper on excerpts: CLIP:FROM:TO[:chain] with chain = raw | full. Returns words with times and probabilities."""
    from .analyze import load_audio, load_model
    C = _clips(project)
    m = load_model(model, "auto", threads or max(4, (os.cpu_count() or 8) // 2))
    out, lines = [], []
    for spec in specs:
        p = spec.split(":"); cid, a, b = p[0], float(p[1]), float(p[2]); chain = p[3] if len(p) > 3 else "raw"
        x = load_audio(C[cid]["path"], start=a, duration=b - a, af=FULL_CHAIN if chain == "full" else None)
        for pr in ([None, prompt] if prompt else [None]):
            segs, _ = m.transcribe(x, language=language, vad_filter=False, word_timestamps=True, beam_size=5,
                                   condition_on_previous_text=False, initial_prompt=pr)
            words = [(round(w.start + a, 2), round(w.end + a, 2), w.word.strip(), round(w.probability, 2))
                     for s in segs for w in (s.words or [])]
            out.append({"clip": cid, "from": a, "to": b, "chain": chain, "prompt": bool(pr), "words": words})
            lines.append(f"== {cid} {a}-{b} {chain} prompt={'yes' if pr else 'no'}")
            lines.append("   " + " ".join(f"{w[2]}@{w[0]:.2f}({w[3]:.2f})" for w in words))
    return {"results": out, "text": "\n".join(lines)}
