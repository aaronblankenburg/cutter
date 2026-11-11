"""What the camera recorded next to the picture: the place, and on action cameras and drones the path and the gyroscope.

  gps_from_tags(tags)   the place in the container metadata (phones: ISO 6709, e.g. "+48.1374+011.5755+519/")
  gopro(data, duration) GoPro GPMF (the 'gpmd' data track): gyroscope per second and the GPS track
  dji_srt(text)         DJI flight log subtitles (a .SRT next to the video or a subtitle track): the GPS track
  read(path, duration)  whichever of these the file has, or None

GPMF is read as documented at https://github.com/gopro/gpmf-parser (KLV, big endian, 32-bit aligned; SCAL is a
divisor for the data that follows it in the same stream). Only magnitudes are taken from the gyroscope, so the axis
order, which differs between camera models, does not matter.
"""
import json
import math
import pathlib
import re
import struct
import subprocess

ISO6709 = re.compile(r"^\s*([+-]\d{1,3}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)([+-]\d+(?:\.\d+)?)?")
LOCATION_TAGS = ("com.apple.quicktime.location.iso6709", "location", "location-eng", "com.android.location", "xyz")
FORMATS = {"b": "b", "B": "B", "s": "h", "S": "H", "l": "l", "L": "L", "f": "f", "d": "d", "j": "q", "J": "Q"}


def gps_from_tags(tags):
    """{"lat", "lon"[, "alt"]} from container tags (keys in lower case), or None."""
    for key in LOCATION_TAGS:
        m = ISO6709.match(str(tags.get(key) or ""))
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if abs(lat) <= 90 and abs(lon) <= 180 and (lat or lon):
                gps = {"lat": round(lat, 6), "lon": round(lon, 6)}
                if m.group(3):
                    gps["alt"] = round(float(m.group(3)), 1)
                return gps
    return None


# ------------------------------------------------------------------ GoPro GPMF

def klv(data):
    """(key, type, sample size, repeat, payload) for every item of a GPMF block."""
    i = 0
    while i + 8 <= len(data):
        key, typ, size = data[i:i + 4], data[i + 4], data[i + 5]
        repeat = int.from_bytes(data[i + 6:i + 8], "big")
        n = size * repeat
        yield key.decode("latin-1"), (chr(typ) if typ else None), size, repeat, data[i + 8:i + 8 + n]
        i += 8 + (n + 3) // 4 * 4


def numbers(typ, size, repeat, payload, complex_type=None):
    """The samples of an item as lists of numbers; [] for types that are not numbers."""
    if typ == "?" and complex_type:
        fmt = ">" + "".join(FORMATS.get(c, "") for c in complex_type)
    elif typ in FORMATS:
        width = struct.calcsize(">" + FORMATS[typ])
        fmt = ">" + FORMATS[typ] * (size // width if size >= width else 0)
    else:
        return []
    if len(fmt) < 2 or struct.calcsize(fmt) != size:
        return []
    return [list(struct.unpack_from(fmt, payload, k * size)) for k in range(repeat) if (k + 1) * size <= len(payload)]


def gopro(data, duration):
    """Gyroscope and GPS from the concatenated payloads of a gpmd track (about one payload per second)."""
    payloads = []
    for key, typ, _, _, block in klv(data):
        if key != "DEVC" or typ is not None:
            continue
        p = {"gyro": [], "gps": [], "fix": None, "dop": None}
        for k2, t2, _, _, stream in klv(block):
            if k2 != "STRM" or t2 is not None:
                continue
            scale, ctype = [1.0], None
            for k3, t3, size, repeat, payload in klv(stream):
                if k3 == "SCAL":
                    scale = [float(v[0]) or 1.0 for v in numbers(t3, size, repeat, payload)] or [1.0]
                elif k3 == "TYPE":
                    ctype = payload.decode("latin-1").rstrip("\0")
                elif k3 == "GPSF":
                    p["fix"] = (numbers(t3, size, repeat, payload) or [[None]])[0][0]
                elif k3 == "GPSP":
                    p["dop"] = (numbers(t3, size, repeat, payload) or [[None]])[0][0]
                elif k3 in ("GYRO", "GPS5", "GPS9"):
                    rows = numbers(t3, size, repeat, payload, ctype)
                    rows = [[v / scale[min(j, len(scale) - 1)] for j, v in enumerate(r)] for r in rows]
                    if k3 == "GYRO":
                        p["gyro"] = [r for r in rows if len(r) == 3]
                    elif k3 == "GPS5":
                        p["gps"] = [r[:5] for r in rows if len(r) >= 5]
                    else:
                        p["gps"] = [r[:5] for r in rows if len(r) >= 9 and r[8] >= 2]
                        p["fix"] = 3 if p["gps"] else 0
        payloads.append(p)
    if not payloads:
        return None
    span = (duration or len(payloads)) / len(payloads)
    out, gyro, track = {"source": "gopro"}, [], []
    for k, p in enumerate(payloads):
        t = round(k * span, 2)
        if p["gyro"]:
            n = len(p["gyro"])
            mean = [sum(r[a] for r in p["gyro"]) / n for a in range(3)]
            dev = math.sqrt(sum(sum((r[a] - mean[a]) ** 2 for a in range(3)) for r in p["gyro"]) / n)
            gyro.append({"t": t, "turn": round(math.degrees(math.sqrt(sum(m * m for m in mean))), 1),
                         "shake": round(math.degrees(dev), 1)})
        good = [r for r in p["gps"] if (r[0] or r[1]) and abs(r[0]) <= 90 and abs(r[1]) <= 180]
        if good and (p["fix"] or 0) >= 2 and (p["dop"] is None or p["dop"] < 1000):
            n = len(good)
            track.append([t] + [round(sum(r[a] for r in good) / n, d) for a, d in ((0, 6), (1, 6), (2, 1))]
                         + [round(sum(r[3] for r in good) / n * 3.6, 1)])
    if gyro:
        out["gyro"] = gyro
    if track:
        out.update(_track(track))
    return out if len(out) > 1 else None


def _track(track):
    """track: [[t, lat, lon, alt, km/h or None]] -> {"gps": first point, "track", "distance_m", "speed_kmh"}."""
    dist = 0.0
    for a, b in zip(track, track[1:]):
        dlat, dlon = math.radians(b[1] - a[1]), math.radians(b[2] - a[2])
        h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[1])) * math.cos(math.radians(b[1])) * math.sin(dlon / 2) ** 2
        dist += 2 * 6371000 * math.asin(min(1.0, math.sqrt(h)))
    first = track[0]
    out = {"gps": {"lat": first[1], "lon": first[2], "alt": first[3]}, "track": track, "distance_m": round(dist)}
    speeds = [p[4] for p in track if len(p) > 4 and p[4] is not None]
    if speeds:
        out["speed_kmh"] = {"mean": round(sum(speeds) / len(speeds), 1), "max": round(max(speeds), 1)}
    elif track[-1][0] > track[0][0]:
        out["speed_kmh"] = {"mean": round(dist / (track[-1][0] - track[0][0]) * 3.6, 1)}
    return out


