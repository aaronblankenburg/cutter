"""Drive FilmCraft: bring a version in and finish it (finish), export it (always with burned-in subtitles).

The default is the headless mode of filmcraft-cli: every version is built on a NEW project file
(--project <base> --save-as <target>); the base and the file open in the GUI stay untouched.
With a bridge port everything goes through the control channel to the running app instead
(filmcraft --control <port>, token from a file with mode 600) – for when the user wants to watch.

finish (taken over from the proven per-version script; values come from the project's FilmCraft configuration):
  Phase 1 (single commands, because their results are needed): optional backup, rename the old sequence, import the
          FCP 7 XML, open the sequence, graphics track, titles, credit layers.
  Phase 2 (one batch, `filmcraft-cli run`): map zoom with ease-in-out and place names, title and credit styling,
          audio chain per dialogue clip, end-text cards, music levels lowered under dialogue and faded out, fade-ins,
          stabilisation, subtitles, marker colours, save.
A dry run uses the stand-in (FakeFilmCraft): it rebuilds the imported sequence from the OTIO next to the XML (or
takes a recording of `inspect`), writes the batch and does not call FilmCraft.

Standard library only. `filmcraft-cli mcp` is not used.
"""
import glob
import json
import math
import os
import pathlib
import re
import shutil
import struct
import subprocess
import time

from .legacy import edit_from_legacy
from .util import CutterError, MISSING, dump_json, read_json

T = 254016000000                 # ticks per second
tk = lambda s: round(s * T)
EXPORT_SETTINGS = {"burnCaptions": True}

DEFAULTS = {
    "old_name": None,
    "music_lufs": {},
    "titles": [],
    "credits": None,
    "map": {"background_nr": "auto", "route_opacity": 80, "zoom_step": 0.08, "map_center": None,
            "fonts": {"Bold": "Liberation Sans Narrow", "Italic": "Liberation Sans Narrow"},
            "point_key": "point", "point": {"text": "•", "size": 34, "color": "#282828", "style": "Bold", "tracking": 0},
            "outline_texts": [], "outline": {"stroke": True, "stroke_width": 3, "stroke_color": "#ffffff"},
            "background_white": 997, "background_normal_value": 128, "blur": 66},
    "audio_chain": {"highpass_hz": 100,
                    "compressor": {"comp_threshold": -24.0, "comp_ratio": 3.0, "comp_attack": 8.0, "comp_release": 160.0},
                    "limiter": {"lim_on": True, "lim_threshold": -1.0}},
    "music": {"base_lufs": -16.0, "under_lufs": -35.0, "low_lufs": -40.0, "fade_out_s": {}, "ramp_down_s": 0.4,
              "ramp_up_s": 0.6},
    "end_text": {"ramp_s": 0.5, "shadow": {"shadow": True, "shadow_color": "#000000", "shadow_distance": 0,
                                           "shadow_opacity": 80, "shadow_blur": 8, "shadow_size": 2}},
    "stabilize": {"max_scale": 125.0},
    "captions": {"name": "Subtitles", "language": None,
                 "style": {"font": "Noto Sans", "fontStyle": "SemiBold", "size": 48, "color": "#ffffff", "background": True,
                           "backgroundColor": "#000000c8", "align": "center", "anchor": "bottom", "margin": 0.06}},
    "marker_colors": {},
    "export": {"preset": "Match Source – Adaptive Low Bitrate", "settings": EXPORT_SETTINGS},
}


def _merge(a, b):
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None):
    raw = read_json(path) if path and pathlib.Path(path).exists() else {}
    return _merge(DEFAULTS, raw)


# ---------------------------------------------------------------- locating filmcraft-cli

def cache_dir():
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "cutter", "filmcraft")


def find_cli(configured=None):
    """filmcraft-cli: FILMCRAFT_CLI, configuration, PATH (the Docker image has it), a mounted AppImage, the extract cache."""
    for p in (os.environ.get("FILMCRAFT_CLI"), configured):
        if p and os.access(os.path.expanduser(p), os.X_OK):
            return os.path.expanduser(p)
    p = shutil.which("filmcraft-cli")
    if p:
        return p
    for p in sorted(glob.glob("/tmp/.mount_film*/usr/bin/filmcraft-cli")):
        if os.access(p, os.X_OK):
            return p
    for p in sorted(glob.glob(os.path.join(cache_dir(), "*", "squashfs-root", "usr", "bin", "filmcraft-cli"))):
        if os.access(p, os.X_OK):
            return p
    return None


def cli_version(cli):
    try:
        r = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=20)
        m = re.search(r"(\d+\.\d+\.\d+)", r.stdout)
        return m.group(1) if m else None
    except (OSError, subprocess.TimeoutExpired):
        return None


# ---------------------------------------------------------------- clients

class Client:
    """Common interface: exec, inspect, run, export."""
    log = None

    def _note(self, kind, *data):
        if self.log is not None:
            self.log.append([kind, *data])


