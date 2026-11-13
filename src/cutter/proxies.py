"""Proxies: small copies of the footage that decode fast, for drafts.

Large footage (4K, HEVC, 10 bit) is slow to decode, and every draft decodes it again: the rough render of the
review data, the FilmCraft export. A proxy is the same clip at a lower height in H.264 with the same frames at the
same times and the same sound, so a timeline built from proxies cuts at the same places as one built from the
footage. The final film is built from the footage.

  make(clips, folder)       <folder>/<ID>.mp4 for every clip that needs one, and <folder>/proxies.json
  table(clips, folder)      {clip ID: {"path", "width", "height"}} of the proxies that can stand in for their clip

A clip needs no proxy when it is H.264 and not higher than the proxy would be: it is used as it is. A proxy stands
in for its clip as long as the clip's file is the one the proxy was made from (size and modification time) and the
proxy is as long as the clip. The footage is only read.
"""
import concurrent.futures as cf
import os
import pathlib
import subprocess
import time

from .util import CutterError, MISSING, USAGE, log, read_json, write_json

HEIGHT = 720            # of the proxies (cutter.toml → [proxies] height)
INDEX = "proxies.json"
EASY = ("h264",)        # codecs that decode fast enough at the proxy size: such clips are used as they are


def _display(clip):
    """Width and height as shown (a phone clip that is stored lying on its side is upright)."""
    w, h = clip.get("width") or 0, clip.get("height") or 0
    return (h, w) if (clip.get("rotation") or 0) in (90, 270, -90) else (w, h)


def needed(clip, height=HEIGHT):
    w, h = _display(clip)
    return bool(clip.get("has_video", True)) and (min(w, h) > height or (clip.get("codec") or "h264") not in EASY)


def size(clip, height=HEIGHT):
    """Width and height of the proxy: the shorter side becomes `height` (never larger than the clip), both even."""
    w, h = _display(clip)
    if not w or not h:
        return None
    f = min(1.0, height / min(w, h))
    return max(2, round(w * f / 2) * 2), max(2, round(h * f / 2) * 2)


def _source(clip):
    st = os.stat(clip["path"])
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def colour(path, hd):
    """The ffmpeg options that say how the proxy's colours are to be read: as the clip's. A clip that does not say
    is read by its size (HD as BT.709, smaller as BT.601) – so a proxy that is smaller than HD has to say what its
    HD clip did not need to, or its colours come out different."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=color_space,color_primaries,color_transfer", "-of", "default=nw=1", str(path)],
                       capture_output=True, text=True)
    said = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    out = []
    for key, option in (("color_space", "-colorspace"), ("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc")):
        value = said.get(key) if said.get(key) not in (None, "", "unknown", "unspecified", "reserved") else ("bt709" if hd else None)
        if value:
            out += [option, value]
    return out


def command(clip, target, height=HEIGHT):
    """One H.264 frame for every frame of the clip at the same time (no frame-rate conversion), a key frame every
    half second so that cutting into the clip is quick, the first sound track as AAC. ffmpeg turns the picture
    upright where the clip says it is rotated. The colours stay the clip's: the values are not converted (only
    full range becomes video range), and the proxy carries the clip's colour tags."""
    w, h = size(clip, height)
    gop = max(1, round(float(clip.get("fps") or 25) / 2))
    hd = (clip.get("height") or 0) >= 720 or (clip.get("width") or 0) >= 1280
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", clip["path"], "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
           "-map_metadata", "-1", "-vf", f"scale={w}:{h}:flags=bilinear:out_range=tv,setsar=1,format=yuv420p",
           "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-g", str(gop), "-bf", "0",
           *colour(clip["path"], hd), "-color_range", "tv",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-f", "mp4", str(target)]
    return cmd


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=duration,width,height",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    try:
        w, h, d = r.stdout.strip().split(",")[:3]
        return float(d), int(w), int(h)
    except ValueError:
        return None


def load(folder):
    f = pathlib.Path(folder) / INDEX
    return read_json(f) if f.exists() else None


def _current(entry, clip, folder, height=None):
    """Whether the index entry still stands for the clip's file (and, when given, for this proxy height)."""
    if not entry or (height is not None and entry.get("for_height") != height):
        return False
    try:
        if entry.get("source") != _source(clip):
            return False
    except OSError:
        return False
    return not entry.get("file") or (pathlib.Path(folder) / entry["file"]).exists()


