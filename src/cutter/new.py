"""Create a project (cutter new) and plan scripts from the template (cutter plan).

A project always lives apart from the footage. It contains cutter.toml, AGENTS.md with the project facts (for
Claude Code, Codex and other agents), CLAUDE.md with @AGENTS.md, and the working folders.
"""
import os
import pathlib
import re
import subprocess
import sys

from .config import FILENAMES, Project, cut_name, media_table, previous_cut, toml_string
from .util import CutterError, MISSING

TEMPLATES = pathlib.Path(__file__).resolve().parent / "templates" / "project"
FOLDERS = ["analysis", "music", "cuts", "exports", ".cutter/review"]


def _inside(child, parent):
    try:
        pathlib.Path(child).resolve().relative_to(pathlib.Path(parent).resolve())
        return True
    except ValueError:
        return False


def default_folder(media, title=""):
    """The current directory if it is empty, otherwise ./<name>-film."""
    cwd = pathlib.Path.cwd()
    if not any(cwd.iterdir()):
        return cwd
    name = title or (pathlib.Path(media[0]).name if media else "film")
    name = re.sub(r"[^\w.-]+", "-", name.strip()).strip("-").lower() or "film"
    return cwd / f"{name}-film"


def cutter_command():
    """How cutter is called on this computer (the launcher with its full path, if known)."""
    if os.environ.get("CUTTER_LAUNCHER"):
        return os.environ["CUTTER_LAUNCHER"]
    launcher = pathlib.Path(__file__).resolve().parents[2] / "bin" / "cutter"
    return str(launcher) if launcher.exists() else "cutter"


def cutter_home():
    return os.environ.get("CUTTER_HOME") or str(pathlib.Path(__file__).resolve().parents[2])


def agents_md(p):
    media = "\n".join(f"  - `{n}`: `{x}`" for n, x in p["media"].items()) or "  - (none yet: [media] in cutter.toml)"
    text = (TEMPLATES / "AGENTS.md.template").read_text(encoding="utf-8")
    values = {"{{TITLE}}": p.get("title") or p.folder.name, "{{PROJECT}}": str(p.folder), "{{MEDIA}}": media,
              "{{LICENSE}}": p.get("project_license") or "(not set: `project_license`)",
              "{{ALLOWED}}": ", ".join(p["music"]["allowed_licenses"]),
              "{{REVIEW_URL}}": p["review"].get("url") or "(none yet: `cutter serve` prints the local addresses)",
              "{{CUTTER}}": cutter_command(), "{{CUTTER_HOME}}": cutter_home()}
    for a, b in values.items():
        text = text.replace(a, b)
    return text


def create(folder=None, media=(), title="", license="", review_url="", language="", dry_run=False):
    media = [str(pathlib.Path(os.path.expanduser(m)).resolve()) for m in (media or [])]
    target = pathlib.Path(folder).expanduser().resolve() if folder else default_folder(media, title)
    for m in media:
        if not pathlib.Path(m).is_dir():
            raise CutterError(f"Footage folder not found: {m}", MISSING)
        if _inside(target, m):
            raise CutterError(f"The project folder must not be inside the footage: {target}", MISSING,
                              "Choose a folder outside the footage, e.g. ./<name>-film")
        if _inside(m, target):
            raise CutterError(f"The footage must not be inside the project folder: {m}", MISSING)
    if any((target / n).exists() for n in FILENAMES):
        raise CutterError(f"There already is a project here: {target}", MISSING)
    files = ["cutter.toml", "AGENTS.md", "CLAUDE.md", ".gitignore"]
    if dry_run:
        return {"dry_run": True, "folder": str(target), "files": files, "folders": FOLDERS,
                "text": f"Dry run – would create {target}:\n  " + "\n  ".join(files + [f + "/" for f in FOLDERS])}
    target.mkdir(parents=True, exist_ok=True)
    for f in FOLDERS:
        (target / f).mkdir(parents=True, exist_ok=True)
    toml = (TEMPLATES / "cutter.toml.template").read_text(encoding="utf-8")
    table = "\n".join(f"{n} = {toml_string(x)}" for n, x in media_table(media).items()) or '# footage = "/path/to/footage"'
    toml = toml.replace("{{TITLE}}", toml_string(title)).replace("{{MEDIA}}", table) \
               .replace("{{LICENSE}}", toml_string(license)).replace("{{REVIEW_URL}}", toml_string(review_url)) \
               .replace("{{LANGUAGE}}", toml_string(language))
    (target / "cutter.toml").write_text(toml, encoding="utf-8")
    p = Project.load(target, required=True)
    (target / "AGENTS.md").write_text(agents_md(p), encoding="utf-8")
    (target / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
    (target / ".gitignore").write_text((TEMPLATES / "gitignore.template").read_text(encoding="utf-8"), encoding="utf-8")
    cmd = cutter_command()
    return {"folder": str(target), "files": files,
            "text": f"Project created: {target}\n  Footage (read-only): {', '.join(media) or '-'}\n"
                    f"  Next: cd {target} && {cmd} doctor && {cmd} analyze"}


def plan(p, cut, new=False, previous=None, dry_run=False):
    """Plan of a cut: cuts/<cut>/plan.py reads the previous edit list and writes cuts/<cut>/edit.json."""
    cut = cut_name(cut)
    prev = cut_name(previous) if previous else previous_cut(cut)
    script = p.path("plan", cut)
    target = p.path("edit", cut)
    if new:
        if script.exists():
            raise CutterError(f"The plan already exists: {script}", MISSING)
        if dry_run:
            return {"dry_run": True, "script": str(script), "text": f"Dry run – would create {script}"}
        text = (TEMPLATES / "plan.py.template").read_text(encoding="utf-8").replace("{{CUT}}", cut).replace("{{PREVIOUS}}", prev or "-")
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(text, encoding="utf-8")
        return {"script": str(script), "text": f"Template created: {script}\nApply the feedback on {prev} in it as code, then: cutter plan {cut}"}
    if not script.exists():
        raise CutterError(f"Plan missing: {script}", MISSING, f"Create it from the template: cutter plan {cut} --new")
    source = p.path("edit", prev) if prev else None
    if source is None or not source.exists():
        raise CutterError(f"Previous edit list missing: {source}", MISSING)
    cmd = [sys.executable, str(script), str(source), str(target)]
    if dry_run:
        return {"dry_run": True, "command": cmd, "text": "Dry run: " + " ".join(cmd)}
    target.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=p.folder)
    if r.returncode:
        raise CutterError(f"{script.name} failed:\n{r.stdout}{r.stderr}".strip())
    return {"script": str(script), "edit": str(target), "output": r.stdout.strip(), "text": (r.stdout.strip() + f"\n-> {target}").strip()}
