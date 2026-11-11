"""Image and sound analysis per clip (part of cutter analyze; --no-image skips it).

Writes one file <out>/<ID>.json per clip of clips.json with
  scenes      scene boundaries (PySceneDetect, ContentDetector)
  per_second  per second: sharpness, exposure, colour, shake, camera pan and zoom, motion (OpenCV), faces, people,
              objects and the shot size they imply (YuNet, YOLOX), sound level and wind; optionally CLIP tags and
              audio events (PANNs) when torch, open_clip and panns_inference are installed
  camera      the camera moves as segments (static, handheld, shaky, pan, tilt, push in, pull out), to 0.2 s
  usable      the ranges worth cutting from; flaws: the rest with the reason (soft, shaky, whip, dark, clipped)
  audio       loudness, peak, and the ranges of speech, silence, wind and clipping
  telemetry   the place and, from action cameras and drones, the GPS track and the gyroscope
  faces       the people of the clip by their faces (SFace), a picture of each in <out>/faces/; people.py finds the
              same person across clips -> <analysis>/people.json
  summary     the figures of the clip, also in <out>/_index.json; similar: clips that look like another take
and <analysis>/quality.txt, the same for reading: a few lines per clip. The retime step uses sharpness, shake and
exposure to avoid flawed seconds. CPU is enough. The three models (57 MB) are downloaded once into the model volume,
checksum verified; without them faces, people, objects and who is who are left out.
The tag list for CLIP comes from cutter.toml → image_analysis.content_tags (neutral defaults below).
"""
import hashlib
import json
import math
import os
import pathlib
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import quality
from .util import CutterError, MISSING, dump_json, log, lufs, read_json

VERSION = 3             # of the per-clip file; older files are measured again

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
DETECT_W = 640          # working width for faces and objects (one frame per second)
SIGNATURE = 24          # frames per clip that are compared to find other takes
WORK_W = 480            # working width for sharpness/exposure
FLOW_W = 320            # working width for optical flow
UNITS = {"sharpness": "variance of the Laplacian at 480 px width (higher = sharper)",
         "exposure": "luma 0-255; share below 10 (too_dark) and above 245 (clipped)",
         "pan": "global camera motion, % of picture width per second; pan_x/pan_y: how the picture moves (x > 0 to "
                "the right = a pan to the left, y > 0 down = a tilt up)",
         "zoom": "growth of the picture, % per second (a zoom, or the camera moving forward; negative: out/back)",
         "color": "warmth (red-blue)/(red+blue), about -0.3 cool to 0.3 warm; saturation 0-1",
         "faces/face": "number of faces and the height of the largest as a share of the picture height (YuNet)",
         "people/person/objects": "number of people, height of the largest, other objects by name (YOLOX-tiny, COCO)",
         "shot": "shot size from the largest face, else the largest person",
         "horizon": "tilt of the longest near-horizontal line in degrees (positive: it falls to the right)",
         "level/wind": "sound level of the second in dBFS; wind 0-1 (energy below 150 Hz that the channels do not share; "
                       "with one microphone only the energy counts: wind or an engine, reported as rumble)",
         "camera/usable/flaws": "seconds of the clip; speeds like pan, shake like shake but from five samples a second",
         "shake": "unsteadiness of the global motion within the second, % of picture width per second",
         "motion": "motion in the picture without camera motion, % of picture width per second",
         "tags/shot_size": "CLIP ViT-B/32, probability within the tag list",
         "audio": "PANNs Cnn14 (SED), highest probability within the second"}

MODELS = {  # name: (file, MB, sha256, url) – all under permissive licenses (MIT, Apache-2.0, Apache-2.0)
    "faces": ("face_detection_yunet_2023mar.onnx", 0.3, "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
              "https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/"
              "face_detection_yunet/face_detection_yunet_2023mar.onnx"),
    "objects": ("yolox_tiny.onnx", 20, "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7",
                "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx"),
    "recognition": ("face_recognition_sface_2021dec.onnx", 37, "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
                    "https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/"
                    "face_recognition_sface/face_recognition_sface_2021dec.onnx"),
}
COCO = ("person,bicycle,car,motorcycle,airplane,bus,train,truck,boat,traffic light,fire hydrant,stop sign,parking meter,"
        "bench,bird,cat,dog,horse,sheep,cow,elephant,bear,zebra,giraffe,backpack,umbrella,handbag,tie,suitcase,frisbee,"
        "skis,snowboard,sports ball,kite,baseball bat,baseball glove,skateboard,surfboard,tennis racket,bottle,wine glass,"
        "cup,fork,knife,spoon,bowl,banana,apple,sandwich,orange,broccoli,carrot,hot dog,pizza,donut,cake,chair,couch,"
        "potted plant,bed,dining table,toilet,tv,laptop,mouse,remote,keyboard,cell phone,microwave,oven,toaster,sink,"
        "refrigerator,book,clock,vase,scissors,teddy bear,hair drier,toothbrush").split(",")