class CliClient(Client):
    def __init__(self, cli, base=None, target=None, data_dir=None, bridge_port=None, token_file=None):
        if not cli:
            raise CutterError("filmcraft-cli not found", MISSING,
                              "It is part of the Docker image (bin/cutter filmcraft ...); natively set FILMCRAFT_CLI. Check: cutter doctor")
        self.cli, self.base, self.target, self.data = cli, base, target, data_dir
        self.bridge_port, self.token_file = bridge_port, token_file
        self.saved = False          # headless: the target file exists only after the first writing command
        self.log = []
        if bridge_port is None:
            if not target:
                raise CutterError("Headless mode needs a target project file", MISSING)
            if base and pathlib.Path(base).resolve() == pathlib.Path(target).resolve():
                raise CutterError("Target file and base project must not be the same file", MISSING)

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "FILMCRAFT_CONTROL_TOKEN")}
        if self.bridge_port is not None:
            p = pathlib.Path(os.path.expanduser(self.token_file or ""))
            if not p.is_file():
                raise CutterError(f"Token file missing: {p}", MISSING, "Start FilmCraft with bin/filmcraft-start")
            env["FILMCRAFT_CONTROL_TOKEN"] = p.read_text().strip()
            env.update({k: os.environ[k] for k in ("DISPLAY", "WAYLAND_DISPLAY") if k in os.environ})
        return env

    def _base_args(self, writes):
        a = [self.cli, "--compact"]
        if self.bridge_port is not None:
            return a + ["--bridge", f"127.0.0.1:{int(self.bridge_port)}"]
        if self.data:
            a += ["--data-dir", str(self.data)]
        if not self.saved:
            if self.base:                      # without a base FilmCraft starts from an empty project
                a += ["--project", str(self.base)]
            if writes:
                a += ["--save-as", str(self.target)]
        else:
            a += ["--project", str(self.target)] + (["--save"] if writes else [])
        return a

    def _run(self, args, writes):
        r = subprocess.run(self._base_args(writes) + args, capture_output=True, text=True, env=self._env())
        if writes and self.bridge_port is None and r.returncode == 0:
            self.saved = True
        return r

    def exec(self, cid, params=None, ok_fail=False):
        self._note("exec", cid, params)
        for _ in range(6):          # right after an import the app is sometimes still busy (control channel only)
            r = self._run(["exec", cid, json.dumps(params or {}, ensure_ascii=False)], True)
            if r.returncode == 0 or "not free" not in (r.stdout + r.stderr) or self.bridge_port is None:
                break
            time.sleep(2)
        if r.returncode and not ok_fail:
            raise CutterError(f"{cid} {json.dumps(params, ensure_ascii=False)[:200]}: {r.stdout}{r.stderr}".strip())
        try:
            return json.loads(r.stdout) if r.stdout.strip() else None
        except json.JSONDecodeError:
            return r.stdout

    def inspect(self, what="sequence"):
        self._note("inspect", what)
        r = self._run(["inspect", what], False)
        if r.returncode:
            raise CutterError(f"inspect {what}: {r.stderr.strip()[:300]}")
        d = json.loads(r.stdout)
        return d.get(what, d) if what == "sequence" else d

    def run(self, batch, keep_going=True):
        self._note("run", str(batch))
        r = self._run(["run", str(batch)] + (["--keep-going"] if keep_going else []), True)
        return r.stdout, r.stderr, r.returncode

    def export(self, target, preset, settings=None, start=None, end=None, extra=()):
        args = ["export", str(target), "--preset", preset, "--settings",
                json.dumps(dict(EXPORT_SETTINGS, **(settings or {})), ensure_ascii=False)]
        if start is not None and end is not None:
            args += ["--start", f"{start:g}", "--end", f"{end:g}"]
        args += list(extra)
        self._note("export", args)
        r = self._run(args, False)
        if r.returncode:
            raise CutterError(f"Export failed: {(r.stderr or r.stdout).strip()[-600:]}")
        try:
            return json.loads(r.stdout.strip().splitlines()[0])
        except (json.JSONDecodeError, IndexError):
            return {"output": r.stdout.strip()}


