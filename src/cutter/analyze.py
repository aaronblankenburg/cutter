"""Footage analysis: clip list, Whisper transcripts with word timestamps, contact sheets and film strips.

Output folder (default analysis/ in the project):
  clips.json             metadata of every clip (duration, frame rate, resolution, recording time, device)
  transcript_all.txt     all transcripts, readable, in chronological order
  transcripts/<ID>.json  transcripts with word timestamps (faster-whisper)
  sheets/<ID>.jpg        contact sheet per clip with timecodes
  strips/<ID>.jpg        film strip per clip
  overview/page_*.jpg    overview pages, one strip per clip
An interrupted run continues where it stopped. Clip IDs are kept between runs; new clips are appended
chronologically. The Whisper model comes from the Hugging Face cache (HF_HOME, the Docker volume cutter-models)
and is downloaded on first use (large-v3-turbo, about 1.6 GB). Runs on the CPU with int8 unless CUDA is available.
"""
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache, partial
from pathlib import Path

from .util import CutterError, MISSING, log

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi", ".mxf", ".webm", ".3gp"}
GRAB_WIDTH = 320      # width of the frames grabbed from the video
THUMB = 224           # longest edge of a picture in the contact sheet
STRIP_H = 80          # picture height on the overview pages
ROWS_PER_PAGE = 12    # clips per overview page

# Typical Whisper hallucinations on silence or wind noise: subtitle credits seen in German and English training data
HALLUCINATION = re.compile(
    r"amara\.org|untertitel(ung)?\s+(im auftrag|der|des|von)|im auftrag des zdf|zdf für funk|subtitles by|thanks for watching",
    re.IGNORECASE,
)


def fmt_ts(sec, decimals=1):
    """seconds -> mm:ss(.s) or h:mm:ss(.s)"""
    sec = round(max(0.0, float(sec or 0)), decimals)
    h, rest = divmod(sec, 3600)
    m, s = divmod(rest, 60)
    s_txt = f"{s:0{3 + decimals}.{decimals}f}" if decimals else f"{int(s):02d}"
    return f"{int(h)}:{int(m):02d}:{s_txt}" if h else f"{int(m):02d}:{s_txt}"


