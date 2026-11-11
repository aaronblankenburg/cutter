"""What the measurements of a clip mean for the cut: camera moves, usable ranges, flaws, time ranges of flags.

Pure Python on the samples that image_analysis measures five times a second, so it runs (and is tested) without
OpenCV. All speeds are in % of the picture width per second, like the per-second values.

  camera(samples)          -> per-sample values and segments [{"start", "end", "move", "speed", "shake"}]
  usable(samples, values)  -> ranges worth cutting from and the flawed ranges with the reason
  ranges(times, flags)     -> [{"start", "end"}] of a per-sample or per-second flag
"""
import math

STATIC, PAN, WHIP = 1.0, 2.5, 40.0      # camera speed: below = static, above = a pan or tilt, above = too fast to use
SHAKY, CALM = 2.0, 0.6                  # unsteadiness: above = shaky, below = as calm as a tripod
ZOOM = 4.0                              # growth of the picture in % per second: a zoom or the camera moving forward
SOFT, BLURRED = 0.5, 0.25               # sharpness below this share of the sharp frames nearby / of the whole clip
NEARBY = 4.0                            # seconds to each side that count as nearby
DARK, CLIPPED = 0.5, 0.25               # share of the picture below luma 10 / above luma 245
MIN_USABLE, MIN_MOVE, CLOSE_GAP = 1.0, 1.0, 0.4

MOVES = ("static", "handheld", "shaky", "pan_left", "pan_right", "tilt_up", "tilt_down", "push_in", "pull_out")


def moving_average(values, n):
    half, out = n // 2, []
    for i in range(len(values)):
        w = values[max(0, i - half):i + half + 1]
        out.append(sum(w) / len(w))
    return out