FACE_PX, FACE_SCORE = 48, 0.85     # a face smaller or less certain than this is not compared with others
SHOTS = ((0.35, "close-up"), (0.15, "medium close-up"), (0.07, "medium"))     # by the height of the largest face
PEOPLE_SHOTS = ((0.6, "full"), (0.25, "wide"), (0.0, "very wide"))             # by the height of the largest person


def models_dir():
    return pathlib.Path(os.environ.get("CUTTER_VISION_MODELS") or
                        pathlib.Path(os.environ.get("HF_HOME") or pathlib.Path.home() / ".cache" / "cutter") / "cutter-vision")


def model_file(name, download=True):
    """The path of a detection model; downloaded once (checksum verified). None when it is not there."""
    file, mb, sha, url = MODELS[name]
    path = models_dir() / file
    if path.is_file():
        return path
    if not download or os.environ.get("CUTTER_TEST") or os.environ.get("HF_HUB_OFFLINE"):
        return None
    log(f"Downloading the {name} model ({mb:g} MB) …")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "cutter"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != sha:
            log(f"  ! {file}: the checksum does not match, not used")
            return None
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError as e:
        log(f"  ! {file} could not be downloaded ({e}); faces, people and objects are left out")
        return None
    return path


class Detector:
    """Faces (YuNet), who they are (SFace) and objects (YOLOX-tiny) on one frame; each may be missing."""

    def __init__(self, faces=None, objects=None, recognition=None, threads=4):
        import cv2
        self.cv2, self.faces, self.objects, self.size, self.recognition = cv2, None, None, None, None
        if faces:
            self.faces = cv2.FaceDetectorYN.create(str(faces), "", (DETECT_W, DETECT_W), 0.7, 0.3, 50)
            if recognition:
                self.recognition = cv2.FaceRecognizerSF.create(str(recognition), "")
        if objects:
            import onnxruntime
            opt = onnxruntime.SessionOptions()
            opt.intra_op_num_threads = threads
            opt.log_severity_level = 3
            self.objects = onnxruntime.InferenceSession(str(objects), opt, providers=["CPUExecutionProvider"])
            self.input = self.objects.get_inputs()[0]
            self.side = int(self.input.shape[2])

    def __bool__(self):
        return bool(self.faces or self.objects)

    def detect(self, bgr, full=None):
        """{"faces": [height share …], "objects": [(label, height share) …], "found": [{"v", "size", "score",
        "crop"}]} for a BGR frame; found: the faces large enough to tell people apart, cut from the frame `full`
        in its original size."""
        import numpy as np
        cv2 = self.cv2
        h, w = bgr.shape[:2]
        out = {"faces": [], "objects": [], "found": []}
        if self.faces:
            if self.size != (w, h):
                self.faces.setInputSize((w, h))
                self.size = (w, h)
            found = self.faces.detect(bgr)[1]
            found = found if found is not None else []
            out["faces"] = sorted((round(float(f[3]) / h, 3) for f in found), reverse=True)
            if self.recognition and full is not None:
                up = full.shape[0] / h
                for f in found:
                    if f[3] * up < FACE_PX or f[14] < FACE_SCORE:
                        continue
                    row = f.copy()
                    row[:14] *= up
                    crop = self.recognition.alignCrop(full, row)
                    v = self.recognition.feature(crop).ravel().astype(np.float32)
                    out["found"].append({"v": v / (float(np.linalg.norm(v)) + 1e-9), "size": round(float(f[3]) / h, 3),
                                         "score": float(f[14]), "crop": crop.copy()})
        if self.objects:
            side = self.side
            r = min(side / h, side / w)
            canvas = np.full((side, side, 3), 114, dtype=np.uint8)
            small = cv2.resize(bgr, (max(1, round(w * r)), max(1, round(h * r))), interpolation=cv2.INTER_LINEAR)
            canvas[:small.shape[0], :small.shape[1]] = small
            pred = self.objects.run(None, {self.input.name: canvas.transpose(2, 0, 1)[None].astype(np.float32)})[0][0]
            grids, strides = [], []
            for stride in (8, 16, 32):
                n = side // stride
                yy, xx = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
                grids.append(np.stack((xx, yy), 2).reshape(-1, 2))
                strides.append(np.full((n * n, 1), stride))
            grids, strides = np.concatenate(grids), np.concatenate(strides)
            xy, wh = (pred[:, :2] + grids) * strides / r, np.exp(pred[:, 2:4]) * strides / r
            scores = pred[:, 4:5] * pred[:, 5:]
            cls = scores.argmax(1)
            conf = scores[np.arange(len(cls)), cls]
            keep = conf >= 0.45
            boxes = np.concatenate((xy - wh / 2, wh), 1)[keep]
            cls, conf = cls[keep], conf[keep]
            for c in set(cls.tolist()):
                idx = np.where(cls == c)[0]
                for k in np.array(cv2.dnn.NMSBoxes(boxes[idx].tolist(), conf[idx].tolist(), 0.45, 0.45)).reshape(-1):
                    out["objects"].append((COCO[c], round(min(1.0, float(boxes[idx][k][3]) / h), 3)))
        return out


