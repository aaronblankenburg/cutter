"""What is in the picture: CLIP ViT-B/32 compares one frame per second with a list of short descriptions (desert,
road, sunset, aerial shot …) and with a list of shot types (close-up, wide, selfie …).

The model runs with onnxruntime (no torch): the quantized image and text encoders of Xenova/clip-vit-base-patch32
(MIT, from OpenAI's CLIP), 155 MB, downloaded once into the model folder with the other analysis models and checked
against their checksums (image_analysis.MODELS "tags_*").

It is a pass of its own after the image analysis: it reads the clip (or its proxy) once more at one frame per second,
224 px, and writes into the clip's analysis/image/<ID>.json

    per_second[i]["tags"]       [[tag, p], …]   the three most likely descriptions (p within the list)
    per_second[i]["shot_size"]  [[name, p], …]  the two most likely shot types
    summary["tags"], summary["shot_sizes"]      the most frequent first ones
    "tags_key"                                  model and lists the tags were made with

Clips whose tags_key matches are skipped, so changing the list in cutter.toml ([image_analysis] content_tags =
{name = "description", …}) tags everything again; adding the part to an analysed project only adds the tags.
ranges() turns the seconds into time ranges per tag, for the material view and quality.txt.
"""
import hashlib
import json
import os
import pathlib
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .util import log as progress, read_json

SIZE = 224
MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
SCALE = 100.0                    # CLIP's logit scale
MIN_P = 0.3                      # a description counts for a second from this share on (ranges, summary)
MODEL_NAMES = ("tags_image", "tags_text", "tags_tokenizer")

CONTENT_TAGS = {
    "landscape": "a wide landscape", "road": "an asphalt road", "gravel road": "a gravel road",
    "street": "a town street with buildings", "village": "a small village", "city": "a big city",
    "beach": "a sandy beach", "sea": "the sea with waves", "river": "a river", "lake": "a lake",
    "forest": "a forest", "mountains": "mountains", "desert": "sand dunes in the desert",
    "savanna": "dry savanna with grass and scattered trees", "snow": "snow and ice",
    "sky": "mostly sky", "sunset": "a sunset sky with the low sun", "night": "a very dark night scene",
    "aerial": "an aerial drone shot from high above", "indoor": "a room interior", "vehicle": "a car, truck or bus",
    "motorbike": "a motorcycle", "bicycle": "a bicycle", "boat": "a boat on the water",
    "wildlife": "wild animals", "camp": "a tent at a campsite", "campfire": "a campfire",
    "market": "a market with stalls", "group": "a group of people", "hands": "hands holding something",
    "food": "food on a table",
}
SHOT_TAGS = {
    "close-up hands": "a close-up shot of hands doing something", "detail": "an extreme close-up of an object",
    "face": "a close-up shot of a person's face", "medium": "a medium shot of a person from the waist up",
    "full": "a full shot of a person from head to toe", "wide": "a wide shot of a small person in a large landscape",
    "empty landscape": "an empty landscape without people", "over the shoulder": "an over-the-shoulder shot",
    "selfie": "a selfie of a person holding the camera at arm's length",
    "low angle": "a low angle shot from the ground looking up", "high angle": "a high angle shot looking down",
    "ground camera": "a camera lying flat on the ground", "silhouette": "a dark silhouette against a bright sky",
}


def prompt(text):
    text = str(text).strip()
    return text if text.lower().startswith("a photo") else f"a photo of {text}"


