"""Read and answer the comments of the review app – as before by hand, now as commands.

fetch: reads the SQLite database (read-only) and writes the export of the newest (or given) cut as JSON into the
       project: .cutter/review/feedback-<cut>.json, so the project keeps the full history. Without deploy.env (or with
       --local) it reads the local database in .cutter/review/data/; with deploy.env it runs on the server over ssh.
reply: backs up the database first (data/backups/), sets status and reply, and restarts the service on a server so
       that the reviewers' apps load the changes.
The same small script runs locally (python3 -) or on the server (ssh <host> python3 -). It works with the current
review app and with the earlier German version still running on a server. Standard library only.
"""
import datetime as dt
import json
import os
import pathlib
import shlex
import subprocess
import sys

from .legacy import feedback_from_legacy
from .util import CutterError, MISSING, USAGE, read_json, write_json

APP = pathlib.Path(__file__).resolve().parent / "review_app"

# Runs on the server (or locally). PARAM is put in front.
SCRIPT = r'''
import datetime as dt, json, os, re, sqlite3, sys
sys.dont_write_bytecode = True   # leave no __pycache__ in the app folder of the server
P = PARAM
sys.path.insert(0, P["app"])
import server   # the app running there: export in the same format as /api/export
db, data = P["db"], P["data"]
if not os.path.exists(db):
    print(json.dumps({"error": "No database: " + db})); sys.exit(3)
new_app = hasattr(server, "Store")
tables = {r[0] for r in sqlite3.connect("file:%s?mode=ro" % db, uri=True).execute("SELECT name FROM sqlite_master WHERE type='table'")}
table = "entries" if "entries" in tables else "eintraege"
cut_col = "cut" if table == "entries" else "fassung"
if P["action"] == "fetch":
    cuts = server.Cuts(data) if new_app else server.Fassungen(os.path.join(data, "fassungen"))
    names = cuts.names() if new_app else cuts.namen()
    f = P.get("cut") or (names[-1] if names else None)
    tl = (cuts.get(f) if new_app else cuts.hole(f)) if f else None
    if tl is None:
        print(json.dumps({"error": "Cut %r does not exist (available: %s)" % (f, ", ".join(names) or "-")})); sys.exit(3)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    con.row_factory = sqlite3.Row
    sql, arg = "SELECT * FROM %s WHERE %s = ?" % (table, cut_col), [f]
    if P.get("status"):
        sql += " AND status = ?"; arg.append(P["status"] if new_app else {"open": "offen", "done": "erledigt", "rejected": "verworfen"}[P["status"]])
    rows = con.execute(sql + " ORDER BY t, id", arg).fetchall()
    if new_app:
        d = server.export_data(f, tl, [server.Store.as_dict(r) for r in rows])
    else:
        d = server.export_daten(f, tl, [server.Ablage.als_dict(r) for r in rows])
    d["cuts_available"] = names
    print(json.dumps(d, ensure_ascii=False))
else:
    replies = P["replies"]
    con = sqlite3.connect(db)
    found = {r[0]: r[1] for r in con.execute("SELECT id, %s FROM %s WHERE id IN (%s)" % (cut_col, table, ",".join("?" * len(replies))), [a["id"] for a in replies])}
    missing = [a["id"] for a in replies if a["id"] not in found]
    wrong = [a["id"] for a in replies if P.get("cut") and found.get(a["id"]) not in (None, P["cut"])]
    if missing or wrong:
        print(json.dumps({"error": "Entries not found: %s; from another cut: %s" % (missing, wrong)})); sys.exit(3)
    os.makedirs(os.path.join(data, "backups"), exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup = os.path.join(data, "backups", "feedback_before_replies_%s.sqlite" % stamp)
    target = sqlite3.connect(backup); con.backup(target); target.close()
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    legacy_status = {"open": "offen", "done": "erledigt", "rejected": "verworfen"}
    with con:
        for a in replies:
            status = a["status"] if table == "entries" else legacy_status[a["status"]]
            text = (a.get("reply") or "").strip() or None
            if table == "entries":
                con.execute("UPDATE entries SET status = ?, reply = ?, changed = ? WHERE id = ?", (status, text, now, a["id"]))
            else:
                con.execute("UPDATE eintraege SET status = ?, antwort = ?, geaendert = ? WHERE id = ?", (status, text, now, a["id"]))
    con.close()
    restarted = ""
    if P.get("restart"):
        r = os.system("pkill -u \"$(id -u)\" -f %s" % P["pattern"])
        restarted = "service restarted" if r == 0 else "no running service found"
    print(json.dumps({"changed": len(replies), "backup": backup, "restart": restarted}, ensure_ascii=False))
'''


