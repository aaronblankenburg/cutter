"""Image and sound analysis per clip (cutter analyze --image).

Writes one file <out>/<ID>.json per clip of clips.json with
  scenes      scene boundaries (PySceneDetect, ContentDetector)
  per_second  per second: sharpness, exposure, shake, camera pan, motion (OpenCV); optionally CLIP tags (content)
              and shot size, and audio events (PANNs) when torch, open_clip and panns_inference are installed
  summary     means, most frequent tags, audio events as time ranges
and an overview <out>/_index.json. The retime step uses sharpness, shake and exposure to avoid flawed seconds.
CPU is enough. The tag list for CLIP comes from cutter.toml → image_analysis.content_tags (neutral defaults below).
"""
import json
import math
import pathlib
import subprocess
import time

from .util import CutterError, MISSING, dump_json, log, read_json

CONTENT_TAGS = {
    "landscape": "a wide landscape", "road": "an asphalt road", "street": "a town street with buildings",
    "beach": "a sandy beach", "sea": "the sea with waves", "forest": "a forest", "mountains": "mountains",
    "desert": "sand dunes in the desert", "sky": "mostly sky", "sunset": "a sunset sky with the low sun",
    "night": "a very dark night scene", "indoor": "a room interior", "vehicle": "a car, truck or bus",
    "group": "a group of people", "hands": "hands holding something", "food": "food on a table",
}
SHOT_TAGS = {
    "close-up hands": "a close-up shot of hands doing something", "detail": "an extreme close-up of an object",
    "face": "a close-up shot of a person's face", "medium": "a medium shot of a person from the waist up",
    "full": "a full shot of a person from head to toe", "wide": "a wide shot of a small person in a large landscape",
    "empty landscape": "an empty landscape without people", "over the shoulder": "an over-the-shoulder shot",
    "selfie": "a selfie of a person holding the camera at arm's length", "low angle": "a low angle shot from the ground looking up",
    "high angle": "a high angle shot looking down", "ground camera": "a camera lying flat on the ground",
    "silhouette": "a dark silhouette against a bright sky",
}
SAMPLE_FPS = 5          # frames per second for the OpenCV measures
WORK_W = 480            # working width for sharpness/exposure
FLOW_W = 320            # working width for optical flow
UNITS = {"sharpness": "variance of the Laplacian at 480 px width (higher = sharper)",
         "exposure": "luma 0-255; share below 10 (too_dark) and above 245 (clipped)",
         "pan": "global camera motion, % of picture width per second",
         "shake": "unsteadiness of the global motion within the second, % of picture width per second",
         "motion": "motion in the picture without camera motion, % of picture width per second",
         "tags/shot_size": "CLIP ViT-B/32, probability within the tag list",
         "audio": "PANNs Cnn14 (SED), highest probability within the second"}


def detect_scenes(path, threshold):
    from scenedetect import detect, ContentDetector
    scenes = detect(str(path), ContentDetector(threshold=threshold, min_scene_len=12), show_progress=False)
    return [{"start": round(a.get_seconds(), 3), "end": round(b.get_seconds(), 3)} for a, b in scenes]