def shot_size(faces, people):
    if faces:
        for limit, name in SHOTS:
            if faces[0] >= limit:
                return name
    if people:
        for limit, name in PEOPLE_SHOTS:
            if people[0] >= limit:
                return name
    return None


def horizon(gray):
    """Tilt in degrees of the longest near-horizontal line across the picture, or None."""
    import cv2
    import numpy as np
    h, w = gray.shape
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, 80, minLineLength=int(0.45 * w), maxLineGap=12)
    best, length = None, 0.0
    for x1, y1, x2, y2 in (np.array(lines).reshape(-1, 4) if lines is not None else []):
        if x2 < x1:
            x1, y1, x2, y2 = x2, y2, x1, y1
        ang = math.degrees(math.atan2(float(y2 - y1), float(x2 - x1)))
        ln = math.hypot(float(x2 - x1), float(y2 - y1))
        if abs(ang) <= 20 and ln > length:
            best, length = ang, ln
    return None if best is None else round(best, 1)


def detect_scenes(path, threshold):
    from scenedetect import detect, ContentDetector
    scenes = detect(str(path), ContentDetector(threshold=threshold, min_scene_len=12), show_progress=False)
    return [{"start": round(a.get_seconds(), 3), "end": round(b.get_seconds(), 3)} for a, b in scenes]


