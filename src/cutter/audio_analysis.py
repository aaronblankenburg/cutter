"""Sound of a clip without any model download: level, clipping, wind, silence, speech and loudness.

measure(path, duration) decodes the audio once (16 kHz, up to two channels) and returns
  per_second  [{"level": dBFS of the second, "wind": 0..1}]
  summary     {"lufs", "peak_db", "clipping": [ranges], "wind" or "rumble": [ranges], "silence": [ranges],
               "speech": [ranges]}

Wind is a heuristic, not a classifier: most of the energy lies below 150 Hz and the two channels do not agree down
there (wind hits each microphone on its own; an engine or bass music reaches both alike). With one microphone – one
channel, or two identical ones as many action cameras record – only the first half can be checked: that is reported
as "rumble", which is wind or an engine. Speech comes from the voice activity detection that ships with
faster-whisper (no download); without it the field is left out.
"""
import math
import subprocess

from .quality import ranges

SR = 16000
LOW_HZ = 150                 # wind lives below this
SILENT_DB = -55.0            # a second quieter than this counts as silence
CLIP_LEVEL = 0.985           # samples at or above this (of full scale) are clipped
WIND = 0.5                   # score from which a second counts as windy


def decode(path, channels):
    import numpy as np
    ch = 2 if (channels or 1) >= 2 else 1
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:a:0", "-ac", str(ch),
                        "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip() or "ffmpeg could not read the audio")
    y = np.frombuffer(r.stdout, dtype=np.float32)
    return y[:y.size - y.size % ch].reshape(-1, ch)


def db(x):
    return round(20 * math.log10(x), 1) if x > 1e-7 else -140.0


def wind_score(block, stereo=True):
    """0..1 for one second of audio (samples x channels); stereo: the channels come from two microphones."""
    import numpy as np
    spec = np.fft.rfft(block * np.hanning(len(block))[:, None], axis=0)
    freq = np.fft.rfftfreq(len(block), 1 / SR)
    power = (np.abs(spec) ** 2)
    low, band = (freq >= 20) & (freq < LOW_HZ), freq >= 20
    total = float(power[band].sum())
    if total <= 0:
        return 0.0
    share = float(power[low].sum()) / total
    score = min(1.0, max(0.0, (share - 0.55) / 0.35))
    if stereo and block.shape[1] >= 2 and score > 0:
        a, b = spec[low, 0], spec[low, 1]
        den = math.sqrt(float((np.abs(a) ** 2).sum()) * float((np.abs(b) ** 2).sum()))
        agree = abs(complex((a * np.conj(b)).sum())) / den if den > 0 else 1.0
        score *= min(1.0, max(0.0, (0.95 - agree) / 0.45))
    return round(score, 2)


def speech_ranges(mono):
    """Voice activity (Silero VAD from faster-whisper); None when it is not installed."""
    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps
    except ImportError:
        return None
    try:
        found = get_speech_timestamps(mono, VadOptions(min_silence_duration_ms=500))
    except Exception:
        return None
    return [{"start": round(s["start"] / SR, 2), "end": round(s["end"] / SR, 2)} for s in found]


def measure(path, duration, channels=2, loudness=None):
    import numpy as np
    y = decode(path, channels)
    if y.shape[0] < SR // 10:
        return [], {"silent": True}
    n_sec = max(1, math.ceil(min(duration or 1e9, y.shape[0] / SR)))
    per, clipped, level = [], [], []
    stereo = False                       # two channels that are the same signal are one microphone
    if y.shape[1] >= 2:
        a, b = y[:, 0] - y[:, 0].mean(), y[:, 1] - y[:, 1].mean()
        den = math.sqrt(float((a * a).sum()) * float((b * b).sum()))
        stereo = den > 0 and float((a * b).sum()) / den < 0.995
    for i in range(n_sec):
        block = y[i * SR:(i + 1) * SR]
        if not len(block):
            break
        rms = float(np.sqrt((block.mean(axis=1) ** 2).mean()))
        level.append(db(rms))
        clipped.append(float((np.abs(block) >= CLIP_LEVEL).mean()) > 0.0005)
        w = wind_score(block, stereo) if len(block) >= SR // 2 and level[-1] > SILENT_DB else 0.0
        per.append({"level": level[-1], "wind": w})
    t = list(range(len(per)))
    end = round(y.shape[0] / SR, 2)
    summary = {"peak_db": db(float(np.abs(y).max())), "channels": int(y.shape[1]) if stereo else 1,
               "clipping": ranges(t, clipped, min_len=1.0, close_gap=1.0, end=end),
               "wind" if stereo else "rumble": ranges(t, [p["wind"] >= WIND for p in per], min_len=2.0, close_gap=1.0, end=end),
               "silence": ranges(t, [v <= SILENT_DB for v in level], min_len=1.0, end=end)}
    if loudness is not None:
        summary["lufs"] = loudness
    sp = speech_ranges(np.ascontiguousarray(y.mean(axis=1)))
    if sp is not None:
        summary["speech"] = sp
    return per, summary
