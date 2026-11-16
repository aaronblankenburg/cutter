"""Stand-in files with a sidecar: analysis reads a small copy, the metadata is the original's.

A tool that cannot hand over the original footage (for example a web app whose browser makes 360p copies) gives
cutter a folder of stand-ins and a folder of sidecars. For a stand-in `<media root>/<name>.mp4` the sidecar is
`<sidecars>/<name>.json`; raw telemetry of the original may sit next to it as `<name>.gpmd` (GoPro GPMF samples back
to back, as `ffmpeg -c copy -f data` writes them) and `<name>.srt` (DJI flight data). The format is in
docs/formats.md ("Stand-ins and sidecars").

Everything about the clip that clips.json states (name, size, duration, frame rate, size in pixels, rotation, start
timecode, device, place, recording time) comes from the sidecar; `path` is the stand-in, because that is the file
every tool reads.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .util import CutterError, MISSING

FORMATS = ("cutlery.sidecar/1",)
CODEC_NAMES = {"avc": "h264"}                    # ffprobe's names are the ones clips.json uses
STANDIN_EXT = ".mp4"
PROBLEM_FRAMES = 2                                # the proxy contract of proxies.py: same length within 2 frames + 0.02 s


def sidecar_files(standin, root, sidecars):
    """{"json", "gpmd", "srt"} paths for a stand-in below the media root `root` (they need not exist)."""
    rel = Path(standin).resolve().relative_to(Path(root).resolve())
    name = rel.as_posix()
    if name.lower().endswith(STANDIN_EXT):
        name = name[:-len(STANDIN_EXT)]
    base = Path(sidecars) / name
    return {k: Path(f"{base}.{k}") for k in ("json", "gpmd", "srt")}


def load(path):
    """The sidecar dict, checked for the fields clips.json needs. Raises ValueError."""
    sc = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(sc, dict) or sc.get("format") not in FORMATS:
        raise ValueError(f"not a sidecar (format must be one of {', '.join(FORMATS)})")
    for key in ("file", "rel_path", "size", "duration", "fingerprint"):
        if sc.get(key) in (None, ""):
            raise ValueError(f"sidecar has no {key}")
    if sc.get("unsupported"):
        raise ValueError("the original could not be read by the browser")
    return sc


def key(clip):
    """What identifies a clip across runs: the original's fingerprint, else the path of the file read."""
    fp = (clip.get("original") or {}).get("fingerprint")
    if fp:
        return f"fp:{fp['size']}:{fp['head_sha256']}:{fp['tail_sha256']}"
    return clip["path"]


def _rate(text):
    try:
        num, den = str(text).split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def clip_from_sidecar(standin, sc, files, probed, parse_time, time_from_name):
    """The clips.json entry. `probed` is ffprobe's result for the stand-in (its size, length and streams)."""
    standin = Path(standin)
    tags = {k.lower(): v for k, v in (sc.get("tags") or {}).items()}
    created, source = time_from_name(sc["file"]), "filename"
    if not created:
        source = "metadata"
        for value in (tags.get("com.apple.quicktime.creationdate"), tags.get("creation_time"), sc.get("creation_time")):
            created = parse_time(value)
            if created:
                break
    if not created:
        created = parse_time(sc.get("mtime")) or datetime.fromtimestamp(standin.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
        source = "file_date"
    video, audio = sc.get("video"), sc.get("audio")
    clip = {"file": sc["file"], "rel_path": sc["rel_path"], "path": str(standin.resolve()),
            "size_mb": round(sc["size"] / 1e6, 1), "duration": round(float(sc["duration"]), 3),
            "created": created.isoformat(sep=" ", timespec="seconds"), "created_source": source,
            "timecode": sc.get("timecode"), "has_video": video is not None, "has_audio": audio is not None}
    from .analyze import guess_device
    clip["device"] = guess_device(tags, {"tags": {"handler_name": tags.get("handler_name")}} if tags.get("handler_name") else None)
    if video:
        rot = (-int(video.get("rotation") or 0)) % 360          # sidecar: clockwise; clips.json: ffprobe's side data
        w, h = video["width"], video["height"]
        if rot % 180 == 90:
            w, h = h, w
        fps, avg = _rate(video.get("r_frame_rate")), _rate(video.get("avg_frame_rate"))
        codec = video.get("codec")
        clip.update({"codec": CODEC_NAMES.get(codec, codec), "width": w, "height": h, "rotation": rot,
                     "fps": round(fps, 3), "fps_raw": video.get("r_frame_rate"), "fps_avg": round(avg, 3),
                     "vfr": bool(video.get("vfr", fps and avg and abs(fps - avg) / fps > 0.01))})
    if audio:
        clip.update({"audio_rate": int(audio["sample_rate"]), "audio_channels": audio["channels"]})
    from .telemetry import gps_from_tags
    gps = gps_from_tags({**tags, "location": sc.get("gps") or tags.get("location")})
    if gps:
        clip["gps"] = gps
    pv = next((s for s in probed.get("streams") or [] if s.get("codec_type") == "video"), {})
    clip["standin"] = {"width": pv.get("width"), "height": pv.get("height")}
    clip["original"] = {"name": sc["file"], "size": sc["size"], "mtime": sc.get("mtime"), "fingerprint": sc["fingerprint"],
                        "bit_depth": (video or {}).get("bit_depth"), "codec_string": (video or {}).get("codec_string")}
    found = {k: str(files[k].resolve()) for k in ("gpmd", "srt") if files[k].is_file()}
    if found:
        clip["telemetry_files"] = found
    return clip


def length_problem(clip, probed):
    """A text if the stand-in's length differs from the original's by more than the proxy contract allows."""
    fmt = probed.get("format") or {}
    try:
        have = float(fmt.get("duration"))
    except (TypeError, ValueError):
        return None
    fps = clip.get("fps") or 25.0
    allowed = PROBLEM_FRAMES / fps + 0.02
    if abs(have - clip["duration"]) > allowed:
        return f"the stand-in is {have:.3f} s long, the original {clip['duration']:.3f} s"
    return None


def require_sidecars(path):
    if not Path(path).is_dir():
        raise CutterError(f"Sidecar folder not found: {path}", MISSING,
                          "Set [standins] sidecars in cutter.toml to the folder with the .json files.")