def video_metrics(path, duration, keep_clip_frames, detector=None):
    """Measures per second, the samples behind them (five a second), what the detector saw in the middle of every
    second, a signature of the clip, one frame per second for CLIP (RGB, middle of the second), and the faces that
    can be told apart."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    step = max(1, round(fps / SAMPLE_FPS))
    n_sec = max(1, math.ceil(duration))
    secs = [dict(sharp=[], luma=[], low=[], high=[], gm=[], resid=[], zoom=[], warm=[], sat=[]) for _ in range(n_sec)]
    frames_for_clip, samples, seen, signature, found = {}, [], {}, [], []
    prev_small, idx, grid = None, 0, None
    while True:
        ok = cap.grab()
        if not ok:
            break
        t = idx / fps
        sec = int(t)
        middle = sec < n_sec and sec not in seen and (t - sec) >= 0.5
        if idx % step == 0 or middle:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            if middle:
                seen[sec] = {}
                if keep_clip_frames:
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    frames_for_clip[sec] = cv2.resize(rgb, (336, round(336 * h / w)), interpolation=cv2.INTER_AREA)
                view = cv2.resize(frame, (DETECT_W, round(DETECT_W * h / w)), interpolation=cv2.INTER_AREA)
                view_gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
                if detector:
                    seen[sec] = detector.detect(view, frame)
                    found += [dict(f, t=sec) for f in seen[sec].pop("found")]
                seen[sec]["horizon"] = horizon(view_gray)
                signature.append(np.concatenate((cv2.resize(view_gray, (12, 8), interpolation=cv2.INTER_AREA).ravel(),
                                                 cv2.resize(view, (4, 3), interpolation=cv2.INTER_AREA).ravel())))
            if idx % step == 0 and sec < n_sec:
                work = cv2.resize(frame, (WORK_W, round(WORK_W * h / w)), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
                s = secs[sec]
                sample = {"t": round(t, 3), "sharp": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
                          "low": float((gray < 10).mean()), "high": float((gray > 245).mean()), "gx": None, "gy": None,
                          "zoom": None}
                s["sharp"].append(sample["sharp"])
                s["luma"].append(float(gray.mean()))
                s["low"].append(sample["low"])
                s["high"].append(sample["high"])
                blue, _, red = (float(v) for v in work[::4, ::4].reshape(-1, 3).mean(axis=0))
                s["warm"].append((red - blue) / (red + blue) if red + blue > 0 else 0.0)
                s["sat"].append(float(cv2.cvtColor(work[::4, ::4], cv2.COLOR_BGR2HSV)[..., 1].mean()) / 255)
                small = cv2.resize(gray, (FLOW_W, round(FLOW_W * h / w)), interpolation=cv2.INTER_AREA)
                if prev_small is not None and prev_small.shape == small.shape:
                    flow = cv2.calcOpticalFlowFarneback(prev_small, small, None, 0.5, 3, 15, 3, 5, 1.2, 0)
                    # camera motion (global): the median flow where the picture has texture; flat areas such as sky
                    # show no flow whatever the camera does
                    edge = np.abs(cv2.Sobel(prev_small, cv2.CV_32F, 1, 0)) + np.abs(cv2.Sobel(prev_small, cv2.CV_32F, 0, 1))
                    textured = edge >= max(40.0, float(np.percentile(edge, 60)))
                    if int(textured.sum()) < 200:
                        textured = np.ones_like(textured)
                    g = np.median(flow[textured], axis=0)
                    # the camera moves everything alike; when the textured parts disagree, it is the subject that moves
                    if float((np.linalg.norm(flow[textured] - g, axis=1) <= 0.5 + 0.3 * float(np.linalg.norm(g))).mean()) < 0.5:
                        g = np.median(flow.reshape(-1, 2), axis=0)
                    resid = np.linalg.norm(flow - g, axis=2)               # motion in the picture
                    dt = step / fps
                    if grid is None or grid[0].shape != small.shape:       # zoom: how the flow grows away from the centre
                        yy, xx = np.mgrid[0:small.shape[0], 0:small.shape[1]].astype(np.float32)
                        grid = (xx - small.shape[1] / 2, yy - small.shape[0] / 2)
                    fx, fy, px, py = flow[..., 0][textured] - g[0], flow[..., 1][textured] - g[1], grid[0][textured], grid[1][textured]
                    zoom = float((fx * px).mean() + (fy * py).mean()) / (float((px ** 2).mean() + (py ** 2).mean()) + 1e-6)
                    gm = (g / FLOW_W * 100 / dt).tolist()
                    sample.update(gx=gm[0], gy=gm[1], zoom=zoom * 100 / dt)
                    s["gm"].append(gm)
                    s["zoom"].append(sample["zoom"])
                    s["resid"].append(float(np.median(resid) / FLOW_W * 100 / dt + resid.mean() / FLOW_W * 100 / dt) / 2)
                prev_small = small
                samples.append(sample)
        idx += 1
    cap.release()
    out = []
    for i, s in enumerate(secs):
        if not s["sharp"]:
            out.append({"t": i})
            continue
        gm = np.array(s["gm"]) if s["gm"] else np.zeros((1, 2))
        mean = gm.mean(axis=0)
        shake = float(np.mean(np.linalg.norm(gm - mean, axis=1))) if len(gm) > 1 else 0.0
        p = {"t": i, "sharpness": round(float(np.median(s["sharp"])), 1),
             "exposure": {"mean": round(float(np.mean(s["luma"])), 1), "too_dark": round(float(np.mean(s["low"])), 3),
                          "clipped": round(float(np.mean(s["high"])), 3)},
             "color": {"warmth": round(float(np.mean(s["warm"])), 3), "saturation": round(float(np.mean(s["sat"])), 3)},
             "pan": round(float(np.linalg.norm(mean)), 2), "pan_x": round(float(mean[0]), 2), "pan_y": round(float(mean[1]), 2),
             "zoom": round(float(np.mean(s["zoom"])) if s["zoom"] else 0.0, 2), "shake": round(shake, 2),
             "motion": round(float(np.mean(s["resid"])) if s["resid"] else 0.0, 2)}
        d = seen.get(i) or {}
        if d.get("horizon") is not None:
            p["horizon"] = d["horizon"]
        if "faces" in d:
            people = sorted((size for name, size in d["objects"] if name == "person"), reverse=True)
            things = {}
            for name, _ in d["objects"]:
                if name != "person":
                    things[name] = things.get(name, 0) + 1
            p.update(faces=len(d["faces"]), people=len(people))
            if d["faces"]:
                p["face"] = d["faces"][0]
            if people:
                p["person"] = people[0]
            if things:
                p["objects"] = things
            shot = shot_size(d["faces"], people)
            if shot:
                p["shot"] = shot
        out.append(p)
    if len(signature) > SIGNATURE:
        signature = [signature[round(k * (len(signature) - 1) / (SIGNATURE - 1))] for k in range(SIGNATURE)]
    return out, frames_for_clip, fps, samples, [v.tolist() for v in signature], found


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


def summarize(per_sec, scenes, audio_events, res=None):
    import numpy as np
    res = res or {}

    def mean(key, sub=None):
        v = [(p[key][sub] if sub else p[key]) for p in per_sec if key in p]
        return round(float(np.mean(v)), 3) if v else None
    tags, shots, labels, things, sizes = {}, {}, {}, {}, {}
    for p in per_sec:
        for name, _ in p.get("tags", [])[:1]:
            tags[name] = tags.get(name, 0) + 1
        for name, _ in p.get("shot_size", [])[:1]:
            shots[name] = shots.get(name, 0) + 1
        for name in p.get("objects", {}):
            things[name] = things.get(name, 0) + 1
        if p.get("shot"):
            sizes[p["shot"]] = sizes.get(p["shot"], 0) + 1
    for e in audio_events:
        labels[e["label"]] = labels.get(e["label"], 0) + (e["end"] - e["start"])
    top = lambda d, n=5: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:n]]
    out = {"scenes": len(scenes), "sharpness_mean": mean("sharpness"), "exposure_mean": mean("exposure", "mean"),
           "shake_mean": mean("shake"), "motion_mean": mean("motion"), "tags": top(tags), "shot_sizes": top(shots),
           "audio": top(labels, 6)}
    if "usable" in res:
        moves = {}
        for c in res.get("camera", []):
            moves[c["move"]] = round(moves.get(c["move"], 0) + c["end"] - c["start"], 1)
        out.update(usable_s=quality.total(res["usable"]), steady_s=quality.total([u for u in res["usable"] if u["steady"]]),
                   camera=dict(sorted(moves.items(), key=lambda kv: -kv[1])),
                   too_dark=mean("exposure", "too_dark"), clipped=mean("exposure", "clipped"),
                   warmth=mean("color", "warmth"), saturation=mean("color", "saturation"))
    if any("faces" in p for p in per_sec):
        seconds = max(1, sum(1 for p in per_sec if "faces" in p))
        out.update(faces_max=max(p.get("faces", 0) for p in per_sec), people_max=max(p.get("people", 0) for p in per_sec),
                   people_share=round(sum(1 for p in per_sec if p.get("people") or p.get("faces")) / seconds, 2),
                   shots=top(sizes, 3), objects=top({k: v for k, v in things.items() if v >= 2 or seconds < 4}, 6))
    tilt = [p["horizon"] for p in per_sec if "horizon" in p]
    if len(tilt) >= max(3, 0.5 * len(per_sec)):       # a line that stays: most likely the horizon
        q1, q3 = quality.percentile(tilt, 25), quality.percentile(tilt, 75)
        if q3 - q1 <= 2.0:
            out["horizon_tilt"] = round(quality.percentile(tilt, 50), 1)
    au = res.get("audio") or {}
    if au:
        out.update({k: au[k] for k in ("lufs", "peak_db") if au.get(k) is not None})
        out.update({f"{k}_s": quality.total(au[k]) for k in ("speech", "wind", "rumble", "clipping", "silence") if k in au})
    tele = res.get("telemetry") or {}
    out.update({k: tele[k] for k in ("gps", "distance_m", "speed_kmh") if k in tele})
    return out


def find_takes(clips, out, minutes=30, threshold=0.93):
    """Clips that look like another take of the same shot: recorded within `minutes` of each other with frames that
    match. Writes summary.similar into the files and the index; returns the groups."""
    import numpy as np
    from datetime import datetime
    sig, when = {}, {}
    for c in clips:
        f = out / f'{c["id"]}.json'
        if not f.exists():
            continue
        v = np.array(read_json(f).get("signature") or [], dtype=np.float32)
        if v.ndim != 2 or len(v) < 1:
            continue
        gray, col = v[:, :96], v[:, 96:]
        gray = gray - gray.mean(axis=1, keepdims=True)
        col = col - col.mean(axis=1, keepdims=True)
        gray /= np.linalg.norm(gray, axis=1, keepdims=True) + 1e-6
        col /= np.linalg.norm(col, axis=1, keepdims=True) + 1e-6
        v = np.concatenate((gray, 0.7 * col), axis=1)
        sig[c["id"]] = v / np.linalg.norm(v, axis=1, keepdims=True)
        try:
            when[c["id"]] = datetime.fromisoformat(c["created"]).timestamp()
        except (KeyError, ValueError):
            when[c["id"]] = None
    ids = sorted(sig, key=lambda i: (when[i] is None, when[i] or 0))
    parent = {i: i for i in ids}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for n, a in enumerate(ids):
        for b in ids[n + 1:]:
            if when[a] is None or when[b] is None:
                continue
            if when[b] - when[a] > minutes * 60:
                break
            m = sig[a] @ sig[b].T
            if min(float(np.median(m.max(axis=1))), float(np.median(m.max(axis=0)))) >= threshold:
                parent[root(b)] = root(a)
    groups = {}
    for i in ids:
        groups.setdefault(root(i), []).append(i)
    groups = [sorted(g) for g in groups.values() if len(g) > 1]
    similar = {i: [j for j in g if j != i] for g in groups for i in g}
    index_path = out / "_index.json"
    index = read_json(index_path) if index_path.exists() else {}
    for c in clips:
        f, cid = out / f'{c["id"]}.json', c["id"]
        if cid not in index or not f.exists() or index[cid].get("similar", []) == similar.get(cid, []):
            continue
        data = read_json(f)
        for target in (data["summary"], index[cid]):
            target.pop("similar", None)
            if cid in similar:
                target["similar"] = similar[cid]
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    dump_json(index_path, index, ensure_ascii=False, indent=1)
    return groups


def flag_low_detail(clips, out):
    """Sharpness is judged within a clip, so a clip that is soft from start to end goes unnoticed there. Compared
    with the other clips of the same size it stands out: summary.low_detail (it may also be fog, sky or water)."""
    index_path = out / "_index.json"
    index = read_json(index_path) if index_path.exists() else {}
    by_size = {}
    for c in clips:
        s = index.get(c["id"], {}).get("sharpness_mean")
        if s is not None:
            by_size.setdefault((c.get("width"), c.get("height")), []).append(s)
    for c in clips:
        e = index.get(c["id"])
        peers = by_size.get((c.get("width"), c.get("height")), [])
        if not e or e.get("sharpness_mean") is None:
            continue
        e.pop("low_detail", None)
        if len(peers) >= 5 and e["sharpness_mean"] < 0.3 * quality.percentile(peers, 50):
            e["low_detail"] = True
    dump_json(index_path, index, ensure_ascii=False, indent=1)


def _ts(sec):
    sec = max(0.0, float(sec))
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _spans(rs, label=None, limit=8):
    parts = [f'{_ts(r["start"])}-{_ts(r["end"])}' + (f" {label(r)}" if label else "") for r in rs[:limit]]
    return ", ".join(parts) + (f" (+{len(rs) - limit} more)" if len(rs) > limit else "")


def readable(clips, out, known=None):
    """A few lines per clip for reading: what is usable, how the camera moves, who and what is in the picture, the
    sound, the place and other takes. known: the people of people.json."""
    index = read_json(out / "_index.json") if (out / "_index.json").exists() else {}
    lines = ["Footage quality per clip (cutter analyze). Times are minutes:seconds of the clip; details and units: "
             "docs/formats.md, the numbers per second: " + f"{out.name}/<ID>.json", ""]
    if known:
        lines += ["People by their faces (pictures: people.jpg; cutter people name/merge to correct): "
                  + ", ".join(f'{p["id"]}{" " + p["name"] if p["name"] else ""} ({p["seconds"]} s in {len(p["clips"])} clips)'
                              for p in known), ""]
    for c in clips:
        f = out / f'{c["id"]}.json'
        if not f.exists():
            continue
        d = read_json(f)
        if "usable" not in d:
            continue
        s, per = dict(d["summary"], **index.get(c["id"], {})), d["per_second"]
        lines.append(f'=== {c["id"]} | {_ts(c["duration"])} | {c.get("created", "")[:16]} | {c.get("rel_path", "")} ===')
        note = " (little detail in the whole clip: out of focus, fog, sky or water?)" if s.get("low_detail") else ""
        lines.append(f'usable   {s["usable_s"]:g} s of {c["duration"]:g} s{note}'
                     + (": " + _spans(d["usable"], lambda r: r["camera"].replace("_", " ") + ("" if r["steady"] else ", unsteady"))
                        if d["usable"] else ""))
        flaws = [r for r in d["flaws"] if r["why"] != ["short"]]
        if flaws:
            lines.append("flaws    " + _spans(flaws, lambda r: "+".join(r["why"])))
        lines.append("camera   " + _spans(d["camera"], lambda r: r["move"].replace("_", " ")
                                          + (f' {r["speed"]:g} %/s' if r["move"] not in ("static", "handheld", "shaky") else "")))
        if len(d.get("scenes") or []) > 1:
            lines.append("cuts     at " + ", ".join(_ts(x["start"]) for x in d["scenes"][1:9]))
        pic = []
        if "faces_max" in s:
            if s["people_max"] or s["faces_max"]:
                sh = [p.get("shot") for p in per]
                runs = quality.ranges([p["t"] for p in per], [bool(x) for x in sh], min_len=1.0, close_gap=1.0, end=c["duration"])
                pic.append(f'people: up to {max(s["people_max"], s["faces_max"])}'
                           + (f' ({", ".join(s["shots"])})' if s["shots"] else "") + (f" at {_spans(runs, limit=4)}" if runs else ""))
            else:
                pic.append("no people")
            if s["objects"]:
                pic.append(", ".join(s["objects"]))
        if s.get("too_dark", 0) > 0.3:
            pic.append("dark")
        if s.get("clipped", 0) > 0.15:
            pic.append("blown highlights")
        if s.get("warmth") is not None:
            pic.append(f'warmth {s["warmth"]:+.2f}')
        if "horizon_tilt" in s and abs(s["horizon_tilt"]) >= 1.5:
            pic.append(f'horizon tilted {s["horizon_tilt"]:+g}°')
        if pic:
            lines.append("picture  " + " | ".join(pic))
        who = [f'{p["id"]}{" " + p["name"] if p["name"] else ""} {_spans(p["clips"][c["id"]], limit=4)}'
               for p in known or [] if p["clips"].get(c["id"])]
        if who:
            lines.append("who      " + ", ".join(who))
        au = d.get("audio") or {}
        if au:
            snd = [f'{au["lufs"]:g} LUFS'] if au.get("lufs") is not None else []
            for key, name in (("speech", "speech"), ("wind", "wind"), ("rumble", "rumble (wind or an engine)"), ("clipping", "clipping")):
                if au.get(key):
                    snd.append(f"{name} {_spans(au[key], limit=5)}")
            if au.get("silent") or (au.get("silence") and quality.total(au["silence"]) >= 0.9 * c["duration"]):
                snd = ["silent"]
            if snd:
                lines.append("sound    " + " | ".join(snd))
        tele = d.get("telemetry") or {}
        if tele.get("gps"):
            place = [f'{tele["gps"]["lat"]:.5f}, {tele["gps"]["lon"]:.5f}']
            if tele.get("distance_m", 0) >= 20:
                place.append(f'{tele["distance_m"]} m' + (f' at {tele["speed_kmh"]["mean"]:g} km/h' if tele.get("speed_kmh") else ""))
            lines.append("place    " + " | ".join(place))
        if tele.get("gyro"):
            g = tele["gyro"]
            lines.append(f'gyro     shake {sum(x["shake"] for x in g) / len(g):.0f} °/s, turning {sum(x["turn"] for x in g) / len(g):.0f} °/s')
        if s.get("similar"):
            lines.append("similar  " + ", ".join(s["similar"]) + " (another take?)")
        lines.append("")
    return "\n".join(lines)


def run(clips_path, out, only=None, force=False, device="auto", scenes=True, clip_tags=None, audio_tags=None,
        scene_threshold=27.0, content_tags=None, detect=True, sound=True, text_path=None, people_path=None,
        recognize=True):
    """Analyze every clip. clip_tags/audio_tags: None = use them if torch & co. are installed."""
    try:
        import cv2
    except ImportError:
        raise CutterError("OpenCV is missing (it is in the Docker image)", MISSING)
    try:                                              # the models make OpenCV 5 warn about targets it ignores anyway
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:
        pass
    from . import audio_analysis, people, telemetry
    all_clips = read_json(clips_path)
    clips = all_clips
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

    def current(dst):
        try:
            return dst.exists() and read_json(dst).get("version", 1) >= VERSION
        except (ValueError, OSError):
            return False
    pending = [c for c in clips if c.get("has_video", True) and (force or not current(out / f'{c["id"]}.json'))]
    # Clips are independent, and OpenCV, onnxruntime and ffmpeg release the interpreter lock: several at a time,
    # each with two threads of its own (left to themselves they all take every core and get in each other's way).
    workers = 1 if (clip_tagger or audio_tagger) else max(1, min(8, (os.cpu_count() or 2) // 2, len(pending)))
    if workers > 1:
        import cv2
        cv2.setNumThreads(2)
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "threads;2")
    models = (None, None, None)
    if pending and detect:
        models = (model_file("faces"), model_file("objects") if available("onnxruntime") else None,
                  model_file("recognition") if recognize and people_path else None)
    if pending:
        log(f"\nImage and sound analysis of {len(pending)} clips "
            f"({sum(c['duration'] for c in pending) / 60:.0f} min of footage) …")
    local = threading.local()

    def detector():
        if not hasattr(local, "detector"):           # one per worker: the detectors keep state between frames
            local.detector = None
            if any(models):
                try:
                    local.detector = Detector(*models, threads=2 if workers > 1 else 4)
                except Exception as e:               # a broken model file must not stop the analysis
                    log(f"  ! detection is not available ({e})")
        return local.detector

    def one(c):
        t0 = time.time()
        path = pathlib.Path(c["path"])
        res = {"id": c["id"], "version": VERSION, "file": c.get("rel_path"), "duration": c["duration"], "fps": c.get("fps"),
               "resolution": f'{c.get("width")}x{c.get("height")}', "units": UNITS}
        try:
            res["scenes"] = detect_scenes(path, scene_threshold) if scenes else []
            per_sec, frames, _, samples, signature, found = video_metrics(path, c["duration"], clip_tagger is not None, detector())
        except Exception as e:
            return c, None, str(e), 0
        if models[0] and models[2]:
            import cv2
            res["faces"] = people.clip_faces(found)
            (out / "faces").mkdir(exist_ok=True)
            for old in (out / "faces").glob(f'{c["id"]}_*.jpg'):
                old.unlink()
            for k, g in enumerate(res["faces"]):
                g["thumb"] = f'faces/{c["id"]}_{k}.jpg'
                cv2.imwrite(str(out / g["thumb"]), found[g.pop("best")]["crop"])
        values, res["camera"] = quality.camera(samples, c["duration"])
        res["usable"], res["flaws"] = quality.usable(samples, values, c["duration"])
        if clip_tagger and frames:
            tags = clip_tagger.tag(frames)
            for p in per_sec:
                p.update(tags.get(p["t"], {}))
        audio_events = []
        if audio_tagger and c.get("has_audio"):
            a_sec, audio_events = audio_tagger.events(path, c["duration"])
            for p, ev in zip(per_sec, a_sec):
                p["audio"] = ev
        if sound and c.get("has_audio"):
            try:
                a_sec, res["audio"] = audio_analysis.measure(path, c["duration"], c.get("audio_channels"), lufs(path))
                for p, a in zip(per_sec, a_sec):
                    p.update(a)
            except Exception as e:
                log(f'    ! sound of {c["id"]}: {e}')
        try:
            tele = telemetry.read(path, c["duration"])
        except Exception as e:
            tele = None
            log(f'    ! telemetry of {c["id"]}: {e}')
        if tele:
            res["telemetry"] = tele
        res["per_second"] = per_sec
        res["audio_events"] = audio_events
        res["signature"] = signature
        res["summary"] = summarize(per_sec, res["scenes"], audio_events, res)
        return c, res, None, time.time() - t0

    done = 0
    with ThreadPoolExecutor(workers) as pool:
        for n, (c, res, error, took) in enumerate(pool.map(one, pending), 1):
            if res is None:
                log(f'  [{n}/{len(pending)}] {c["id"]}: ! {error}')
                continue
            (out / f'{c["id"]}.json').write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            index[c["id"]] = res["summary"]
            dump_json(index_path, index, ensure_ascii=False, indent=1)
            done += 1
            log(f'  [{n}/{len(pending)}] {c["id"]} {c["duration"]:.0f} s in {took:.0f} s, usable {res["summary"]["usable_s"]:g} s')
    flag_low_detail(all_clips, out)
    takes = find_takes(all_clips, out)
    known = people.update(all_clips, out, people_path)["people"] if people_path else []
    if text_path:
        pathlib.Path(text_path).write_text(readable(all_clips, out, known), encoding="utf-8")
    return {"output": str(out), "analyzed": done, "clip_tags": bool(clip_tagger), "audio_tags": bool(audio_tagger),
            "faces": bool(models[0]), "objects": bool(models[1]),
            "people": [{k: p[k] for k in ("id", "name", "seconds")} for p in known], "workers": workers if pending else 0,
            "takes": takes, "text": str(text_path) if text_path else None}
