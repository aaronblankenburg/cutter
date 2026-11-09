"""Shared helpers: errors with exit codes, output (text or JSON), processes, ffprobe, loudness.

Standard library only, so the launcher and the host-side commands (doctor, agent, deploy, feedback) run without
extra packages.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

# Exit codes (also listed in the CLI help and in AGENTS.md)
OK, FAILED, USAGE, MISSING, FINDING = 0, 1, 2, 3, 4


class CutterError(Exception):
    """Error with a clear message and an exit code. The CLI prints the message, never a traceback."""

    def __init__(self, text, code=FAILED, hint=None):
        super().__init__(text)
        self.text, self.code, self.hint = text, code, hint


class Output:
    """Messages for people on stdout (or stderr with --json); with --json exactly one JSON object on stdout."""

    def __init__(self, as_json=False):
        self.as_json = as_json

    def info(self, *parts):
        print(*parts, file=sys.stderr if self.as_json else sys.stdout, flush=True)

    def warn(self, text):
        print(f"Warning: {text}", file=sys.stderr, flush=True)

    def result(self, data, text=None):
        if self.as_json:
            print(json.dumps(data, ensure_ascii=False, indent=1, default=str), flush=True)
        elif text:
            print(text, flush=True)


def log(*parts):
    """Progress messages of long-running tools; stderr when the CLI runs with --json."""
    print(*parts, file=sys.stderr if os.environ.get("CUTTER_JSON") else sys.stdout, flush=True)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump_json(path, data, **kw):
    """json.dump into a file that is closed right away."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, **kw)


def write_json(path, data, indent=1):
    """Write atomically (next to the target first, then replace)."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)
    return path


def require_program(name, hint=None):
    p = shutil.which(name)
    if not p:
        raise CutterError(f"Program not found: {name}", MISSING, hint)
    return p


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def require_file(path, what="File"):
    p = pathlib.Path(path)
    if not p.exists():
        raise CutterError(f"{what} not found: {p}", MISSING)
    return p


def lufs(path, start=None, duration=None):
    """Integrated loudness (EBU R128) via ffmpeg; None if it cannot be measured."""
    cmd = ["ffmpeg", "-nostats", "-v", "info"]
    if start is not None:
        cmd += ["-ss", str(start)]
    if duration is not None:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    return float(m[-1]) if m else None