def key(content_tags=None):
    """What the tags of a clip were made with: the model and both lists."""
    from .image_analysis import MODELS
    raw = json.dumps([[MODELS[n][2] for n in MODEL_NAMES], content_tags or CONTENT_TAGS, SHOT_TAGS], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def model_files(download=True):
    """{name: path} of the three files, or None when one is missing (and cannot be downloaded)."""
    from .image_analysis import model_file
    files = {}
    for name in MODEL_NAMES:
        path = model_file(name, download=download)
        if not path:
            return None
        files[name] = path
    return files


class Tagger:
    def __init__(self, files, content_tags=None, threads=None):
        import numpy as np
        import onnxruntime
        from tokenizers import Tokenizer
        self.np = np
        opt = onnxruntime.SessionOptions()
        opt.intra_op_num_threads = threads or max(1, min(8, os.cpu_count() or 2))
        opt.log_severity_level = 3
        self.image = onnxruntime.InferenceSession(str(files["tags_image"]), opt, providers=["CPUExecutionProvider"])
        text = onnxruntime.InferenceSession(str(files["tags_text"]), opt, providers=["CPUExecutionProvider"])
        tok = Tokenizer.from_file(str(files["tags_tokenizer"]))

        def enc(descriptions):
            rows = []
            for d in descriptions:                    # one at a time: no padding, the end token is the last one
                ids = np.array([tok.encode(prompt(d)).ids[:77]], dtype=np.int64)
                rows.append(text.run(None, {"input_ids": ids})[0][0])
            e = np.array(rows, dtype=np.float32)
            return e / np.linalg.norm(e, axis=1, keepdims=True)
        tags = content_tags or CONTENT_TAGS
        self.c_names, self.s_names = list(tags), list(SHOT_TAGS)
        self.c_emb, self.s_emb = enc(tags.values()), enc(SHOT_TAGS.values())

    def tag(self, frames, batch=16):
        """frames: uint8 array (n, 224, 224, 3) RGB -> one {"tags", "shot_size"} per frame."""
        np = self.np
        out = []
        mean, std = np.array(MEAN, dtype=np.float32), np.array(STD, dtype=np.float32)
        for i in range(0, len(frames), batch):
            x = ((frames[i:i + batch].astype(np.float32) / 255.0 - mean) / std).transpose(0, 3, 1, 2)
            im = self.image.run(None, {"pixel_values": np.ascontiguousarray(x)})[0]
            im = im / np.linalg.norm(im, axis=1, keepdims=True)
            for row in im:
                c, s = _softmax(SCALE * self.c_emb @ row), _softmax(SCALE * self.s_emb @ row)
                out.append({"tags": [[self.c_names[j], round(float(c[j]), 3)] for j in np.argsort(-c)[:3]],
                            "shot_size": [[self.s_names[j], round(float(s[j]), 3)] for j in np.argsort(-s)[:2]]})
        return out


def _softmax(v):
    import numpy as np
    e = np.exp(v - v.max())
    return e / e.sum()


def frames(path, duration):
    """One frame per second (the middle of it), scaled to fill 224x224 and cut to it: uint8 (n, 224, 224, 3)."""
    import numpy as np
    n = max(1, int(-(-float(duration or 0) // 1)))
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "0.5" if (duration or 0) > 1 else "0", "-i", str(path),
                        "-an", "-sn", "-vf", f"fps=1,scale={SIZE}:{SIZE}:force_original_aspect_ratio=increase:flags=bicubic,"
                        f"crop={SIZE}:{SIZE}", "-frames:v", str(n), "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode(errors="replace").strip()[-300:] or "ffmpeg failed")
    k = len(r.stdout) // (SIZE * SIZE * 3)
    return np.frombuffer(r.stdout[:k * SIZE * SIZE * 3], dtype=np.uint8).reshape(k, SIZE, SIZE, 3)


def first(p, field="tags", min_p=MIN_P):
    """The most likely description of a second when it is likely enough, else None."""
    v = p.get(field) or []
    return v[0][0] if v and v[0][1] >= min_p else None


def summary(per_sec, n=5):
    """The most frequent first descriptions and shot types."""
    tags, shots = {}, {}
    for p in per_sec:
        t, s = first(p), first(p, "shot_size")
        if t:
            tags[t] = tags.get(t, 0) + 1
        if s:
            shots[s] = shots.get(s, 0) + 1
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
    return top(tags), top(shots)


def ranges(per_sec, duration=None, field="tags", min_len=2.0):
    """{tag: [{"start", "end"}]}: where a description is the most likely one, gaps of a second closed."""
    from . import quality
    times = [p["t"] for p in per_sec]
    names = {first(p, field) for p in per_sec} - {None}
    out = {}
    for name in sorted(names):
        rs = quality.ranges(times, [first(p, field) == name for p in per_sec], min_len=min_len, close_gap=1.0,
                            end=duration)
        if rs:
            out[name] = rs
    return out


def run(clips, out, content_tags=None, proxies=None, only=None, force=False, log=progress, files=None):
    """Tag the analysed clips that have no tags from this model and these lists yet. Returns
    {"tagged", "skipped", "available"}; without the model files nothing changes."""
    out = pathlib.Path(out)
    files = files or model_files()
    if not files:
        if log:
            log("Content tags: the CLIP model could not be loaded; no tags")
        return {"tagged": 0, "skipped": 0, "available": False}
    want = {x.strip().upper() for x in only.split(",")} if only else None
    k = key(content_tags)
    todo = []
    for c in clips:
        if want and c["id"] not in want or not c.get("has_video", True):
            continue
        dst = out / f'{c["id"]}.json'
        try:
            res = read_json(dst)
        except (OSError, ValueError):
            continue
        if force or res.get("tags_key") != k:
            todo.append((c, dst, res))
    if not todo:
        return {"tagged": 0, "skipped": 0, "available": True}
    tagger = Tagger(files, content_tags)
    index_path = out / "_index.json"
    index = read_json(index_path) if index_path.exists() else {}
    if log:
        log(f"\nContent tags (CLIP) for {len(todo)} clips …")

    def load(item):
        c, dst, res = item
        src = (proxies or {}).get(c["id"], {}).get("path") or c["path"]
        try:
            return item, frames(src, c["duration"]), None
        except Exception as e:                       # noqa: BLE001 – one unreadable clip must not stop the rest
            return item, None, str(e)
    done = skipped = 0
    with ThreadPoolExecutor(2) as pool:              # the next clip is decoded while this one is tagged
        for n, ((c, dst, res), pics, error) in enumerate(pool.map(load, todo), 1):
            if pics is None or not len(pics):
                skipped += 1
                if log:
                    log(f'  [{n}/{len(todo)}] {c["id"]}: ! {error or "no frames"}')
                continue
            tags = tagger.tag(pics)
            per = res.get("per_second") or []
            for i, p in enumerate(per):
                p.pop("tags", None)
                p.pop("shot_size", None)
                if i < len(tags):
                    p.update(tags[i])
            s = res.setdefault("summary", {})
            s["tags"], s["shot_sizes"] = summary(per)
            res["tags_key"] = k
            tmp = dst.with_name(dst.name + ".part")
            tmp.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, dst)
            if c["id"] in index:
                index[c["id"]]["tags"], index[c["id"]]["shot_sizes"] = s["tags"], s["shot_sizes"]
            done += 1
            if log and (n % 10 == 0 or n == len(todo)):
                log(f"  [{n}/{len(todo)}] tagged")
    if done and index:
        from .util import dump_json
        dump_json(index_path, index, ensure_ascii=False, indent=1)
    return {"tagged": done, "skipped": skipped, "available": True}