def frac(value):
    """'30000/1001' or '25' -> float"""
    try:
        if isinstance(value, str) and "/" in value:
            num, den = (float(x) for x in value.split("/", 1))
            return num / den if den else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_time(value):
    """Timestamp from metadata -> naive UTC datetime or None"""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    s = re.sub(r"\.\d+", "", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt if dt.year >= 2005 else None             # camera clock never set


NAME_TIME = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[-.:](\d{2})[-.:](\d{2})")


def time_from_name(name):
    """Recording time from names like '2024-06-01 10-15-00 GOPR0001.MP4' (more reliable than wrong camera clocks)"""
    m = NAME_TIME.match(name)
    if not m:
        return None
    try:
        dt = datetime(*map(int, m.groups()))
    except ValueError:
        return None
    return dt if dt.year >= 2005 else None


@lru_cache(maxsize=None)
def font(size):
    from PIL import ImageFont
    for name in ("DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ------------------------------------------------------------------ files and metadata

def collect(inputs):
    """Collect video files; paths relative to the common parent so folder names are kept."""
    found, seen, anchors = [], set(), []
    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_dir():
            cands = [f for f in sorted(p.rglob("*"))
                     if f.is_file() and f.suffix.lower() in VIDEO_EXT
                     and not any(part.startswith(".") for part in f.relative_to(p).parts)]
        elif p.is_file():
            cands = [p]
        else:
            log(f"! Not found: {raw}")
            continue
        anchors.append(str(p.resolve().parent))
        for f in cands:
            f = f.resolve()
            if f not in seen:
                seen.add(f)
                found.append(f)
    try:
        base = Path(os.path.commonpath(anchors)) if anchors else None
    except ValueError:
        base = None
    return found, base


def rel_path(path, base):
    try:
        return path.relative_to(base).as_posix() if base else path.name
    except ValueError:
        return path.name


def ffprobe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "ffprobe error")
    return json.loads(r.stdout or "{}")


def rotation(stream):
    for sd in stream.get("side_data_list") or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    try:
        return int(float((stream.get("tags") or {}).get("rotate", 0))) % 360
    except (TypeError, ValueError):
        return 0


def guess_device(tags, video):
    parts = [str(v) for k, v in tags.items() if re.search(r"make|model|manufacturer", k) and v]
    if not parts and tags.get("encoder"):
        parts = [str(tags["encoder"])]
    if not parts and video:
        handler = (video.get("tags") or {}).get("handler_name")
        if handler:
            parts = [handler]
    return " ".join(dict.fromkeys(parts))[:80] or None


def probe_clip(path, base):
    info = ffprobe(path)
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    vtags = {k.lower(): v for k, v in ((video or {}).get("tags") or {}).items()}
    created, source = time_from_name(path.name), "filename"
    if not created:
        source = "metadata"
        for key in ("com.apple.quicktime.creationdate", "creation_time"):
            created = parse_time(tags.get(key) or vtags.get(key))
            if created:
                break
    if not created:
        created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
        source = "file_date"
    timecode = tags.get("timecode") or next(
        ((s.get("tags") or {}).get("timecode") for s in streams if (s.get("tags") or {}).get("timecode")), None)
    clip = {"file": path.name, "rel_path": rel_path(path, base), "path": str(path.resolve()),
            "size_mb": round(path.stat().st_size / 1e6, 1),
            "duration": round(frac(fmt.get("duration")) or frac((video or audio or {}).get("duration")), 3),
            "created": created.isoformat(sep=" ", timespec="seconds"), "created_source": source,
            "device": guess_device(tags, video), "timecode": timecode,
            "has_video": video is not None, "has_audio": audio is not None}
    if video:
        w, h = video.get("width"), video.get("height")
        rot = rotation(video)
        if rot % 180 == 90:
            w, h = h, w
        fps, avg = frac(video.get("r_frame_rate")), frac(video.get("avg_frame_rate"))
        clip.update({"codec": video.get("codec_name"), "width": w, "height": h, "rotation": rot,
                     "fps": round(fps, 3), "fps_raw": video.get("r_frame_rate"), "fps_avg": round(avg, 3),
                     "vfr": bool(fps and avg and abs(fps - avg) / fps > 0.01)})
    if audio:
        clip.update({"audio_rate": int(frac(audio.get("sample_rate"))), "audio_channels": audio.get("channels")})
    return clip


def assign_ids(clips, out):
    """Keep IDs from earlier runs, append new clips chronologically."""
    known = {}
    cj = out / "clips.json"
    if cj.exists():
        try:
            known = {c["path"]: c["id"] for c in json.loads(cj.read_text(encoding="utf-8"))}
        except (ValueError, KeyError, TypeError, OSError):
            known = {}
    nums = [int(i[1:]) for i in known.values() if i[1:].isdigit()]
    nxt = max(nums, default=0) + 1
    for c in sorted(clips, key=lambda c: (c["created"], c["rel_path"])):
        if c["path"] in known:
            c["id"] = known[c["path"]]
        else:
            c["id"] = f"C{nxt:03d}"
            nxt += 1


def parse_bound(value, end=False):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace(" ", "T"))
    except ValueError:
        raise CutterError(f"Invalid date: {value} (format YYYY-MM-DD or 'YYYY-MM-DD HH:MM')", 2)
    if end and len(value.strip()) <= 10:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def filter_dates(clips, since, until):
    lo, hi = parse_bound(since), parse_bound(until, end=True)
    if lo or hi:
        before = len(clips)
        clips = [c for c in clips if (not lo or c["created"] >= lo) and (not hi or c["created"] <= hi)]
        log(f"Date range: {len(clips)} of {before} clips")
    return clips


def summary(clips):
    by_dir, by_day = {}, {}
    for c in clips:
        by_dir.setdefault(str(Path(c["rel_path"]).parent), []).append(c)
        by_day.setdefault(c["created"][:10], []).append(c)
    dur = lambda cs: fmt_ts(sum(c["duration"] for c in cs), 0)
    lines = [f"{'Folder':<48} {'Clips':>5} {'Duration':>9}  Recorded"]
    for d, cs in sorted(by_dir.items()):
        ts = sorted(c["created"][:16] for c in cs)
        lines.append(f"{d[-48:]:<48} {len(cs):>5} {dur(cs):>9}  {ts[0]} to {ts[-1]}")
    lines.append(f"\n{'Day':<12} {'Clips':>5} {'Duration':>9}")
    for day, cs in sorted(by_day.items()):
        lines.append(f"{day:<12} {len(cs):>5} {dur(cs):>9}")
    lines.append(f"\nTotal: {len(clips)} clips, {dur(clips)}")
    return "\n".join(lines)


def save_clips(clips, out):
    (out / "clips.json").write_text(json.dumps(clips, ensure_ascii=False, indent=1), encoding="utf-8")


# ------------------------------------------------------------------ contact sheets