# ------------------------------------------------------------------ DJI flight log subtitles

SRT_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d+)\s*-->")
DJI_NEW = (re.compile(r"\[latitude\s*:\s*(-?[\d.]+)\]", re.I), re.compile(r"\[long?t?itude\s*:\s*(-?[\d.]+)\]", re.I))
DJI_ALT = re.compile(r"(?:abs_alt|altitude)\s*:\s*(-?[\d.]+)", re.I)
DJI_OLD = re.compile(r"GPS\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)")      # longitude, latitude, altitude


def dji_srt(text):
    """The GPS track of a DJI flight log (.SRT), at most one point per second; None when there is none."""
    track, last = [], -1.0
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        m = SRT_TIME.search(block)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + float("0." + m.group(4))
        lat = DJI_NEW[0].search(block)
        lon = DJI_NEW[1].search(block)
        old = DJI_OLD.search(block)
        if lat and lon:
            alt = DJI_ALT.search(block)
            point = [float(lat.group(1)), float(lon.group(1)), float(alt.group(1)) if alt else None]
        elif old:
            point = [float(old.group(2)), float(old.group(1)), float(old.group(3))]
        else:
            continue
        if not (point[0] or point[1]) or abs(point[0]) > 90 or abs(point[1]) > 180 or t - last < 1.0:
            continue
        last = t
        track.append([round(t, 2), round(point[0], 6), round(point[1], 6), point[2], None])
    return dict({"source": "dji"}, **_track(track)) if track else None


# ------------------------------------------------------------------ one clip

def read(path, duration, info=None):
    """The telemetry of a clip, or None. info: the ffprobe result when it is already at hand."""
    path = pathlib.Path(path)
    if info is None:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        info = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    streams = info.get("streams") or []
    found = None
    gpmd = next((s for s in streams if s.get("codec_tag_string") == "gpmd"), None)
    if gpmd is not None:
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", f"0:{gpmd['index']}",
                            "-c", "copy", "-f", "data", "-"], capture_output=True)
        if r.returncode == 0 and r.stdout:
            found = gopro(r.stdout, duration)
    if found is None:
        for side in (path.with_suffix(".SRT"), path.with_suffix(".srt")):
            if side.is_file() and side.stat().st_size < 50e6:
                found = dji_srt(side.read_text(encoding="utf-8", errors="replace"))
                break
    if found is None and any(s.get("codec_type") == "subtitle" for s in streams):
        r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:s:0", "-f", "srt", "-"],
                           capture_output=True)
        if r.returncode == 0 and r.stdout:
            found = dji_srt(r.stdout.decode("utf-8", "replace"))
    if found is None:
        tags = {k.lower(): v for k, v in ((info.get("format") or {}).get("tags") or {}).items()}
        gps = gps_from_tags(tags)
        found = {"source": "tags", "gps": gps} if gps else None
    return found