class FakeFilmCraft(Client):
    """Stand-in for FilmCraft: for dry runs and tests. State optionally in a JSON file (for the command line)."""

    def __init__(self, recording=None, state_file=None, start_id=900000):
        self.state_file = state_file
        self.log = []
        if state_file and pathlib.Path(state_file).exists():
            self.z = read_json(state_file)
            return
        self.z = {"next": start_id, "sequences": {}, "active": None, "recording": None, "calls": [],
                  "project": {"activeSequence": 1, "root": {"children": [{"type": "Sequence", "item": 1, "name": "previous version"}]}}}
        if recording:
            a = read_json(recording)
            self.z["recording"] = a.get("sequence", a)

    def _id(self):
        self.z["next"] += 1
        return self.z["next"]

    def save_state(self):
        if self.state_file:
            dump_json(self.state_file, self.z)

    def _clip(self, cid):
        seq = self.z["sequences"].get(str(self.z["active"])) or {}
        for tracks in (seq.get("video", []), seq.get("audio", [])):
            for tr in tracks:
                for it in tr["items"]:
                    if it["clip"] == cid:
                        return it
        return self.z.setdefault("graphics", {}).setdefault(str(cid), {"clip": cid, "effects": [], "layers": 0})

    def sequence_from_otio(self, xml):
        """Rebuild the imported sequence from the OTIO next to the FCP 7 XML (names, times in ticks, default effects)."""
        otio_p = re.sub(r"[._]fcp7\.xml$", ".otio", str(xml))
        if not os.path.exists(otio_p):
            raise CutterError(f"The stand-in needs the OTIO next to the XML: {otio_p}", MISSING)
        o = read_json(otio_p)
        q = lambda s: round(round(s * 25) * T / 25)
        sec = lambda rt: rt["value"] / rt["rate"]
        video, audio, total = [], [], 0
        for tr in o["tracks"]["children"]:
            t, items = 0.0, []
            for it in tr["children"]:
                d = sec(it["source_range"]["duration"])
                if it["OTIO_SCHEMA"].startswith("Clip"):
                    if tr.get("kind") == "Audio":
                        lvl = 0.0
                        for md in it.get("metadata", {}).values():
                            for key in ("audio", "ton"):
                                if isinstance(md, dict) and isinstance(md.get(key), dict):
                                    lvl = max(-60.0, min(12.0, md[key].get("gain_db", 0.0)))
                        eff = [{"effect": "volume", "params": {"level": {"keyframes": 0, "value": f"Float({lvl})"}}},
                               {"effect": "channel_volume", "params": {}}, {"effect": "panner", "params": {}}]
                    else:
                        eff = [{"effect": "motion", "params": {}}, {"effect": "opacity", "params": {}}]
                    items.append({"clip": self._id(), "name": it.get("name", ""), "start": q(t), "end": q(t + d), "effects": eff})
                t += d
            total = max(total, q(t))
            (audio if tr.get("kind") == "Audio" else video).append({"name": tr.get("name", ""), "items": items})
        markers = [{"id": self._id(), "name": m["name"], "start": q(sec(m["marked_range"]["start_time"]))}
                   for m in o["tracks"].get("markers", [])]
        return {"video": video, "audio": audio, "duration": total, "markers": markers}

    def exec(self, cid, params=None, ok_fail=False):
        params = params or {}
        self._note("exec", cid, params)
        self.z.setdefault("calls", []).append([cid, params])
        r = {}
        if cid == "file.import":
            seq = self.z["recording"] or self.sequence_from_otio(params["paths"][0])
            sid = self._id()
            self.z["sequences"][str(sid)] = seq
            self.z["project"]["root"]["children"].append({"type": "Sequence", "item": sid, "name": "imported"})
            r = {"sequences": [sid], "documents": [{"offlineMedia": [], "sequences": [sid]}], "errors": []}
        elif cid == "sequence.open":
            self.z["active"] = params["item"]
            self.z["project"]["activeSequence"] = params["item"]
        elif cid == "effects.apply":
            applied = []
            for c in params["clips"]:
                it = self._clip(c)
                it.setdefault("effects", []).append({"effect": params["effect"], "params": {}})
                applied.append({"clip": c, "index": len(it["effects"]) - 1})
            r = {"applied": applied}
        elif cid == "sequence.addTracks":
            seq = self.z["sequences"][str(self.z["active"])]
            new = [self._id() for _ in range(params.get("video", 0))]
            seq["video"].extend({"name": f"V{len(seq['video']) + 1}", "items": []} for _ in new)
            r = {"video": new, "audio": [], "submix": []}
        elif cid in ("graphics.newText", "graphics.newRectangle"):
            if params.get("clip"):
                it = self._clip(params["clip"])
                it["layers"] = it.get("layers", 1) + 1
                r = {"clip": params["clip"], "layer": it["layers"] - 1}
            else:
                c = self._id()
                self.z.setdefault("graphics", {})[str(c)] = {"clip": c, "effects": [], "layers": 1}
                r = {"clip": c, "layer": 0}
        self.save_state()
        return r

    def inspect(self, what="sequence"):
        self._note("inspect", what)
        if what == "project":
            return json.loads(json.dumps(self.z["project"]))
        seq = self.z["sequences"].get(str(self.z["active"]))
        if seq is None:
            raise CutterError("Stand-in: no sequence open")
        return json.loads(json.dumps(seq))          # like the real CLI: read fresh every time

    def run(self, batch, keep_going=True):
        self._note("run", str(batch))
        lines = [json.loads(z) for z in pathlib.Path(batch).read_text(encoding="utf-8").splitlines() if z.strip() and not z.startswith("#")]
        out = "\n".join(json.dumps({"id": z["id"], "line": i, "ok": True, "result": {}}, separators=(",", ":"))
                        for i, z in enumerate(lines, 1))
        return out + "\n", "", 0

    def export(self, target, preset, settings=None, start=None, end=None, extra=()):
        args = {"target": str(target), "preset": preset, "settings": dict(EXPORT_SETTINGS, **(settings or {})), "start": start, "end": end}
        self._note("export", args)
        return {"dry_run": True, **args}