def sample_times(duration, max_frames, interval=None):
    if duration <= 0.5:
        return [0.0]
    if interval:
        n = min(120, int(duration // interval) + 1)
    else:
        n = min(max_frames, max(4, math.ceil(duration / 4)))
    n = max(1, min(n, int(duration * 2)))
    step = duration / n
    return [round(step * (i + 0.5), 2) for i in range(n)]


def grab_frame(path, t):
    from PIL import Image
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path),
           "-map", "0:v:0", "-frames:v", "1", "-vf", f"scale={GRAB_WIDTH}:-2", "-pix_fmt", "rgb24",
           "-f", "image2pipe", "-vcodec", "png", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return Image.open(io.BytesIO(r.stdout)).convert("RGB")
    except Exception:
        return None


def make_sheet(clip, frames, path):
    from PIL import Image, ImageDraw
    cols = min(6, len(frames))
    fw, fh = frames[0][1].size
    scale = THUMB / max(fw, fh)
    tw, th = max(1, round(fw * scale)), max(1, round(fh * scale))
    pad, head = 4, 30
    rows = math.ceil(len(frames) / cols)
    sheet = Image.new("RGB", (max(pad + cols * (tw + pad), 560), head + pad + rows * (th + pad)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    d.text((pad + 2, 7), f'{clip["id"]}   {clip["file"]}   {fmt_ts(clip["duration"], 0)}   {clip["created"][:16]}',
           fill=(235, 235, 235), font=font(15))
    small = font(13)
    decimals = 1 if clip["duration"] < 60 else 0
    for i, (t, img) in enumerate(frames):
        x = pad + (i % cols) * (tw + pad)
        y = head + pad + (i // cols) * (th + pad)
        sheet.paste(img.resize((tw, th)), (x, y))
        label = fmt_ts(t, decimals)
        bl, bt, br, bb = d.textbbox((0, 0), label, font=small)
        d.rectangle((x, y + th - (bb - bt) - 6, x + (br - bl) + 6, y + th), fill=(0, 0, 0))
        d.text((x + 3 - bl, y + th - (bb - bt) - 3 - bt), label, fill=(255, 214, 0), font=small)
    sheet.save(path, quality=65)


def make_strip(frames, path, n=6):
    from PIL import Image
    if len(frames) > n:
        idx = sorted({round(i * (len(frames) - 1) / (n - 1)) for i in range(n)})
        frames = [frames[i] for i in idx]
    imgs = [img for _, img in frames]
    widths = [max(1, round(im.width * STRIP_H / im.height)) for im in imgs]
    strip = Image.new("RGB", (sum(widths) + 3 * (len(imgs) - 1), STRIP_H), (24, 24, 24))
    x = 0
    for im, w in zip(imgs, widths):
        strip.paste(im.resize((w, STRIP_H)), (x, 0))
        x += w + 3
    strip.save(path, quality=65)


def do_sheets(clips, out, force, max_frames, interval):
    pending = [c for c in clips if c.get("has_video") and (force or not (
        (out / "sheets" / f'{c["id"]}.jpg').exists() and (out / "strips" / f'{c["id"]}.jpg').exists()))]
    if not pending:
        return 0
    log(f"\nContact sheets for {len(pending)} clips …")
    with ThreadPoolExecutor(min(4, os.cpu_count() or 2)) as pool:
        for k, c in enumerate(pending, 1):
            times = sample_times(c["duration"], max_frames, interval)
            imgs = list(pool.map(partial(grab_frame, c["path"]), times))
            frames = [(t, im) for t, im in zip(times, imgs) if im is not None]
            if not frames:
                log(f"  [{k}/{len(pending)}] {c['id']}: no readable frames")
                continue
            make_sheet(c, frames, out / "sheets" / f'{c["id"]}.jpg')
            make_strip(frames, out / "strips" / f'{c["id"]}.jpg')
            log(f"  [{k}/{len(pending)}] {c['id']}  {c['rel_path']}")
    return len(pending)


def speech_label(c):
    if not c.get("has_audio"):
        return "no audio"
    if "words" not in c:
        return "not transcribed"
    return f'{c["words"]} words' if c["words"] else "no speech"


def make_overview(clips, out):
    from PIL import Image, ImageDraw
    odir = out / "overview"
    shutil.rmtree(odir, ignore_errors=True)
    odir.mkdir(parents=True)
    rows = [(c, out / "strips" / f'{c["id"]}.jpg') for c in clips]
    rows = [(c, p) for c, p in rows if p.exists()]
    label_w, row_h, pad = 175, STRIP_H + 8, 6
    big, small = font(15), font(13)
    pages = 0
    for start in range(0, len(rows), ROWS_PER_PAGE):
        chunk = rows[start:start + ROWS_PER_PAGE]
        strips = [Image.open(p) for _, p in chunk]
        page = Image.new("RGB", (label_w + max(s.width for s in strips) + 2 * pad, pad + len(chunk) * row_h), (24, 24, 24))
        d = ImageDraw.Draw(page)
        for i, ((c, _), s) in enumerate(zip(chunk, strips)):
            y = pad + i * row_h
            page.paste(s, (label_w + pad, y))
            d.text((pad, y + 2), f'{c["id"]}  {fmt_ts(c["duration"], 0)}', fill=(235, 235, 235), font=big)
            d.text((pad, y + 24), speech_label(c), fill=(170, 170, 170), font=small)
            d.text((pad, y + 44), c["created"][5:16], fill=(125, 125, 125), font=small)
        pages += 1
        page.save(odir / f"page_{pages:03d}.jpg", quality=65)
    return pages


# ------------------------------------------------------------------ transcription

def load_model(model, device="auto", threads=4):
    try:
        import ctranslate2
        from faster_whisper import WhisperModel, download_model
    except ImportError:
        raise CutterError("faster-whisper is missing (it is in the Docker image)", MISSING)
    log(f"\nWhisper model {model} (downloaded on first use, about 1.6 GB for large-v3-turbo) …")
    try:
        path = model if Path(model).is_dir() else download_model(model, local_files_only=bool(os.environ.get("HF_HUB_OFFLINE")))
    except Exception as e:
        raise CutterError(f"Whisper model could not be loaded: {e}", MISSING,
                          "Check: cutter doctor (the model lives in the Docker volume cutter-models)")
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    compute = "float16" if device == "cuda" else "int8"
    log(f"  Running on {device.upper()} ({compute})")
    return WhisperModel(path, device=device, compute_type=compute, cpu_threads=threads)


def load_audio(path, sr=16000, start=None, duration=None, af=None):
    """Read the audio with ffmpeg as 16 kHz mono float32."""
    import numpy as np
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error"] + (["-ss", f"{start:.3f}"] if start is not None else []) + \
          (["-t", f"{duration:.3f}"] if duration is not None else []) + ["-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", str(sr)] + \
          (["-af", af] if af else []) + ["-f", "f32le", "-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "ffmpeg could not read the audio")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def transcribe_audio(model, audio, language, duration=0, progress=True):
    segments, info = model.transcribe(audio, language=None if language == "auto" else language, vad_filter=True,
                                      word_timestamps=True, condition_on_previous_text=False, beam_size=5)
    segs = []
    for s in segments:
        text = s.text.strip()
        segs.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text,
                     "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3),
                     "suspect": bool(HALLUCINATION.search(text)),
                     "words": [[round(w.start, 2), round(w.end, 2), w.word, round(w.probability, 3)] for w in (s.words or [])]})
        if duration and progress and not os.environ.get("CUTTER_JSON"):
            print(f"\r    {min(100.0, s.end / duration * 100):5.1f} %", end="", flush=True)
    if duration and progress and not os.environ.get("CUTTER_JSON"):
        print("\r    100.0 %")
    return segs, info


def transcribe_clip(model, clip, language):
    audio = load_audio(clip["path"])
    if audio.size < 1600:
        return {"id": clip["id"], "file": clip["file"], "language": None, "language_probability": 0.0, "segments": []}
    segs, info = transcribe_audio(model, audio, language, clip.get("duration") or 0)
    return {"id": clip["id"], "file": clip["file"], "language": info.language,
            "language_probability": round(info.language_probability, 3), "segments": segs}


def do_transcripts(clips, out, force, model_name, language, device, threads):
    pending = [c for c in clips if c.get("has_audio") and (force or not (out / "transcripts" / f'{c["id"]}.json').exists())]
    if not pending:
        return 0
    log(f"\nTranscribing {len(pending)} clips ({fmt_ts(sum(c['duration'] for c in pending), 0)} of footage) …")
    model = load_model(model_name, device, threads)
    for k, c in enumerate(pending, 1):
        log(f"  [{k}/{len(pending)}] {c['id']}  {c['rel_path']}  ({fmt_ts(c['duration'], 0)})")
        t0 = time.time()
        try:
            data = transcribe_clip(model, c, language)
        except Exception as e:
            log(f"\n    ! Error: {e}")
            continue
        target = out / "transcripts" / f'{c["id"]}.json'
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
        log(f"    {len(data['segments'])} segments, {time.time() - t0:.0f} s")
    return len(pending)


def load_transcript(out, cid):
    p = out / "transcripts" / f"{cid}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def write_readable(clips, out):
    lines = []
    found = [d.get("language") for d in (load_transcript(out, c["id"]) for c in clips) if d and d.get("segments")]
    main = max(set(found), key=found.count) if found else None      # the language most clips speak
    for c in clips:
        data = load_transcript(out, c["id"])
        extra = f' | {c["device"][:40]}' if c.get("device") else ""
        if data and data.get("segments") and data.get("language") not in (None, main):
            extra += f' | language: {data["language"]}'
        lines.append(f'=== {c["id"]} | {c["created"][:16]} | {fmt_ts(c["duration"], 0)} | {c["rel_path"]}{extra} ===')
        if not c.get("has_audio"):
            lines.append("(no audio)")
        elif data is None:
            lines.append("(not transcribed)")
        else:
            segs = [s for s in data["segments"] if not s.get("suspect")]
            for s in segs:
                unsure = " (?)" if s["avg_logprob"] < -1.0 or s["no_speech_prob"] > 0.6 else ""
                lines.append(f'[{fmt_ts(s["start"])}-{fmt_ts(s["end"])}]{unsure} {s["text"]}')
            if not segs:
                lines.append("(no speech recognized)")
        lines.append("")
    (out / "transcript_all.txt").write_text("\n".join(lines), encoding="utf-8")


def media_ref(path, roots):
    """{"root": name, "path": relative} for the named footage folder that contains the clip, else None."""
    for name, root in (roots or {}).items():
        try:
            return {"root": name, "path": Path(path).resolve().relative_to(Path(root).resolve()).as_posix()}
        except ValueError:
            continue
    return None


def run_analysis(inputs, out, list_only=False, since=None, until=None, exclude=(), only=None, force=False,
                 no_transcripts=False, no_sheets=False, model="large-v3-turbo", language="auto", device="auto",
                 threads=4, max_frames=24, interval=None, roots=None):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        raise CutterError("ffmpeg/ffprobe not found", MISSING, "Use the Docker image: bin/cutter analyze")
    files, base = collect(inputs)
    if exclude:
        keep = [f for f in files if not any(x.lower() in rel_path(f, base).lower() for x in exclude)]
        log(f"Excluded: {len(files) - len(keep)} files ({', '.join(exclude)})")
        files = keep
    if not files:
        raise CutterError("No video files found.", MISSING)
    log(f"{len(files)} video files found, reading metadata …")
    clips = []
    for f in files:
        try:
            clips.append(probe_clip(f, base))
        except Exception as e:
            log(f"  ! skipped: {f} ({e})")
    clips = filter_dates(clips, since, until)
    if not clips:
        raise CutterError("No readable video files (in the selected date range).", MISSING)
    for c in clips:                       # portable reference: named footage folder + relative path
        ref = media_ref(c["path"], roots)
        if ref:
            c["media"] = ref
    if list_only:
        text = summary(clips)
        return {"clips": len(clips), "text": text}
    out = Path(out).expanduser().resolve()
    for sub in ("transcripts", "sheets", "strips"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    assign_ids(clips, out)
    clips.sort(key=lambda c: (c["created"], c["rel_path"]))
    save_clips(clips, out)
    todo = clips
    if only:
        wanted = {x.strip().upper() for x in only.split(",") if x.strip()}
        todo = [c for c in clips if c["id"] in wanted]
        log(f"Only: {', '.join(c['id'] for c in todo) or '(no matching IDs)'}")
    n_sheets = 0 if no_sheets else do_sheets(todo, out, force, max_frames, interval)
    n_tr = 0 if no_transcripts else do_transcripts(todo, out, force, model, language, device, threads)
    for c in clips:
        data = load_transcript(out, c["id"])
        if data is not None:
            segs = [s for s in data["segments"] if not s.get("suspect")]
            c["words"] = sum(len(s["words"]) or len(s["text"].split()) for s in segs)
            c["speech_seconds"] = round(sum(s["end"] - s["start"] for s in segs), 1)
    save_clips(clips, out)
    write_readable(clips, out)
    try:
        pages = make_overview(clips, out)
    except (ImportError, ValueError):
        pages = 0
    silent = sum(1 for c in clips if c.get("words") == 0)
    total = sum(c["duration"] for c in clips)
    return {"output": str(out), "clips": len(clips), "duration_s": round(total, 1), "sheets": n_sheets,
            "transcribed": n_tr, "without_speech": silent, "overview_pages": pages,
            "text": f"Done: {len(clips)} clips, {fmt_ts(total, 0)} of footage, {silent} without recognized speech, "
                    f"{pages} overview pages -> {out}"}