def _run(param, remote=None, dry_run=False, python=None):
    code = "import json\nPARAM = json.loads(%r)\n" % json.dumps(param, ensure_ascii=False) + SCRIPT
    cmd = ["ssh", remote, "python3", "-"] if remote else [python or sys.executable, "-"]
    if dry_run:
        return {"dry_run": True, "command": cmd, "param": {k: v for k, v in param.items() if k != "replies"}}
    if remote and os.environ.get("CUTTER_TEST"):
        raise CutterError(f"Tests never connect to a server (CUTTER_TEST is set; target {remote})", USAGE)
    r = subprocess.run(cmd, input=code, capture_output=True, text=True)
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else {}
    except (json.JSONDecodeError, IndexError):
        d = {}
    if r.returncode or "error" in d:
        raise CutterError(d.get("error") or f"Failed ({r.returncode}): {r.stderr.strip()[-500:]}", MISSING if r.returncode == 3 else 1)
    return d


def _target(p, local, data, env_file):
    from .deploy import load_env, process_pattern
    use_local = local is not None
    if not use_local and p is not None and p.file:
        try:
            load_env(p, env_file)
        except CutterError:
            use_local = True               # no server configured: the project's own database
    if use_local:
        db = pathlib.Path(local).resolve() if local else p.path("review") / "feedback.sqlite"
        d = pathlib.Path(data).resolve() if data else (db.parent if local else p.path("review"))
        return None, {"db": str(db), "data": str(d), "app": str(APP)}, None
    env = load_env(p, env_file)
    z = env["TARGET_DIR"].rstrip("/")
    return env["TARGET_HOST"], {"db": f"{z}/data/feedback.sqlite", "data": f"{z}/data", "app": f"{z}/app"}, \
        shlex.quote(process_pattern(z))


def fetch(p, cut=None, local=None, data=None, output=None, status=None, env_file=None, dry_run=False):
    remote, param, _ = _target(p, local, data, env_file)
    param.update(action="fetch", cut=cut, status=status)
    d = _run(param, remote, dry_run)
    if dry_run:
        return dict(d, text=f"Dry run: {' '.join(d['command'])} (cut {cut or 'newest'})")
    d = feedback_from_legacy(d)
    f = d.get("cut") or d.get("version")
    if output is None:
        folder = p.path("htpasswd").parent if p is not None and p.file else pathlib.Path.cwd()
        output = pathlib.Path(folder) / f"feedback-{f}.json"
    write_json(output, d)
    z = d["counts"]
    newest = d["cuts_available"][-1] if d.get("cuts_available") else f
    note = "" if f == newest else f"\nNote: {f} is not the newest cut ({newest}); only feedback on the newest cut counts."
    return {"cut": f, "file": str(output), "counts": z, "people": d["people"], "source": "server" if remote else "local",
            "text": f"Feedback on {f}: {z['total']} entries (open {z.get('open', 0)}, done {z.get('done', 0)}, rejected {z.get('rejected', 0)}) -> {output}{note}"}


def reply(p, file, cut=None, local=None, env_file=None, dry_run=False, restart=True):
    raw = read_json(file)
    items = raw.get("entries", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise CutterError(f"{file}: a list of replies is expected", MISSING)
    for a in items:
        if "antwort" in a and "reply" not in a:
            a["reply"] = a.pop("antwort")
        a["status"] = {"offen": "open", "erledigt": "done", "verworfen": "rejected"}.get(a.get("status"), a.get("status"))
        if not isinstance(a.get("id"), int) or a.get("status") not in ("open", "done", "rejected"):
            raise CutterError(f"{file}: every reply needs an id (number) and a status (open/done/rejected): {a}", MISSING)
        if a.get("reply") is not None and (not isinstance(a["reply"], str) or len(a["reply"]) > 2000):
            raise CutterError(f"{file}: reply must be text of at most 2000 characters (id {a['id']})", MISSING)
    remote, param, pattern = _target(p, local, None, env_file)
    param.update(action="reply", cut=cut, replies=[{"id": a["id"], "status": a["status"], "reply": a.get("reply")} for a in items],
                 restart=bool(restart and remote), pattern=pattern)
    d = _run(param, remote, dry_run)
    if dry_run:
        return dict(d, count=len(items), text=f"Dry run: {len(items)} replies via {' '.join(d['command'])}; backup first, restart afterwards")
    return dict(d, text=f"{d['changed']} entries answered. Backup: {d['backup']}" + (f"; {d['restart']}" if d.get("restart") else ""))