def make(clips, folder, height=HEIGHT, only=None, force=False, jobs=None, dry_run=False):
    """Make the missing proxies. Returns {"made", "kept", "as_is", "failed", "seconds", "mb", "folder"}."""
    if not 144 <= int(height) <= 2160:
        raise CutterError(f"Proxy height {height} is not between 144 and 2160", USAGE, "cutter.toml → [proxies] height")
    height = int(height)
    folder = pathlib.Path(folder)
    index = load(folder) or {}
    entries = dict(index.get("clips") or {})
    wanted = {x.strip().upper() for x in only.split(",") if x.strip()} if only else None
    todo, as_is, kept = [], 0, 0
    for c in clips:
        if wanted is not None and c["id"] not in wanted:
            continue
        if not c.get("has_video", True) or not os.path.exists(c["path"]):
            continue
        if not needed(c, height):
            as_is += 1
            if not dry_run:
                entries[c["id"]] = {"file": None, "for_height": height, "source": _source(c)}
        elif not force and _current(entries.get(c["id"]), c, folder, height):
            kept += 1
        else:
            todo.append(c)
    total = sum(c["duration"] for c in todo)
    report = {"folder": str(folder), "height": height, "made": 0, "kept": kept, "as_is": as_is, "failed": [],
              "footage_s": round(total, 1)}
    if dry_run:
        return dict(report, to_make=[c["id"] for c in todo])
    folder.mkdir(parents=True, exist_ok=True)
    if todo:
        log(f"Proxies ({height}p): {len(todo)} clips, {total / 60:.1f} min of footage …")
    jobs = jobs or max(1, min(4, (os.cpu_count() or 2) // 4))
    t0, done = time.time(), [0]

    def one(c):
        target, part = folder / f'{c["id"]}.mp4', folder / f'{c["id"]}.part.mp4'
        r = subprocess.run(command(c, part, height), capture_output=True, text=True)
        got = _duration(part) if r.returncode == 0 else None
        frame = 1.0 / float(c.get("fps") or 25)
        problem = None
        if got is None:
            problem = (r.stderr.strip().splitlines() or ["ffmpeg could not make it"])[-1]
        elif abs(got[0] - c["duration"]) > 2 * frame + 0.02:
            problem = f'the proxy is {got[0]:.2f} s long, the clip {c["duration"]:.2f} s'
        done[0] += 1
        if problem:
            part.unlink(missing_ok=True)
            log(f'  [{done[0]}/{len(todo)}] {c["id"]}  ! no proxy ({problem}); the footage is used')
            return c, None, problem
        os.replace(part, target)
        log(f'  [{done[0]}/{len(todo)}] {c["id"]}  {c.get("width")}x{c.get("height")} {c.get("codec") or ""} -> '
            f'{got[1]}x{got[2]}, {target.stat().st_size / 1e6:.0f} MB')
        return c, {"file": target.name, "width": got[1], "height": got[2], "duration": round(got[0], 3),
                   "for_height": height, "source": _source(c)}, None

    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for c, entry, problem in ex.map(one, todo):
            if entry:
                entries[c["id"]] = entry
                report["made"] += 1
            else:
                entries.pop(c["id"], None)
                report["failed"].append({"clip": c["id"], "problem": problem})
    known = {c["id"] for c in clips}
    for cid in [k for k in entries if k not in known]:           # proxies of clips that are gone
        if entries[cid].get("file"):
            (folder / entries[cid]["file"]).unlink(missing_ok=True)
        del entries[cid]
    write_json(folder / INDEX, {"version": 1, "height": height, "clips": entries})
    report["seconds"] = round(time.time() - t0, 1)
    report["mb"] = round(sum((folder / e["file"]).stat().st_size for e in entries.values()
                             if e.get("file") and (folder / e["file"]).exists()) / 1e6, 1)
    return report


def table(clips, folder):
    """{clip ID: {"path", "width", "height"}} for the clips a proxy stands in for, and the IDs of the clips that
    have neither a proxy nor the word that they need none (new footage, or footage that changed)."""
    index = load(folder)
    if index is None:
        raise CutterError(f"No proxies in {folder}", MISSING, "First: cutter analyze --proxies")
    out, missing = {}, []
    for c in clips:
        e = (index.get("clips") or {}).get(c["id"])
        if not c.get("has_video", True):
            continue
        if not _current(e, c, folder):
            missing.append(c["id"])
        elif e.get("file"):
            out[c["id"]] = {"path": str(pathlib.Path(folder) / e["file"]), "width": e["width"], "height": e["height"]}
    return out, missing