def moving_median(values, n):
    half, out = n // 2, []
    for i in range(len(values)):
        w = sorted(values[max(0, i - half):i + half + 1])
        out.append(w[len(w) // 2] if len(w) % 2 else (w[len(w) // 2 - 1] + w[len(w) // 2]) / 2)
    return out


def percentile(values, q):
    v = sorted(values)
    if not v:
        return 0.0
    k = (len(v) - 1) * q / 100
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def _step(samples):
    return (samples[-1]["t"] - samples[0]["t"]) / (len(samples) - 1) if len(samples) > 1 else 0.2


def _move(tx, ty, zoom, shake):
    speed = math.hypot(tx, ty)
    if shake >= SHAKY:
        return "shaky"
    if speed >= PAN:                     # the picture moves against the camera: content to the right = a pan to the left
        if abs(tx) >= abs(ty):
            return "pan_left" if tx > 0 else "pan_right"
        return "tilt_up" if ty > 0 else "tilt_down"
    if abs(zoom) >= ZOOM:
        return "push_in" if zoom > 0 else "pull_out"
    return "static" if speed < STATIC and shake < CALM else "handheld"


def _mode_filter(labels, n=5):
    half, out = n // 2, []
    for i in range(len(labels)):
        w = labels[max(0, i - half):i + half + 1]
        best = max(set(w), key=lambda x: (w.count(x), x == labels[i]))
        out.append(best)
    return out


def camera(samples, duration=None):
    """samples: [{"t", "gx", "gy", "zoom"}] (global motion of the picture; None for the first frame).
    Returns (values, segments): values per sample {"speed", "shake", "move"}; segments of one move each."""
    if not samples:
        return [], []
    step, window = _step(samples), max(3, round(1.0 / _step(samples)) | 1)
    gx = [s.get("gx") or 0.0 for s in samples]
    gy = [s.get("gy") or 0.0 for s in samples]
    tx, ty = moving_average(gx, window), moving_average(gy, window)
    # Unsteadiness: how far a sample lies off the line between its neighbours. A pan that starts or stops is off
    # for a sample or two, shaking all the time, hence the median. 1.22 makes it comparable to the per-second shake.
    off = [0.0] * len(gx)
    for i in range(1, len(gx) - 1):
        off[i] = math.hypot(gx[i] - (gx[i - 1] + gx[i + 1]) / 2, gy[i] - (gy[i - 1] + gy[i + 1]) / 2) / 1.22
    if len(off) > 2:
        off[0], off[-1] = off[1], off[-2]
    shake = moving_median(off, window)
    zoom = moving_average([s.get("zoom") or 0.0 for s in samples], window)
    moves = _mode_filter([_move(a, b, z, k) for a, b, z, k in zip(tx, ty, zoom, shake)])
    values = [{"speed": math.hypot(a, b), "shake": k, "move": m} for a, b, k, m in zip(tx, ty, shake, moves)]
    runs = []
    for i, m in enumerate(moves):
        if runs and runs[-1][0] == m:
            runs[-1][2] = i
        else:
            runs.append([m, i, i])
    changed = True
    while changed and len(runs) > 1:      # a move shorter than MIN_MOVE is part of the longer neighbour
        changed = False
        for k, (m, a, b) in enumerate(runs):
            if (b - a + 1) * step < MIN_MOVE:
                near = [j for j in (k - 1, k + 1) if 0 <= j < len(runs)]
                j = max(near, key=lambda j: runs[j][2] - runs[j][1])
                runs[j][1], runs[j][2] = min(runs[j][1], a), max(runs[j][2], b)
                del runs[k]
                changed = True
                break
    merged = []
    for m, a, b in runs:
        if merged and merged[-1][0] == m:
            merged[-1][2] = b
        else:
            merged.append([m, a, b])
    end = duration if duration else samples[-1]["t"] + step
    segments = []
    for k, (m, a, b) in enumerate(merged):
        for v in values[a:b + 1]:
            v["move"] = m
        part = values[a:b + 1]
        z = zoom[a:b + 1]
        segments.append({"start": round(samples[a]["t"] if k else 0.0, 2),
                         "end": round(min(end, samples[b]["t"] + step) if k < len(merged) - 1 else end, 2), "move": m,
                         "speed": round(sum(abs(x) for x in z) / len(z) if m in ("push_in", "pull_out")
                                        else sum(v["speed"] for v in part) / len(part), 1),
                         "shake": round(sum(v["shake"] for v in part) / len(part), 2)})
    return values, segments


def usable(samples, values, duration=None):
    """Ranges worth cutting from, and the flawed ranges with the reason.

    samples: [{"t", "sharp", "low", "high"}], values from camera(). Sharpness depends on the subject, so it is
    judged within the clip: 'soft' means clearly less sharp than the sharp frames a few seconds around it (the
    focus hunting, a bump), or far less sharp than the clip's sharp frames (a long blur). A pan from a plain wall to
    a detailed one is neither. A clip that is out of focus from start to end is not found this way (see low_detail
    in the index). Shake is a flaw from SHAKY on; in
    a clip that is unsteady throughout (an action camera in the hand) only its worst moments are, so that the
    calmer parts remain to choose from – their camera move still says 'shaky'."""
    if not samples:
        return [], []
    step = _step(samples)
    sharp = [s["sharp"] for s in samples]
    ref, near = percentile(sharp, 75), max(1, round(NEARBY / step))
    too_shaky = max(SHAKY, 1.5 * percentile([v["shake"] for v in values], 50))
    why = []
    for i, (s, v) in enumerate(zip(samples, values)):
        r = []
        if ref > 0 and (s["sharp"] < BLURRED * ref or s["sharp"] < SOFT * percentile(sharp[max(0, i - near):i + near + 1], 75)):
            r.append("soft")
        if v["shake"] >= too_shaky:
            r.append("shaky")
        if v["speed"] >= WHIP:
            r.append("whip")
        if s.get("low", 0) > DARK:
            r.append("dark")
        if s.get("high", 0) > CLIPPED:
            r.append("clipped")
        why.append(r)
    ok = [not r for r in why]
    gap = max(1, round(CLOSE_GAP / step))
    i = 0
    while i < len(ok):                    # a flaw of a frame or two inside a good range does not split it
        if not ok[i]:
            j = i
            while j < len(ok) and not ok[j]:
                j += 1
            if 0 < i and j < len(ok) and j - i <= gap:
                for k in range(i, j):
                    ok[k] = True
            i = j
        else:
            i += 1
    end = duration if duration else samples[-1]["t"] + step
    good, flaws, i = [], [], 0
    while i < len(ok):
        j = i
        while j < len(ok) and ok[j] == ok[i]:
            j += 1
        a = samples[i]["t"] if i else 0.0
        b = min(end, samples[j]["t"]) if j < len(ok) else end
        part = values[i:j]
        if ok[i] and b - a >= MIN_USABLE:
            moves = [v["move"] for v in part]
            main = max(set(moves), key=moves.count)
            calm = sum(v["shake"] for v in part) / len(part)
            good.append({"start": round(a, 2), "end": round(b, 2), "camera": main,
                         "steady": bool(calm < CALM + 0.2 and main in ("static", "handheld"))})
        elif not ok[i]:
            reasons = [r for r in ("soft", "shaky", "whip", "dark", "clipped") if any(r in w for w in why[i:j])]
            flaws.append({"start": round(a, 2), "end": round(b, 2), "why": reasons})
        elif b - a > 0:
            flaws.append({"start": round(a, 2), "end": round(b, 2), "why": ["short"]})
        i = j
    return good, flaws


def ranges(times, flags, step=1.0, min_len=1.0, close_gap=0.0, end=None):
    """[{"start", "end"}] where flags are set; ranges shorter than min_len are dropped, gaps up to close_gap closed."""
    out = []
    for t, f in zip(times, flags):
        if not f:
            continue
        if out and t - out[-1]["end"] <= close_gap + 1e-6:
            out[-1]["end"] = t + step
        else:
            out.append({"start": t, "end": t + step})
    res = []
    for r in out:
        if end is not None:
            r["end"] = min(r["end"], end)
        if r["end"] - r["start"] >= min_len - 1e-6:
            res.append({"start": round(r["start"], 2), "end": round(r["end"], 2)})
    return res


def total(rs):
    return round(sum(r["end"] - r["start"] for r in rs), 1)
