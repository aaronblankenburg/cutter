"""cutter doctor: check what a film project needs and say how to fix what is missing.

The launcher (bin/cutter) checks the host – Docker, the image, disk space, Chrome, the FilmCraft desktop app – and
passes its findings in as JSON (--host-facts); this module adds what can only be seen inside the image: the
FilmCraft CLI, the Whisper model in the cutter-models volume, the footage mounts, fonts and Python packages.
Downloads are listed with their size; nothing is downloaded here.
"""
import glob
import importlib.util
import json
import os
import shutil
import socket

from .util import OK

MODEL_DIRS = {"large-v3-turbo": "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
              "large-v3": "models--Systran--faster-whisper-large-v3"}
MODEL_MB = {"large-v3-turbo": 1600, "large-v3": 3100}
FONTS = {"Noto Sans (subtitles, end text)": ("NotoSans-Regular", "NotoSans-Bold", "NotoSans-SemiBold"),
         "P052 (titles)": ("P052-Roman",),
         "Liberation Sans Narrow (credits, map labels)": ("LiberationSansNarrow-Bold", "LiberationSansNarrow-Regular")}


def _item(name, ok, detail="", fix=None, required=True, download_mb=None):
    d = {"name": name, "ok": bool(ok), "required": required, "detail": detail}
    if not ok and fix:
        d["fix"] = fix
    if not ok and download_mb:
        d["download_mb"] = download_mb
    return d


def model_present(model):
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(os.environ.get("HF_HOME") or os.path.expanduser("~/.cache/huggingface"), "hub")
    d = MODEL_DIRS.get(model)
    if os.path.isdir(model):
        return True, model
    if not d:
        return False, hub
    snaps = glob.glob(os.path.join(hub, d, "snapshots", "*", "model.bin"))
    return bool(snaps), (os.path.dirname(snaps[0]) if snaps else hub)


def font_present(stems):
    for root in ("/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.local/share/fonts"), "/Library/Fonts",
                 "/System/Library/Fonts"):
        for stem in stems:
            if glob.glob(os.path.join(root, "**", stem + ".*"), recursive=True):
                return True
    return False


def check(project=None, filmcraft_port=None, model="large-v3-turbo", host_facts=None):
    checks = []
    host = json.loads(host_facts) if host_facts else {}
    in_container = bool(os.environ.get("CUTTER_IN_CONTAINER"))
    for c in host.get("checks", []):                  # from the launcher: Docker, image, disk space, Chrome, FilmCraft app
        checks.append(c)
    if not host and not in_container:
        checks.append(_item("Docker launcher", False, "running natively; the launcher bin/cutter checks Docker and the image",
                            "use ~/.local/share/cutter/bin/cutter doctor", required=False))
    # tools inside the image (or natively)
    for prog in ("ffmpeg", "ffprobe"):
        checks.append(_item(prog, shutil.which(prog), shutil.which(prog) or "missing", "part of the Docker image"))
    missing = [m for m in ("numpy", "librosa", "opentimelineio", "faster_whisper", "PIL", "cv2") if importlib.util.find_spec(m) is None]
    checks.append(_item("Python packages", not missing, "all present" if not missing else "missing: " + ", ".join(missing),
                        "part of the Docker image (bin/cutter …)"))
    from .filmcraft import cli_version, find_cli
    cli = find_cli()
    checks.append(_item("FilmCraft CLI (headless)", cli, f"{cli} {cli_version(cli) or ''}".strip() if cli else "missing",
                        "part of the Docker image; natively: install FilmCraft or set FILMCRAFT_CLI", required=False))
    ok_m, where = model_present(model)
    checks.append(_item(f"Whisper model {model}", ok_m, where, f"downloaded on the first 'cutter analyze' into the volume cutter-models "
                        f"(about {MODEL_MB.get(model, 1600) / 1000:.1f} GB) – ask the user first", required=False,
                        download_mb=MODEL_MB.get(model)))
    from .image_analysis import MODELS, model_file
    absent = [name for name in MODELS if model_file(name, download=False) is None]
    checks.append(_item("Detection models (faces, people, objects)", not absent, "present" if not absent else "missing: " + ", ".join(absent),
                        "downloaded on the first 'cutter analyze' into the volume cutter-models (20 MB)", required=False,
                        download_mb=sum(round(MODELS[name][1]) for name in absent) or None))
    for label, stems in FONTS.items():
        checks.append(_item(f"Font {label}", font_present(stems), stems[0], "part of the Docker image (fonts-noto-core, fonts-urw-base35, fonts-liberation)",
                            required=False))
    if project is not None and project.file:
        roots = project.media_roots()
        if not roots:
            checks.append(_item("Footage", False, "no [media] in cutter.toml", "cutter.toml → [media] name = \"/path\""))
        for name, path in roots.items():
            readable = os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)
            detail = path
            if readable:
                try:
                    n = sum(1 for _ in zip(range(5), os.scandir(path)))
                    detail += f" ({'readable' if n else 'empty'})"
                except OSError as ex:
                    readable, detail = False, f"{path}: {ex}"
            checks.append(_item(f"Footage '{name}'", readable, detail,
                                "check the path in cutter.toml; on macOS share the folder in Docker Desktop → Settings → Resources → File sharing"))
        try:
            st = shutil.disk_usage(project.folder)
            free = st.free / 1e9
            checks.append(_item("Free space (project)", free > 5, f"{free:.1f} GB free in {project.folder}",
                                "free some space: analysis files and exports need a few GB", required=free > 1))
        except OSError:
            pass
    if filmcraft_port:
        s = socket.socket()
        s.settimeout(1.5)
        try:
            s.connect(("127.0.0.1", int(filmcraft_port)))
            checks.append(_item(f"FilmCraft control channel :{filmcraft_port}", True, "reachable", required=False))
        except OSError as ex:
            checks.append(_item(f"FilmCraft control channel :{filmcraft_port}", False, str(ex), "start it: cutter filmcraft gui", required=False))
        finally:
            s.close()
    ok = all(c["ok"] or not c.get("required", True) for c in checks)
    downloads = sum(c.get("download_mb") or 0 for c in checks if not c["ok"])
    return {"ok": ok, "in_container": in_container, "checks": checks, "downloads_mb": downloads, "code": OK if ok else 4}


def as_text(r):
    lines = []
    for c in r["checks"]:
        mark = "ok " if c["ok"] else ("!! " if c.get("required", True) else "-- ")
        lines.append(f"{mark} {c['name']}: {c.get('detail', '')}")
        if not c["ok"] and c.get("fix"):
            lines.append(f"     fix: {c['fix']}" + (f" (download about {c['download_mb']} MB)" if c.get("download_mb") else ""))
    lines.append("All required parts are in place." if r["ok"] else "Something required is missing (marked !!).")
    if r.get("downloads_mb"):
        lines.append(f"Pending downloads: about {r['downloads_mb'] / 1000:.1f} GB – ask the user before starting them.")
    return "\n".join(lines)