def video_metrics(path, duration, keep_clip_frames):
    """Measures per second and one frame per second for CLIP (RGB, middle of the second)."""
    import cv2
    import numpy as np
    cv2.setNumThreads(4)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(fps / SAMPLE_FPS))
    n_sec = max(1, math.ceil(duration))
    secs = [dict(sharp=[], luma=[], low=[], high=[], gm=[], resid=[]) for _ in range(n_sec)]
    frames_for_clip = {}
    prev_small, idx = None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = idx / fps
        sec = int(t)
        want_clip = keep_clip_frames and sec < n_sec and sec not in frames_for_clip and (t - sec) >= 0.5
        if idx % step == 0 or want_clip:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            if want_clip:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                frames_for_clip[sec] = cv2.resize(rgb, (336, round(336 * h / w)), interpolation=cv2.INTER_AREA)
            if idx % step == 0 and sec < n_sec:
                work = cv2.resize(frame, (WORK_W, round(WORK_W * h / w)), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
                s = secs[sec]
                s["sharp"].append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
                s["luma"].append(float(gray.mean()))
                s["low"].append(float((gray < 10).mean()))
                s["high"].append(float((gray > 245).mean()))
                small = cv2.resize(gray, (FLOW_W, round(FLOW_W * h / w)), interpolation=cv2.INTER_AREA)
                if prev_small is not None and prev_small.shape == small.shape:
                    flow = cv2.calcOpticalFlowFarneback(prev_small, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                    g = np.median(flow.reshape(-1, 2), axis=0)            # camera motion (global)
                    resid = np.linalg.norm(flow - g, axis=2)               # motion in the picture
                    dt = step / fps
                    s["gm"].append((g / FLOW_W * 100 / dt).tolist())
                    s["resid"].append(float(np.median(resid) / FLOW_W * 100 / dt + resid.mean() / FLOW_W * 100 / dt) / 2)
                prev_small = small
        idx += 1
    cap.release()
    out = []
    for i, s in enumerate(secs):
        if not s["sharp"]:
            out.append({"t": i})
            continue
        gm = np.array(s["gm"]) if s["gm"] else np.zeros((1, 2))
        pan = float(np.linalg.norm(gm.mean(axis=0)))
        shake = float(np.mean(np.linalg.norm(gm - gm.mean(axis=0), axis=1))) if len(gm) > 1 else 0.0
        out.append({"t": i, "sharpness": round(float(np.median(s["sharp"])), 1),
                    "exposure": {"mean": round(float(np.mean(s["luma"])), 1), "too_dark": round(float(np.mean(s["low"])), 3),
                                 "clipped": round(float(np.mean(s["high"])), 3)},
                    "pan": round(pan, 2), "shake": round(shake, 2),
                    "motion": round(float(np.mean(s["resid"])) if s["resid"] else 0.0, 2)})
    return out, frames_for_clip, fps


class ClipTagger:
    def __init__(self, device, content_tags, shot_tags):
        import torch
        import open_clip
        self.torch, self.device = torch, device
        self.model, _, self.preprocess = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai", device=device)
        self.model.eval()
        tok = open_clip.get_tokenizer("ViT-B-32")
        with torch.no_grad():
            def enc(prompts):
                e = self.model.encode_text(tok([f"a photo of {p}" if not p.startswith("a ") else f"a photo: {p}" for p in prompts]).to(device))
                return e / e.norm(dim=-1, keepdim=True)
            self.c_names, self.s_names = list(content_tags), list(shot_tags)
            self.c_emb, self.s_emb = enc(list(content_tags.values())), enc(list(shot_tags.values()))

    def tag(self, frames, batch=32):
        import numpy as np
        from PIL import Image
        torch = self.torch
        secs, res = sorted(frames), {}
        for i in range(0, len(secs), batch):
            chunk = secs[i:i + batch]
            x = torch.stack([self.preprocess(Image.fromarray(frames[s])) for s in chunk]).to(self.device)
            with torch.no_grad():
                im = self.model.encode_image(x)
                im = im / im.norm(dim=-1, keepdim=True)
                c = (100 * im @ self.c_emb.T).softmax(dim=-1).cpu().numpy()
                sh = (100 * im @ self.s_emb.T).softmax(dim=-1).cpu().numpy()
            for k, s in enumerate(chunk):
                res[s] = {"tags": [[self.c_names[j], round(float(c[k, j]), 3)] for j in np.argsort(-c[k])[:3]],
                          "shot_size": [[self.s_names[j], round(float(sh[k, j]), 3)] for j in np.argsort(-sh[k])[:2]]}
        return res


class AudioTagger:
    SR = 32000

    def __init__(self, device):
        from panns_inference import SoundEventDetection, labels
        self.sed = SoundEventDetection(checkpoint_path=None, device=device)
        self.labels = labels

    def events(self, path, duration):
        import numpy as np
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(self.SR), "-f", "f32le", "-"],
                           capture_output=True)
        y = np.frombuffer(r.stdout, dtype=np.float32)
        if y.size < self.SR // 2:
            return [], []
        hop, chunk, fw = self.SR // 100, 60 * self.SR, []
        for a in range(0, y.size, chunk):
            seg = y[a:a + chunk]
            if seg.size < self.SR // 2:
                seg = np.pad(seg, (0, self.SR // 2 - seg.size))
            fw.append(self.sed.inference(seg[None, :])[0][: math.ceil(seg.size / hop)])
        fw = np.concatenate(fw)
        per_sec = []
        for i in range(max(1, math.ceil(duration))):
            w = fw[i * 100:(i + 1) * 100]
            if not len(w):
                per_sec.append([]); continue
            m = w.max(axis=0)
            per_sec.append([[self.labels[j], round(float(m[j]), 3)] for j in np.argsort(-m)[:3] if m[j] >= 0.15])
        ev, active = [], fw >= 0.3
        for j in np.where(active.any(axis=0))[0]:
            col = np.r_[False, active[:, j], False]
            for s, e in zip(np.where(~col[:-1] & col[1:])[0], np.where(col[:-1] & ~col[1:])[0]):
                if e - s >= 30:
                    ev.append({"label": self.labels[j], "start": round(s / 100, 2), "end": round(e / 100, 2), "max": round(float(fw[s:e, j].max()), 3)})
        ev.sort(key=lambda x: (x["start"], -x["max"]))
        return per_sec, ev


def summarize(per_sec, scenes, audio_events):
    import numpy as np

    def mean(key, sub=None):
        v = [(p[key][sub] if sub else p[key]) for p in per_sec if key in p]
        return round(float(np.mean(v)), 3) if v else None
    tags, shots, labels = {}, {}, {}
    for p in per_sec:
        for name, _ in p.get("tags", [])[:1]:
            tags[name] = tags.get(name, 0) + 1
        for name, _ in p.get("shot_size", [])[:1]:
            shots[name] = shots.get(name, 0) + 1
    for e in audio_events:
        labels[e["label"]] = labels.get(e["label"], 0) + (e["end"] - e["start"])
    top = lambda d, n=5: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
    return {"scenes": len(scenes), "sharpness_mean": mean("sharpness"), "exposure_mean": mean("exposure", "mean"),
            "shake_mean": mean("shake"), "motion_mean": mean("motion"), "tags": top(tags), "shot_sizes": top(shots),
            "audio": top(labels, 6)}


def run(clips_path, out, only=None, force=False, device="auto", scenes=True, clip_tags=None, audio_tags=None,
        scene_threshold=27.0, content_tags=None):
    """Analyze every clip. clip_tags/audio_tags: None = use them if torch & co. are installed."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        raise CutterError("OpenCV is missing (it is in the Docker image)", MISSING)
    clips = read_json(clips_path)
    if only:
        want = {x.strip().upper() for x in only.split(",")}
        clips = [c for c in clips if c["id"] in want]
    out = pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def available(mod):
        try:
            __import__(mod); return True
        except ImportError:
            return False
    if device == "auto":
        device = "cpu"
        if available("torch"):
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
    if scenes and not available("scenedetect"):
        log("PySceneDetect is missing: no scene detection")
        scenes = False
    use_clip = available("torch") and available("open_clip") if clip_tags is None else clip_tags
    use_audio = available("torch") and available("panns_inference") if audio_tags is None else audio_tags
    clip_tagger = ClipTagger(device, content_tags or CONTENT_TAGS, SHOT_TAGS) if use_clip else None
    audio_tagger = AudioTagger(device) if use_audio else None
    index_path = out / "_index.json"
    index = read_json(index_path) if index_path.exists() else {}
    done = 0
    for n, c in enumerate(clips, 1):
        dst = out / f'{c["id"]}.json'
        if dst.exists() and not force:
            log(f'[{n}/{len(clips)}] {c["id"]} exists, skipped')
            continue
        if not c.get("has_video", True):
            continue
        t0 = time.time()
        path = pathlib.Path(c["path"])
        res = {"id": c["id"], "file": c.get("rel_path"), "duration": c["duration"], "fps": c.get("fps"),
               "resolution": f'{c.get("width")}x{c.get("height")}', "units": UNITS}
        res["scenes"] = detect_scenes(path, scene_threshold) if scenes else []
        per_sec, frames, _ = video_metrics(path, c["duration"], keep_clip_frames=clip_tagger is not None)
        if clip_tagger and frames:
            tags = clip_tagger.tag(frames)
            for p in per_sec:
                p.update(tags.get(p["t"], {}))
        audio_events = []
        if audio_tagger and c.get("has_audio"):
            a_sec, audio_events = audio_tagger.events(path, c["duration"])
            for p, ev in zip(per_sec, a_sec):
                p["audio"] = ev
        res["per_second"] = per_sec
        res["audio_events"] = audio_events
        res["summary"] = summarize(per_sec, res["scenes"], audio_events)
        dst.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        index[c["id"]] = res["summary"]
        dump_json(index_path, index, ensure_ascii=False, indent=1)
        done += 1
        log(f'[{n}/{len(clips)}] {c["id"]} {c["duration"]:.0f} s in {time.time() - t0:.0f} s')
    return {"output": str(out), "analyzed": done, "clip_tags": bool(clip_tagger), "audio_tags": bool(audio_tagger)}