# ---------------------------------------------------------------- helpers

def png_size(path):
    """Width and height of a PNG file from its header (no image library), else None."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
    except OSError:
        pass
    return None


def _level(c):
    v = [e["params"]["level"]["value"] for e in c.get("effects", []) if e.get("effect") == "volume" and "level" in e.get("params", {})]
    try:
        return float(v[0].split("(")[1].rstrip(")")) if v else 0.0
    except (IndexError, ValueError):
        return 0.0


def _music_lufs(cue, fc, music_analysis, retime_cues, files=None):
    ml = fc.get("music_lufs") or {}
    if cue in ml:
        return ml[cue]
    source = (retime_cues or {}).get(cue, {}).get("beats_from", cue)
    if source in ml:
        return ml[source]
    names = [cue, source] + [pathlib.Path(f).name for f in ((files or {}).get(cue), (files or {}).get(source)) if f]
    for k in names:
        if (music_analysis or {}).get(k, {}).get("lufs") is not None:
            return music_analysis[k]["lufs"]
    raise CutterError(f"No loudness for music cue {cue} (filmcraft.json → music_lufs or music.json → lufs)", MISSING)


# ---------------------------------------------------------------- finish

def finish(client, fc, edit, xml, srt, batch_path, base=None, backup=None, music_analysis=None, retime_cues=None,
           execute=True):
    """Bring a version in and finish it. Returns log lines and counts."""
    edit = edit_from_legacy(edit)[0]
    log = []
    shots = {s["nr"]: s for sec in edit["sections"] for s in sec["shots"]}
    batch = []
    add = lambda cid, params: batch.append({"id": cid, "params": params})
    ex = client.exec

    # --- phase 1: backup (control channel only), import, graphics (needs results)
    if backup:
        ex("file.saveCopy", {"path": str(backup)})
    proj = client.inspect("project")
    if fc.get("old_name"):
        for c in proj["root"]["children"]:
            if c["type"] == "Sequence" and c["item"] == proj["activeSequence"]:
                ex("clip.rename", {"item": c["item"], "name": fc["old_name"]}, ok_fail=True)
    imp = ex("file.import", {"paths": [str(xml)]})
    new_seq = imp["sequences"][0]
    ex("sequence.open", {"item": new_seq})
    d = client.inspect()
    V, A = d["video"], d["audio"]
    nr_of = lambda c: int(c["name"].split()[0]) if c["name"].split() and c["name"].split()[0].isdigit() else None
    v1 = {nr_of(c): c for c in V[0]["items"] if nr_of(c) is not None}
    a1 = {nr_of(c): c for c in A[0]["items"] if nr_of(c) is not None}
    total = d["duration"] / T
    offline = (imp.get("documents") or [{}])[0].get("offlineMedia")
    log.append(f"Import: sequence {new_seq}, offline: {offline}, duration {total:.2f} s")

    intro = edit.get("intro") or {}
    kz = intro.get("map_zoom")
    mc = fc["map"]
    map_clip = route = overview = bg = None
    if kz:
        def track_item(filename):
            if not filename:
                return None
            stem = pathlib.Path(filename).name
            return next((it["clip"] for tr in V[1:] for it in tr["items"]
                         if it["name"] == stem or it["name"].startswith(pathlib.Path(stem).stem)), None)
        map_clip = V[1]["items"][0]["clip"] if intro.get("map") else None
        route = V[2]["items"][0]["clip"] if intro.get("route") else None
        overview = track_item(intro.get("overview"))
        bnr = mc.get("background_nr", "auto")
        if bnr == "auto":
            k0 = intro["map_start"]
            bnr = next((n for n, c in sorted(v1.items()) if c["start"] / T <= k0 + 1e-6 < c["end"] / T), None)
        bg = v1[bnr]["clip"] if bnr in v1 else None
    lv = gb = None
    if bg is not None:
        lv = ex("effects.apply", {"clips": [bg], "effect": "levels"})["applied"][0]["index"]
        gb = ex("effects.apply", {"clips": [bg], "effect": "gaussian_blur"})["applied"][0]["index"]
    ex("sequence.addTracks", {"video": 1, "audio": 0})
    gtrack = len(V)

    title_clips = []
    for tt in fc.get("titles") or []:
        r = ex("graphics.newText", {"text": tt["text"], "font": tt["font"], "size": tt["size"], "position": tt["position"],
                                    "seconds": tt["duration"], "track": gtrack, "time": tk(tt["time"])})
        title_clips.append((r["clip"], tt))

    cred, cred_layers, a0 = None, [], None
    cr = fc.get("credits")
    if cr and cr.get("layers"):
        a0 = total - cr.get("duration", 20.0)
        styles = cr.get("styles", {})
        music_lines = "\n".join(fc.get("credits_music_lines") or edit.get("credits_music_lines") or [])
        for L in cr["layers"]:
            st = styles[L["style"]]
            txt = L["text"].replace("{music_lines}", music_lines)
            p = {"text": txt, "font": st["font"], "size": L["size"], "position": L["position"]}
            p.update({"seconds": cr.get("duration", 20.0), "track": gtrack, "time": tk(a0)} if cred is None else {"clip": cred})
            r = ex("graphics.newText", p); cred = r["clip"]
            props = dict(st, size=L["size"], position=L["position"])
            if L.get("line_height"):
                props["leading"] = L["line_height"] - L["size"]
            if L.get("align"):
                props["align"] = L["align"]
            cred_layers.append((r["layer"], props))
        log.append(f"Title and credits (from {a0:.2f} s) created on V{gtrack + 1}")

    # --- phase 2: batch
    if kz and map_clip is not None:
        k_start = intro["map_start"]
        s0, C0, C1 = kz["start_scale"], kz["start_center"], kz["target_center"]
        ZP = [(C1[i] - C0[i] * s0) / (1 - s0) for i in (0, 1)]          # fixed point of the zoom (map pixels)
        EM, OV = kz["route_center"], kz["overview"]
        FAK, OVM = OV["factor"], OV["center"]
        KM = mc.get("map_center")
        if not KM:
            wh = png_size(pathlib.Path(base or ".") / intro["map"])
            if not wh:
                raise CutterError(f"Unknown map size ({intro['map']}); set filmcraft.json → map.map_center", MISSING)
            KM = (wh[0] / 2, wh[1] / 2) if wh[0] % 2 or wh[1] % 2 else (wh[0] // 2, wh[1] // 2)

        def center(s): return [ZP[i] - (ZP[i] - C0[i]) * s0 / s for i in (0, 1)]
        def pos(m, s, C): return [round(960 + (m[0] - C[0]) * s, 2), round(540 + (m[1] - C[1]) * s, 2)]
        ease = lambda u: 4 * u ** 3 if u < 0.5 else 1 - (-2 * u + 2) ** 3 / 2
        t_a, t_b = kz["zoom_from"], kz["zoom_to"]

        def scale_at(t):
            u = min(1.0, max(0.0, (t - t_a) / (t_b - t_a))); return math.exp(math.log(s0) * (1 - ease(u)))

        def time_for(s_target):                                           # inverse of scale_at(t), bisection
            lo, hi = t_a, t_b
            for _ in range(60):
                m = (lo + hi) / 2
                lo, hi = (m, hi) if scale_at(m) < s_target else (lo, m)
            return round((lo + hi) / 2, 3)
        step = mc.get("zoom_step", 0.08)
        times = [k_start, t_a] + [t_a + k * step for k in range(1, int(round((t_b - t_a) / step)))] + [t_b]
        targets = [(map_clip, lambda s, C: pos(KM, s, C), 1.0)]
        if route is not None:
            targets.append((route, lambda s, C: pos(EM, s, C), 1.0))
        if overview is not None:
            targets.append((overview, lambda s, C: pos(OVM, s, C), FAK))
        for t in times:
            s = scale_at(t); C = center(s)
            for clip, pf, f in targets:
                add("effects.setParam", {"clip": clip, "effect": "motion", "param": "position", "value": pf(s, C), "time": tk(t), "keyframe": True})
                add("effects.setParam", {"clip": clip, "effect": "motion", "param": "scale", "value": round(100 * (s * f if f != 1.0 else s), 3), "time": tk(t), "keyframe": True})
        t_u0, t_u1 = (time_for(x) for x in kz["crossfade_scale"])
        log.append(f"Map zoom: {len(times)} keyframes, overview {100*s0:.1f} % -> 100 % ({t_a:.2f}-{t_b:.2f} s, ease-in-out), "
                   f"overview cross-fades {t_u0:.2f}-{t_u1:.2f} s")
        add("effects.setParam", {"clip": map_clip, "effect": "opacity", "param": "blend", "value": "Multiply"})
        if overview is not None:
            for t, val in ((k_start, 100), (t_u0, 100), (t_u1, 0)):
                add("effects.setParam", {"clip": overview, "effect": "opacity", "param": "opacity", "value": val, "time": tk(t), "keyframe": True})
        for clip, v in ((map_clip, 100), (route, mc.get("route_opacity", 80))):
            if clip is None:
                continue
            for t, val in ((k_start, v), (kz["fade_from"], v), (kz["fade_to"], 0)):
                add("effects.setParam", {"clip": clip, "effect": "opacity", "param": "opacity", "value": val, "time": tk(t), "keyframe": True})
        if bg is not None:
            if "background_white_until" in kz:
                bw, bn = mc.get("background_white", 997), mc.get("background_normal_value", 128)
                for t, val in ((k_start, bw), (kz["background_white_until"], bw), (kz["background_normal"], bn)):   # white until after the zoom
                    add("effects.setParam", {"clip": bg, "effect": lv, "param": "gamma", "value": val, "time": tk(t), "keyframe": True})
            add("effects.setParam", {"clip": bg, "effect": gb, "param": "repeat_edge", "value": True})
            for t, val in ((kz["fade_from"] - 0.023, mc.get("blur", 66)), (kz["fade_to"] - 0.03, 0)):
                add("effects.setParam", {"clip": bg, "effect": gb, "param": "blurriness", "value": val, "time": tk(t), "keyframe": True})
        # place names of the overview as text (stay sharp), shown until shortly before the zoom
        if kz.get("labels"):
            fonts = mc.get("fonts", {})
            points = []
            pk = kz.get(mc.get("point_key", "point"))
            if pk:
                points = [dict(mc.get("point"), pos=pk)]
            names_clip, name_layers = None, []
            for L in kz["labels"] + points:
                x, y = L["pos"]; y_base = round(y + L["size"] * 0.36, 1)
                p = {"text": L["text"], "font": fonts.get(L["style"], "Liberation Sans Narrow"), "fontStyle": L["style"], "size": L["size"], "position": [x, y_base]}
                p.update({"seconds": round(kz["labels_gone"] - k_start, 3), "track": gtrack, "time": tk(k_start)} if names_clip is None else {"clip": names_clip})
                r = ex("graphics.newText", p); names_clip = r["clip"]
                props = {"size": L["size"], "fill_color": L["color"], "align": "right" if L.get("right") else "center", "position": [x, y_base]}
                if L.get("tracking"):
                    props["tracking"] = L["tracking"]
                if L.get("outline") or L["text"] in mc.get("outline_texts", []):
                    props.update(mc.get("outline"))
                name_layers.append((r["layer"], props))
            for layer, props in name_layers:
                add("graphics.set", {"clip": names_clip, "layer": layer, "props": props})
            for t, v in ((k_start, 100), (kz["labels_until"], 100), (kz["labels_gone"] - 0.04, 0)):
                add("effects.setParam", {"clip": names_clip, "effect": "opacity", "param": "opacity", "value": v, "time": tk(t), "keyframe": True})
            log.append(f"Place names of the overview: {len(name_layers)} layers until {kz['labels_until']:.2f} s, gone at {kz['labels_gone']:.2f} s")
    # title and credit styling
    for clip, tt in title_clips:
        if tt.get("style"):
            add("graphics.set", {"clip": clip, "layer": 0, "props": tt["style"]})
        for t, v in tt.get("opacity") or []:
            add("effects.setParam", {"clip": clip, "effect": "opacity", "param": "opacity", "value": v, "time": tk(t), "keyframe": True})
    for layer, props in cred_layers:
        add("graphics.set", {"clip": cred, "layer": layer, "props": props})
    if cred is not None:
        for t, v in ((a0, 0), (a0 + 1, 100), (total - 1, 100), (total - 0.04, 0)):
            add("effects.setParam", {"clip": cred, "effect": "opacity", "param": "opacity", "value": v, "time": tk(t), "keyframe": True})

    # dialogue: audio chain per clip (effect indices are appended one after another)
    chain = fc["audio_chain"]
    n_dialogue = 0
    for nr, s in shots.items():
        if nr not in a1 or not s.get("audio"):
            continue
        c = a1[nr]; cid = c["clip"]; idx = len(c["effects"]); au = s["audio"]
        if s["kind"] == "dialogue":
            n_dialogue += 1
            add("effects.apply", {"clips": [cid], "effect": "highpass"}); add("effects.setParam", {"clip": cid, "effect": idx, "param": "cutoff", "value": au.get("highpass_hz", chain.get("highpass_hz", 100))}); idx += 1
            if au.get("noise_reduction", 0) > 0:
                add("effects.apply", {"clips": [cid], "effect": "denoise"}); add("effects.setParam", {"clip": cid, "effect": idx, "param": "amount", "value": au["noise_reduction"]}); idx += 1
            if au.get("speech_mix", 0) > 0:
                add("effects.apply", {"clips": [cid], "effect": "speech_enhance"}); add("effects.setParam", {"clip": cid, "effect": idx, "param": "mix", "value": au["speech_mix"]}); idx += 1
            add("effects.apply", {"clips": [cid], "effect": "dynamics_rack"})
            if au.get("compressor"):
                for k, v in chain["compressor"].items():
                    add("effects.setParam", {"clip": cid, "effect": idx, "param": k, "value": v})
            else:
                add("effects.setParam", {"clip": cid, "effect": idx, "param": "comp_on", "value": False})
            for k, v in chain["limiter"].items():
                add("effects.setParam", {"clip": cid, "effect": idx, "param": k, "value": v})
        if "gain_db" in au:
            add("clip.audioGain", {"clips": [cid], "mode": "set", "db": round(au["gain_db"] - _level(c), 1) + 0.0})   # + 0.0: no -0.0

    # end text: cards with at most two lines, text on a semi-transparent dark band
    et = edit.get("end_text")
    dip = None
    if et:
        style = et["style"]; tag_nr = {s["tag"]: nr for nr, s in shots.items() if s.get("tag")}
        ec = fc["end_text"]; ramp = ec.get("ramp_s", 0.5)
        spans = []
        for card in et["cards"]:
            nr = tag_nr[card["tag"]]; t0 = v1[nr]["start"] / T
            a_, b_ = t0 + card["from"], t0 + card["to"]
            lines = card["text"].count("\n") + 1
            if lines > 2:
                raise CutterError(f"End-text card with {lines} lines (at most two): {card['text'][:40]}…")
            height = style["band"]["height"][str(lines)]
            r = ex("graphics.newRectangle", {"position": [960, style["center_y"]], "size": [1920, height], "seconds": round(b_ - a_, 3),
                                             "track": gtrack, "time": tk(a_)})
            band_clip, band_layer = r["clip"], r["layer"]
            base_y = style["center_y"] - 18 if lines == 2 else style["center_y"] + 20
            rt_ = ex("graphics.newText", {"text": card["text"], "position": [960, base_y], "size": style["size"], "font": style["font"],
                                          "fontStyle": style["fontStyle"], "clip": band_clip})
            add("graphics.set", {"clip": band_clip, "layer": band_layer,
                                 "props": {"fill_color": style["band"]["color"], "opacity": style["band"]["opacity"], "stroke": False}})
            add("graphics.set", {"clip": band_clip, "layer": rt_["layer"],
                                 "props": {"align": "center", "size": style["size"], "fill_color": style["fill_color"], "position": [960, base_y],
                                           **ec.get("shadow", {})}})
            for t, v in ((a_, 0), (a_ + ramp, 100), (b_ - ramp, 100), (b_ - 0.04, 0)):
                add("effects.setParam", {"clip": band_clip, "effect": "opacity", "param": "opacity", "value": v, "time": tk(t), "keyframe": True})
            spans.append((a_, b_, lines, band_clip))
        dip = (spans[0][0], spans[-1][1], et.get("music_dip_db", -4.0))
        log.append("End text: " + ", ".join(f"{a:.2f}-{b:.2f} s ({n} l.)" for a, b, n, _ in spans))

    # music: base level, lowered under dialogue (music_lower: even lower), ramps, fade-out
    mu = fc["music"]
    kind = {nr: s["kind"] for nr, s in shots.items()}
    rng = {nr: (v1[nr]["start"] / T, v1[nr]["end"] / T) for nr in v1}
    dialogue_rng = sorted((a, b, nr) for nr, (a, b) in rng.items() if kind.get(nr) == "dialogue")
    low = {nr for nr, s in shots.items() if s.get("audio", {}).get("music_lower")}
    FADE = dict(mu.get("fade_out_s") or {})          # per cue; a "fade_out_s" on the cue in the edit list wins
    FADE.update({m["cue"]: float(m["fade_out_s"]) for m in edit.get("music", []) if m.get("fade_out_s")})
    r_down, r_up = mu.get("ramp_down_s", 0.4), mu.get("ramp_up_s", 0.6)
    for c in (A[1]["items"] if len(A) > 1 else []):
        cue = c["name"].split()[0]; a, b = c["start"] / T, c["end"] / T
        L = _music_lufs(cue, fc, music_analysis, retime_cues, {m["cue"]: m.get("file") for m in edit.get("music", [])})
        base_db = round(mu["base_lufs"] - L, 1); kf = {}

        def put(t, v): kf[round(t, 3)] = round(v, 1)
        duck = lambda nr: round((mu["low_lufs"] if nr in low else mu["under_lufs"]) - L, 1)
        regs = [(s_, e_, nr) for s_, e_, nr in dialogue_rng if s_ < b - 0.05 and e_ > a + 0.05]
        put(a, base_db if not regs or regs[0][0] > a + 0.05 else duck(regs[0][2]))
        for i, (s_, e_, nr) in enumerate(regs):
            prev = regs[i - 1] if i else None
            if prev and abs(prev[1] - s_) < 0.05:
                put(s_ - 0.04, kf[max(kf)]); put(s_, duck(nr))
            elif s_ > a + 0.05:
                put(max(a, s_ - r_down), base_db); put(s_, duck(nr))
            nxt = regs[i + 1] if i + 1 < len(regs) else None
            if nxt and abs(nxt[0] - e_) < 0.05:
                continue
            put(e_, duck(nr))
            if e_ < b - 0.05:
                put(min(b, e_ + r_up), base_db)
        if dip and a <= dip[0] < b:
            put(dip[0] - 0.5, base_db); put(dip[0] + 0.5, base_db + dip[2]); put(dip[1], base_db + dip[2])
            put(min(b - FADE.get(cue, 0.0) - 0.1, dip[1] + 1.0), base_db)
        if cue in FADE:
            t0 = b - FADE[cue]; last = [v for t, v in sorted(kf.items()) if t <= t0]
            for t in [t for t in kf if t > t0]:
                del kf[t]
            put(t0, last[-1] if last else base_db); put(b - 0.04, -60)
        for t, v in sorted(kf.items()):
            add("effects.setParam", {"clip": c["clip"], "effect": "volume", "param": "level", "value": v, "time": tk(t), "keyframe": True})

    # fade in from black (e.g. after a cut to black): picture and sound
    for nr, s_ in shots.items():
        if s_.get("fade_in_s") and nr in v1:
            t0 = v1[nr]["start"] / T
            add("effects.setParam", {"clip": v1[nr]["clip"], "effect": "opacity", "param": "opacity", "value": 0, "time": tk(t0), "keyframe": True})
            add("effects.setParam", {"clip": v1[nr]["clip"], "effect": "opacity", "param": "opacity", "value": 100, "time": tk(t0 + s_["fade_in_s"]), "keyframe": True})
            if nr in a1:
                add("effects.setParam", {"clip": a1[nr]["clip"], "effect": "volume", "param": "level", "value": -60, "time": tk(t0), "keyframe": True})
                add("effects.setParam", {"clip": a1[nr]["clip"], "effect": "volume", "param": "level", "value": round(_level(a1[nr]), 1), "time": tk(t0 + 0.32), "keyframe": True})

    # stabilisation (Warp Stabilizer) on shots with "stabilize" and their cutaways
    v4 = {}
    v4_track = next((tr for tr in V if tr.get("name", "").startswith("V4")), V[3] if len(V) > 3 else None)
    if v4_track is not None:
        for c in v4_track["items"]:
            tok = c["name"].split()[0] if c["name"].split() else ""
            if tok.endswith("e") and tok[:-1].isdigit():
                v4.setdefault(int(tok[:-1]), []).append(c)
    n_stab = 0
    for nr in sorted(nr for nr, s in shots.items() if s.get("stabilize")):
        for c in ([v1[nr]] if nr in v1 else []) + v4.get(nr, []):
            idx = len(c["effects"])
            add("effects.apply", {"clips": [c["clip"]], "effect": "warp_stabilizer"})
            add("effects.setParam", {"clip": c["clip"], "effect": idx, "param": "max_scale", "value": fc["stabilize"]["max_scale"]})
            n_stab += 1
    log.append(f"Stabilised: {n_stab} clips")

    # subtitles, marker colours, save
    if srt and pathlib.Path(srt).exists():
        cap = fc["captions"]
        add("captions.import", {"path": str(srt), "name": cap["name"]})
        add("captions.setStyle", {"track": "C1", **cap["style"]})
        if cap.get("language"):
            add("captions.setTrack", {"track": "C1", "language": cap["language"]})
    want = fc.get("marker_colors") or {}
    for m in d.get("markers", []):
        col = next((v for k, v in want.items() if m["name"].startswith(k)), None)
        if col:
            add("markers.edit", {"target": "program", "marker": m["id"], "color": col})
    add("file.save", {})
    batch_path = pathlib.Path(batch_path)
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    batch_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in batch) + "\n", encoding="utf-8")
    result = {"batch": str(batch_path), "commands": len(batch), "dialogue": n_dialogue, "stabilised": n_stab,
              "sequence": new_seq, "duration": round(total, 3)}
    if execute:
        out, err, code = client.run(batch_path, keep_going=True)
        ok = sum(1 for z in out.splitlines() if '"ok":true' in z.replace(" ", ""))
        bad = [z for z in out.splitlines() if '"ok":false' in z.replace(" ", "")]
        log.append(f"Batch: {len(batch)} commands, {ok} ok, {len(bad)} failed; dialogue processed: {n_dialogue} clips")
        log += bad[:10]
        if err.strip():
            log.append("stderr: " + err[-800:])
        result.update(ok=ok, failed=len(bad), failed_lines=bad[:10])
    else:
        log.append(f"Batch written (not executed): {len(batch)} commands, dialogue: {n_dialogue} clips")
    result["log"] = log
    return result


# ---------------------------------------------------------------- stand-in on the command line
# For comparisons with older scripts that call filmcraft-cli as a program:
#   python3 -m cutter.filmcraft fake --state s.json [--recording inspect.json] exec|inspect|run ...

def _fake_main(argv):
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="cutter.filmcraft fake")
    ap.add_argument("--state", required=True)
    ap.add_argument("--recording")
    ap.add_argument("--compact", action="store_true")
    ap.add_argument("--keep-going", action="store_true")
    ap.add_argument("command", choices=["exec", "inspect", "run"])
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args(argv)
    fake = FakeFilmCraft(recording=a.recording, state_file=a.state)
    if a.command == "exec":
        params = json.loads(a.rest[1]) if len(a.rest) > 1 else {}
        print(json.dumps(fake.exec(a.rest[0], params), ensure_ascii=False))
    elif a.command == "inspect":
        print(json.dumps(fake.inspect(a.rest[0] if a.rest else "sequence"), ensure_ascii=False))
    else:
        out, err, code = fake.run(a.rest[0])
        sys.stdout.write(out)
    fake.save_state()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "fake":
        _fake_main(sys.argv[2:])
