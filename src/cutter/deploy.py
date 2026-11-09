"""Copy the review app and cuts to a server (rsync/ssh) and generate the server files from templates.

Target and domain live in deploy.env (git-ignored; template: deploy.env.example):
  TARGET_HOST=user@server       ssh target
  TARGET_DIR=/home/user/cutter
  DOMAIN=review.example.org     nginx site
  PORT=8089                     local port of the service on the server
  SERVICE=cutter-review         name of the systemd unit
The comments (data/feedback.sqlite) and the password file on the server are never touched.
Standard library only; needs ssh and rsync (they are in the Docker image; the launcher mounts ~/.ssh read-only).
"""
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import tempfile

from .util import CutterError, MISSING, USAGE, require_program

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE / "review_app"
TEMPLATES = HERE / "templates" / "server"
REPO = HERE.parents[1]
LEGACY = {"ZIEL_HOST": "TARGET_HOST", "ZIEL_ORDNER": "TARGET_DIR", "DIENST": "SERVICE", "DIENST_NUTZER": "SERVICE_USER"}


def load_env(p=None, env_file=None):
    env_file = env_file or os.environ.get("CUTTER_DEPLOY_ENV")       # set by the launcher (deploy.env of the checkout)
    candidates = [pathlib.Path(env_file)] if env_file else ([p.folder / "deploy.env"] if p is not None else [])
    if not env_file and not os.environ.get("CUTTER_TEST"):            # tests never reach a real server by accident
        candidates.append(REPO / "deploy.env")
    for f in candidates:
        if f.is_file():
            values = {}
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip().removeprefix("export ").strip()
                values[LEGACY.get(k, k)] = " ".join(shlex.split(v, comments=True)) if v.strip() else ""
            missing = [x for x in ("TARGET_HOST", "TARGET_DIR") if not values.get(x)]
            if missing:
                raise CutterError(f"{f}: {', '.join(missing)} missing", MISSING)
            values.setdefault("PORT", "8089")
            values.setdefault("SERVICE", "cutter-review")
            values.setdefault("SERVICE_USER", values["TARGET_HOST"].split("@")[0] if "@" in values["TARGET_HOST"] else "www-data")
            values.setdefault("DOMAIN", "")
            values["_file"] = str(f)
            return values
    raise CutterError("deploy.env not found", MISSING,
                      "Copy deploy.env.example to deploy.env (project folder or cutter folder) and fill in the server")


def process_pattern(target_dir):
    """pkill pattern for the service; the bracket keeps pkill from matching its own ssh command."""
    p = target_dir.rstrip("/") + "/app/server.py"
    return "[" + p[0] + "]" + p[1:]


def write_app_config(p, data_dir):
    """data/project.json (title, reviewers, agent, language) for the review app."""
    d = pathlib.Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps(p.app_config(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return d / "project.json"


def fill_templates(env, title, target):
    values = {"TARGET_DIR": env["TARGET_DIR"].rstrip("/"), "PORT": env["PORT"], "DOMAIN": env.get("DOMAIN") or "_",
              "SERVICE": env["SERVICE"], "SERVICE_USER": env["SERVICE_USER"], "TITLE": title or "review"}
    target = pathlib.Path(target)
    target.mkdir(parents=True, exist_ok=True)
    for v in TEMPLATES.iterdir():
        text = v.read_text(encoding="utf-8")
        for a, b in values.items():
            text = text.replace("{{" + a + "}}", str(b))
        (target / v.name).write_text(text, encoding="utf-8")
    return sorted(x.name for x in target.iterdir())


def run(p, what, env_file=None, dry_run=False):
    env = load_env(p, env_file)
    host, target = env["TARGET_HOST"], env["TARGET_DIR"].rstrip("/")
    q = shlex.quote
    cmds = [["ssh", host, f"mkdir -p {q(target + '/app')} {q(target + '/server')} {q(target + '/data/cuts')}"]]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cutter_deploy_"))
    try:
        return _run(p, what, env, host, target, cmds, tmp, dry_run)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(p, what, env, host, target, cmds, tmp, dry_run):
    q = shlex.quote
    local_data = p.path("review") if p is not None else None
    restart = False
    for w in what:
        if w == "app":
            cmds.append(["rsync", "-rlt", "--delete", "--exclude", "__pycache__", "--exclude", "*.pyc", f"{APP}/", f"{host}:{target}/app/"])
            if p is not None and p.file:
                cp = write_app_config(p, tmp)
                cmds.append(["rsync", "-rlt", str(cp), f"{host}:{target}/data/project.json"])
            restart = True
        elif w == "server":
            fill_templates(env, p.app_config()["title"] if p is not None else "", tmp / "server")
            cmds.append(["rsync", "-rlt", f"{tmp / 'server'}/", f"{host}:{target}/server/"])
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", w):
                raise CutterError(f"Invalid cut name: {w}", USAGE)
            d = local_data / "cuts" / w if local_data else None
            if not d or not (d / "timeline.json").exists():
                raise CutterError(f"No review data for {w} under {d}", MISSING, f"First: cutter review-data {w}")
            # video and images first, timeline.json last: the cut appears in the app only when it is complete
            cmds.append(["rsync", "-rlt", "--delete", "--partial", "--exclude", "timeline.json", f"{d}/", f"{host}:{target}/data/cuts/{w}/"])
            cmds.append(["rsync", "-rlt", f"{d / 'timeline.json'}", f"{host}:{target}/data/cuts/{w}/"])
    if restart:
        # systemd restarts the service right away (Restart=always); only if it is running already
        cmds.append(["ssh", host, f"systemctl is-active --quiet {q(env['SERVICE'])} && "
                                  f"pkill -u \"$(id -u)\" -f {q(process_pattern(target))} || true"])
    text = "\n".join("  " + " ".join(q(str(x)) for x in c) for c in cmds)
    if dry_run:
        return {"dry_run": True, "target": f"{host}:{target}", "commands": cmds, "env": env["_file"],
                "text": f"Dry run – would run (target from {env['_file']}):\n{text}"}
    if os.environ.get("CUTTER_TEST"):
        raise CutterError(f"Tests never deploy to a server (CUTTER_TEST is set; target {host})", USAGE)
    require_program("ssh"); require_program("rsync")
    for c in cmds:
        r = subprocess.run([str(x) for x in c])
        if r.returncode:
            raise CutterError(f"Command failed ({r.returncode}): {' '.join(map(str, c))}")
    hint = ""
    if "server" in what:
        hint = f"\nOnce on the server (needs sudo – ask the user first): ssh -t {host} sudo bash {target}/server/install.sh"
    return {"target": f"{host}:{target}", "commands": len(cmds), "text": f"Uploaded to {host}:{target}: {', '.join(what)}{hint}"}
