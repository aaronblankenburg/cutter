#!/usr/bin/env python3
"""
cutter review app – a small web service in which reviewers comment on cuts of a film at exact moments.

Python standard library only (3.9+). One service, everything behind Basic Auth:
  /                      frontend from static/
  /media/<cut>/<file>    review.mp4, timeline.json, thumbnails from data/cuts/<cut>/ (with HTTP range requests)
  /api/...               JSON API, comments in SQLite (data/feedback.sqlite); /api/submit sends the feedback off:
                         a request the agent waits for with "cutter feedback wait"

  python3 server.py --host 127.0.0.1 --port 8089 --data ../data --htpasswd ../htpasswd

Passwords: python3 set_password.py <name> (stores only a scrypt hash). Lines from "htpasswd -B" (bcrypt) or
"openssl passwd -6" are recognised as well. The file is re-read when it changes.
Behind nginx: the service listens on 127.0.0.1 only; the client IP comes from X-Real-IP.
Project title, reviewers (names and colours), the agent's name and the UI language are read from
<data>/project.json if present: {"title": "My film", "people": [{"name": "Alice", "color": "#38bdf8"}],
"agent": "Claude", "ui_language": "en"}.
An app that embeds the frontend (in an iframe of the same origin, usually serving static/ itself with this API
behind its own login) may add to project.json: "person": "Alice" (the fixed reviewer name, never asked for),
"submit": false (no "Send feedback" box), "embedded": true (no brand or title in the header, the layout fills the
frame; this server then allows frames of the same origin). The frontend opens the cut of a "cut" query parameter of
its page URL (index.html?cut=cut2) when that cut exists. With "embedded" it posts {source: "cutter-review", cut,
open, entries} to its parent window (same origin) whenever the selected cut or its entries change.
Entries about the whole film have no moment ("t": null). "alternative": "<place id>/<option>" or "<place id>/keep"
records a choice among the alternatives of the cut (timeline.json "alternatives"); one per reviewer and place:
choosing again changes the earlier entry. "color": "all/off", "all/on", "<nr>/off" or "<nr>/on" switches the colour
correction (timeline.json "color") off or on for the whole cut or one shot; also one per reviewer and target. Such an
entry needs no action, reaction or text.
Data written by the earlier German version (data/fassungen/, table "eintraege", German timeline.json keys) is read
transparently; an old database is migrated once at start-up after a backup copy is made next to it.
"""
import argparse
import base64
import bisect
import datetime as dt
import email.utils
import hashlib
import hmac
import http.cookies
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

ACTIONS = {
    "remove": "Remove", "shorter": "Shorter", "longer": "Longer", "cut_earlier": "Cut earlier", "cut_later": "Cut later",
    "other_shot": "Other shot", "music_softer": "Music softer", "music_louder": "Music louder", "unclear": "Unclear",
    "like": "Like", "question": "Question",
}
REACTIONS = ["😂", "👍", "😴"]
STATUSES = ["open", "done", "rejected"]
EDIT_ACTIONS = {"cut_earlier", "cut_later"}

CUT_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")
COLOR_RE = re.compile(r"^(all|[1-9][0-9]{0,4})/(on|off)$")                   # colour correction switched on/off
ALTERNATIVE_RE = re.compile(r"^([A-Za-z0-9_-]{1,40})/(keep|[1-9])$")    # "<place id>/<option number>" or "<place id>/keep"
TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8", ".mp4": "video/mp4",
    ".jpg": "image/jpeg", ".png": "image/png", ".vtt": "text/vtt; charset=utf-8", ".ico": "image/x-icon",
}
MEDIA = {".mp4", ".json", ".jpg", ".png", ".vtt"}
COOKIE = "sf_session"
CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
MAX_FAILURES, LOCK_S = 20, 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cut TEXT NOT NULL,
    t REAL,
    t_to REAL,
    nr INTEGER,
    nr_to INTEGER,
    clip TEXT,
    source_t REAL,
    cutaway TEXT,
    section TEXT,
    music TEXT,
    edit_point TEXT,
    action TEXT,
    reaction TEXT,
    text TEXT NOT NULL DEFAULT '',
    person TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    reply TEXT,
    created TEXT NOT NULL,
    changed TEXT,
    alternative TEXT,
    color TEXT
);
CREATE INDEX IF NOT EXISTS entries_cut ON entries (cut, t);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cut TEXT NOT NULL,
    person TEXT NOT NULL,
    created TEXT NOT NULL,
    taken TEXT
);
"""
JSON_FIELDS = ("cutaway", "music", "edit_point")

# ---------------------------------------------------------------- data of the earlier German version
LEGACY_STATUS = {"offen": "open", "erledigt": "done", "verworfen": "rejected"}
LEGACY_ACTIONS = {"raus": "remove", "kuerzer": "shorter", "laenger": "longer", "schnitt_frueher": "cut_earlier",
                  "schnitt_spaeter": "cut_later", "anderes_bild": "other_shot", "musik_leiser": "music_softer",
                  "musik_lauter": "music_louder", "unverstaendlich": "unclear", "gefaellt": "like", "frage": "question"}
LEGACY_CUT_KINDS = {"einstellung": "shot", "einschnitt_an": "cutaway_in", "einschnitt_aus": "cutaway_out"}
LEGACY_KINDS = {"dialog": "dialogue", "bild": "picture", "atmo": "ambience", "titel": "title", "schwarz": "black"}


def legacy_timeline(d):
    """Review timeline.json of the German version -> the keys used here (only what the server needs)."""
    if "einstellungen" not in d:
        return d
    shots = []
    for s in d["einstellungen"]:
        shots.append({"nr": s.get("nr"), "start": s["start"], "end": s["ende"], "section": s.get("abschnitt"),
                      "kind": LEGACY_KINDS.get(s.get("art"), s.get("art")), "clip": s.get("clip"), "source_in": s.get("quelle_in"),
                      "source_out": s.get("quelle_out"), "note": s.get("notiz"),
                      "cutaways": [{"start": c["start"], "end": c["ende"], "clip": c.get("clip"), "source_in": c.get("quelle_in")}
                                   for c in s.get("einschnitte", [])]})
    return {"title": d.get("titel"), "duration": d.get("dauer"), "source": d.get("quelle"), "created": d.get("erzeugt"),
            "shots": shots, "music": [{"cue": m["cue"], "title": m.get("titel"), "start": m["start"], "end": m["ende"],
                                       "source_start": m.get("quelle_start", 0.0)} for m in d.get("musik", [])],
            "edit_points": [{"t": c["t"], "before": c.get("vor"), "after": c.get("nach"),
                             "kind": LEGACY_CUT_KINDS.get(c.get("art"), c.get("art"))} for c in d.get("schnitte", [])]}


def migrate_legacy_db(db, path):
    """Copy the comments of the German version (table "eintraege") into the table "entries", once."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "eintraege" not in tables or db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]:
        return 0
    backup = f"{path}.backup-before-migration-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target = sqlite3.connect(backup)
    db.backup(target)
    target.close()

    def js(v, keys):
        if not v:
            return None
        d = json.loads(v)
        out = {keys.get(k, k): x for k, x in d.items()}
        if "kind" in out:
            out["kind"] = LEGACY_CUT_KINDS.get(out["kind"], out["kind"])
        return json.dumps(out, ensure_ascii=False)
    cur = db.execute("SELECT * FROM eintraege ORDER BY id")
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    with db:
        for r in rows:
            db.execute("INSERT INTO entries (id, cut, t, t_to, nr, nr_to, clip, source_t, cutaway, section, music, edit_point, "
                       "action, reaction, text, person, status, reply, created, changed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"], r["fassung"], r["t"], r["t_bis"], r["nr"], r["nr_bis"], r["clip"], r["quelle_t"],
                        js(r["einschnitt"], {"quelle_t": "source_t"}), r["abschnitt"],
                        js(r["musik"], {"titel": "title", "t_stueck": "t_track"}),
                        js(r["schnitt"], {"vor": "before", "nach": "after", "art": "kind"}),
                        LEGACY_ACTIONS.get(r["aktion"], r["aktion"]), r["reaktion"], r["text"], r["person"],
                        LEGACY_STATUS.get(r["status"], r["status"]), r["antwort"], r["erstellt"], r["geaendert"]))
        db.execute("ALTER TABLE eintraege RENAME TO eintraege_legacy")
    sys.stderr.write(f"Migrated {len(rows)} comments from the earlier version (backup: {backup})\n")
    return len(rows)


def migrate_general_entries(db):
    """Databases before 0.5.0 have "t REAL NOT NULL"; general comments (about the whole film) have no moment."""
    cols = {r[1]: r for r in db.execute("PRAGMA table_info(entries)")}
    if "alternative" not in cols:                     # 0.5.0: the choice among a place's alternatives
        db.execute("ALTER TABLE entries ADD COLUMN alternative TEXT")
        cols = {r[1]: r for r in db.execute("PRAGMA table_info(entries)")}
    if "color" not in cols:                           # 0.6.0: colour correction switched on/off
        db.execute("ALTER TABLE entries ADD COLUMN color TEXT")
        cols = {r[1]: r for r in db.execute("PRAGMA table_info(entries)")}
    if not cols["t"][3]:
        return False
    sql = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'entries'").fetchone()[0]
    with db:
        db.execute("ALTER TABLE entries RENAME TO entries_before_050")
        db.execute(sql.replace("t REAL NOT NULL", "t REAL", 1))
        names = ", ".join(cols)
        db.execute(f"INSERT INTO entries ({names}) SELECT {names} FROM entries_before_050")
        db.execute("DROP TABLE entries_before_050")
        db.execute("DROP INDEX IF EXISTS entries_cut")
        db.execute("CREATE INDEX entries_cut ON entries (cut, t)")
    return True


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class ApiError(Exception):
    def __init__(self, status, text):
        super().__init__(text)
        self.status, self.text = status, text


# ---------------------------------------------------------------- authentication

_CRYPT_LOCK = threading.Lock()


def _sys_crypt(pw, stored):
    """crypt(3) for htpasswd lines ($2y$ bcrypt, $6$ sha512 …)."""
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import crypt as _crypt                       # up to Python 3.12
        with _CRYPT_LOCK:
            return _crypt.crypt(pw, stored)
    except ImportError:
        import ctypes
        import ctypes.util
        lib = ctypes.CDLL(ctypes.util.find_library("crypt") or "libcrypt.so.1")
        lib.crypt.restype, lib.crypt.argtypes = ctypes.c_char_p, [ctypes.c_char_p, ctypes.c_char_p]
        with _CRYPT_LOCK:
            r = lib.crypt(pw.encode(), stored.encode())
        return r.decode() if r else None


def password_ok(pw, stored):
    try:
        if stored.startswith("scrypt$"):
            _, n, r, p, salt, want = stored.split("$")
            want = base64.b64decode(want)
            got = hashlib.scrypt(pw.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                                 maxmem=256 * 1024 * 1024, dklen=len(want))
            return hmac.compare_digest(got, want)
        if stored.startswith("$"):
            got = _sys_crypt(pw, stored)
            return bool(got) and hmac.compare_digest(got.encode(), stored.encode())
    except Exception:
        return False
    return False


_DUMMY = "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class Auth:
    """Basic Auth against a hash file, cache for checked logins, session cookie, lock-out after failures."""

    def __init__(self, path):
        self.path, self.lock = path, threading.Lock()
        self.users, self.state = {}, ("?",)
        self.checked = {}            # sha256(Authorization) -> (user, valid_until)
        self.failures = {}           # ip -> [times]
        self.secret = secrets.token_bytes(32)

    def _load(self):
        try:
            st = os.stat(self.path)
            state = (st.st_mtime_ns, st.st_size, st.st_ino)
        except FileNotFoundError:
            state = None
        if state == self.state:
            return
        users = {}
        if state:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and ":" in line:
                        u, h = line.split(":", 1)
                        users[u.strip()] = h.strip()
        self.users, self.state = users, state
        self.checked.clear()

    def has_users(self):
        with self.lock:
            self._load()
            return bool(self.users)

    def basic(self, header):
        if not header or not header.startswith("Basic "):
            return None
        key = hashlib.sha256(header.encode("utf-8", "replace")).digest()
        with self.lock:
            self._load()
            hit = self.checked.get(key)
            if hit and hit[1] > time.time() and hit[0] in self.users:
                return hit[0]
            users = dict(self.users)
        try:
            name, pw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").split(":", 1)
        except Exception:
            return None
        stored = users.get(name)
        if not password_ok(pw, stored or _DUMMY) or stored is None:
            return None
        with self.lock:
            if len(self.checked) > 1000:
                self.checked.clear()
            self.checked[key] = (name, time.time() + 12 * 3600)
        return name

    def _sig(self, name, expiry):
        h = hashlib.sha256(self.users.get(name, "").encode()).hexdigest()[:16]   # a password change ends sessions
        return hmac.new(self.secret, f"{name}|{expiry}|{h}".encode(), hashlib.sha256).hexdigest()[:32]

    def new_cookie(self, name):
        expiry = int(time.time()) + 7 * 86400
        with self.lock:
            sig = self._sig(name, expiry)
        return base64.urlsafe_b64encode(f"{name}|{expiry}|{sig}".encode()).decode().rstrip("=")

    def cookie(self, value):
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
            name, expiry, sig = raw.rsplit("|", 2)
            if int(expiry) < time.time():
                return None
        except Exception:
            return None
        with self.lock:
            self._load()
            if name in self.users and hmac.compare_digest(sig, self._sig(name, expiry)):
                return name
        return None

    def locked(self, ip):
        t = time.time()
        with self.lock:
            lst = [x for x in self.failures.get(ip, []) if t - x < LOCK_S]
            if lst:
                self.failures[ip] = lst
            else:
                self.failures.pop(ip, None)
            return len(lst) >= MAX_FAILURES

    def failure(self, ip):
        with self.lock:
            if len(self.failures) > 10000:
                self.failures.clear()
            self.failures.setdefault(ip, []).append(time.time())


# ---------------------------------------------------------------- cuts (timeline.json)

class Timeline:
    def __init__(self, data):
        self.d = legacy_timeline(data)
        self.shots = self.d.get("shots", [])
        self.starts = [s["start"] for s in self.shots]
        self.edit_points = self.d.get("edit_points", [])
        self.edit_t = [c["t"] for c in self.edit_points]
        self.duration = float(self.d.get("duration") or (self.shots[-1]["end"] if self.shots else 0))
        self.alternatives = {a["id"]: a for a in self.d.get("alternatives") or [] if isinstance(a, dict) and a.get("id")}

    def shot(self, t):
        if not self.shots:
            return None
        i = bisect.bisect_right(self.starts, t + 1e-6) - 1
        return self.shots[max(0, min(i, len(self.shots) - 1))]

    def edit_point(self, t):
        if not self.edit_points:
            return None
        i = bisect.bisect_left(self.edit_t, t)
        cand = [j for j in (i - 1, i) if 0 <= j < len(self.edit_points)]
        j = min(cand, key=lambda j: abs(self.edit_t[j] - t))
        c = self.edit_points[j]
        return {"t": c["t"], "before": c.get("before"), "after": c.get("after"), "kind": c.get("kind", "shot")}

    def enrich(self, e, edit_hint=None):
        """Add nr, clip, source time, section, music and, if needed, the edit point from the timeline.
        A general entry (t None, about the whole film) gets none of them."""
        t = e["t"]
        if t is None:
            e.update(t_to=None, nr=None, nr_to=None, clip=None, source_t=None, cutaway=None, section=None, music=None,
                     edit_point=None)
            return e
        s = self.shot(t) or {}
        e["nr"], e["clip"], e["section"] = s.get("nr"), s.get("clip"), s.get("section")
        e["source_t"] = round(s["source_in"] + (t - s["start"]), 2) if s.get("source_in") is not None else None
        cw = next((x for x in s.get("cutaways", []) if x["start"] <= t < x["end"]), None)
        e["cutaway"] = {"clip": cw["clip"], "source_t": round(cw["source_in"] + (t - cw["start"]), 2)} if cw else None
        m = next((m for m in self.d.get("music", []) if m["start"] <= t < m["end"]), None)
        e["music"] = {"cue": m["cue"], "title": m.get("title"),
                      "t_track": round(m.get("source_start", 0.0) + (t - m["start"]), 2)} if m else None
        e["nr_to"] = None
        if e.get("t_to") is not None:
            sb = self.shot(e["t_to"] - 0.001) or {}
            e["nr_to"] = sb.get("nr") if sb.get("nr") != e["nr"] else None
        e["edit_point"] = None
        if e.get("action") in EDIT_ACTIONS:
            e["edit_point"] = self.edit_point(edit_hint if isinstance(edit_hint, (int, float)) else t)
        return e


class AppConfig:
    """data/project.json: title, reviewers, agent and language for the frontend; re-read when it changes."""

    def __init__(self, path):
        self.path, self.state, self.data, self.lock = path, None, {}, threading.Lock()

    def get(self):
        try:
            state = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            return {}
        with self.lock:
            if state != self.state:
                try:
                    with open(self.path, encoding="utf-8") as f:
                        d = json.load(f)
                except (OSError, ValueError):
                    d = {}
                people = [{"name": str(p["name"])[:40], "color": str(p.get("color") or "")[:20]}
                          for p in (d.get("people") or []) if isinstance(p, dict) and p.get("name")]
                self.data = {"title": str(d.get("title") or "")[:120], "people": people[:20],
                             "agent": str(d.get("agent") or "Claude")[:40], "ui_language": str(d.get("ui_language") or "")[:8]}
                # set by an app that embeds the frontend (see the module docstring)
                if isinstance(d.get("person"), str) and d["person"].strip():
                    self.data["person"] = " ".join(d["person"].split())[:40]
                if d.get("submit") is False:
                    self.data["submit"] = False
                if d.get("embedded") is True:
                    self.data["embedded"] = True
                self.state = state
            return self.data


class Cuts:
    def __init__(self, data_dir):
        # cuts live in data/cuts/; data/fassungen/ of the earlier version is read as well
        self.folders = [os.path.join(data_dir, "cuts"), os.path.join(data_dir, "fassungen")]
        self.cache, self.lock = {}, threading.Lock()

    def folder(self, f):
        for d in self.folders:
            if os.path.isfile(os.path.join(d, f, "timeline.json")):
                return os.path.join(d, f)
        return None

    def get(self, f):
        if not isinstance(f, str) or not CUT_RE.match(f):
            return None
        d = self.folder(f)
        if d is None:
            return None
        p = os.path.join(d, "timeline.json")
        try:
            state = os.stat(p).st_mtime_ns
        except FileNotFoundError:
            return None
        with self.lock:
            c = self.cache.get(f)
            if c and c[0] == state:
                return c[1]
        with open(p, encoding="utf-8") as fh:
            tl = Timeline(json.load(fh))
        with self.lock:
            self.cache[f] = (state, tl)
        return tl

    def names(self):
        names = set()
        for d in self.folders:
            try:
                names |= {n for n in os.listdir(d) if CUT_RE.match(n) and os.path.isfile(os.path.join(d, n, "timeline.json"))}
            except FileNotFoundError:
                pass
        return sorted(names, key=lambda n: [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", n)])


# ---------------------------------------------------------------- database

class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        migrate_legacy_db(self.db, path)
        migrate_general_entries(self.db)
        self.db.row_factory = sqlite3.Row
        self.start, self.counter = secrets.token_hex(3), 0

    def rev(self):
        return f"{self.start}-{self.counter}"

    @staticmethod
    def as_dict(r):
        d = {k: r[k] for k in r.keys()}
        for k in JSON_FIELDS:
            d[k] = json.loads(d[k]) if d.get(k) else None
        d["action_text"] = ACTIONS.get(d.get("action"))
        return d

    def list(self, cut, status=None):
        sql, arg = "SELECT * FROM entries WHERE cut = ?", [cut]
        if status:
            sql += " AND status = ?"
            arg.append(status)
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY t IS NOT NULL, t, id", arg).fetchall()   # general ones first
        return [self.as_dict(r) for r in rows]

    def one(self, eid):
        with self.lock:
            r = self.db.execute("SELECT * FROM entries WHERE id = ?", (eid,)).fetchone()
        return self.as_dict(r) if r else None

    def _values(self, e):
        return {k: (json.dumps(e[k], ensure_ascii=False) if k in JSON_FIELDS and e.get(k) is not None else e.get(k))
                for k in ("cut", "t", "t_to", "nr", "nr_to", "clip", "source_t", "cutaway", "section",
                          "music", "edit_point", "action", "reaction", "text", "person", "alternative", "color")}

    def new(self, e):
        w = self._values(e)
        w["created"] = now()
        cols = ", ".join(w)
        with self.lock:
            cur = self.db.execute(f"INSERT INTO entries ({cols}) VALUES ({', '.join('?' * len(w))})", list(w.values()))
            self.counter += 1
            eid = cur.lastrowid
        return self.one(eid)

    def change(self, eid, e):
        w = self._values(e)
        w.pop("cut"); w.pop("person")
        w["changed"] = now()
        with self.lock:
            self.db.execute(f"UPDATE entries SET {', '.join(k + ' = ?' for k in w)} WHERE id = ?", [*w.values(), eid])
            self.counter += 1
        return self.one(eid)

    def delete(self, eid):
        with self.lock:
            self.db.execute("DELETE FROM entries WHERE id = ?", (eid,))
            self.counter += 1

    def set_status(self, ids, status, reply, with_reply):
        sets, arg = ["status = ?", "changed = ?"], [status, now()]
        if with_reply:
            sets.append("reply = ?")
            arg.append(reply)
        with self.lock:
            n = self.db.execute(f"UPDATE entries SET {', '.join(sets)} WHERE id IN ({', '.join('?' * len(ids))})",
                                [*arg, *ids]).rowcount
            self.counter += 1
        return n

    # requests: a reviewer sent the feedback off; the agent takes a request with "cutter feedback wait"
    def request(self, cut):
        """The newest request for this cut (taken = when the agent picked it up), or None."""
        with self.lock:
            r = self.db.execute("SELECT * FROM requests WHERE cut = ? ORDER BY id DESC LIMIT 1", (cut,)).fetchone()
        return {k: r[k] for k in r.keys()} if r else None

    def new_request(self, cut, person):
        """One waiting request per cut: sending again before the agent took it returns the waiting one."""
        with self.lock:
            r = self.db.execute("SELECT id FROM requests WHERE cut = ? AND taken IS NULL", (cut,)).fetchone()
            if not r:
                self.db.execute("INSERT INTO requests (cut, person, created) VALUES (?, ?, ?)", (cut, person, now()))
        return self.request(cut), not r

    def own_choice(self, cut, person, place, column="alternative"):
        """The id of the person's entry that chose among the alternatives of this place (column "alternative") or
        switched the colour of this target (column "color"), or None."""
        assert column in ("alternative", "color")
        with self.lock:
            r = self.db.execute(f"SELECT id FROM entries WHERE cut = ? AND lower(person) = lower(?) AND {column} LIKE ? "
                                "ORDER BY id DESC LIMIT 1", (cut, person, place + "/%")).fetchone()
        return r[0] if r else None

    def count(self, cut):
        with self.lock:
            rows = self.db.execute("SELECT status, COUNT(*) FROM entries WHERE cut = ? GROUP BY status", (cut,)).fetchall()
        return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------- export

def time_text(t):
    if t is None:
        return ""
    m, s = divmod(round(t, 1), 60)
    return f"{int(m)}:{s:04.1f}"


def export_data(cut, tl, entries):
    people = {}
    for e in entries:
        people[e["person"]] = people.get(e["person"], 0) + 1
    counts = {s: sum(1 for e in entries if e["status"] == s) for s in STATUSES}
    fields = ("id", "t", "t_to", "nr", "nr_to", "clip", "source_t", "cutaway", "section", "music", "edit_point",
              "action", "action_text", "text", "reaction", "alternative", "color", "person", "status", "reply", "created", "changed")
    return {"format": 2, "cut": cut, "title": tl.d.get("title"), "duration": tl.duration,
            "source": tl.d.get("source"), "exported": now(),
            "people": [{"name": n, "entries": k} for n, k in sorted(people.items())],
            "counts": {"total": len(entries), **counts}, "actions": ACTIONS,
            "entries": [{k: e.get(k) for k in fields} for e in entries]}


def export_markdown(d, tl):
    z = d["counts"]
    lines = [f"# Feedback {d['cut']} – {d['title']}", "",
             f"Exported {d['exported'][:16].replace('T', ' ')} · {z['total']} entries "
             f"(open {z['open']}, done {z['done']}, rejected {z['rejected']}) · "
             + ", ".join(f"{p['name']} {p['entries']}" for p in d["people"]), ""]
    shots = {s["nr"]: s for s in tl.shots}
    section = nr = None
    general = [e for e in d["entries"] if e.get("t") is None]
    if general:
        lines += ["", "## Whole film", ""]
    for e in general + [e for e in d["entries"] if e.get("t") is not None]:
        if e.get("t") is None:
            lines.append(entry_line(e, ""))
            continue
        if e["section"] != section:
            section, nr = e["section"], None
            lines += ["", f"## {section or 'no section'}", ""]
        if e["nr"] != nr:
            nr = e["nr"]
            s = shots.get(nr, {})
            src = f" · {s.get('clip')} {time_text(s.get('source_in'))}–{time_text(s.get('source_out'))}" if s.get("source_in") is not None else ""
            lines += [f"### Shot {nr} · {time_text(s.get('start'))}–{time_text(s.get('end'))}{src}",
                      f"_{s.get('note', '')}_" if s.get("note") else "", ""]
        lines.append(entry_line(e, time_text(e["t"])))
    return "\n".join(lines).replace("\n\n\n", "\n\n") + "\n"


def entry_line(e, when):
    """One entry of the Markdown export (with its status and reply on a second line); when: its time, or "" (general)."""
    mark = "x" if e["status"] == "done" else ("~" if e["status"] == "rejected" else " ")
    what = " ".join(x for x in (f"**{e['action_text']}**" if e.get("action_text") else "", e.get("reaction") or "") if x)
    rng = f"–{time_text(e['t_to'])}" + (f" (to shot {e['nr_to']})" if e.get("nr_to") else "") if e.get("t_to") else ""
    extra = []
    if e.get("edit_point"):
        c = e["edit_point"]
        extra.append(f"edit {time_text(c['t'])} ({c['before']}|{c['after']}{', ' + c['kind'] if c.get('kind') != 'shot' else ''})")
    if e.get("cutaway"):
        extra.append(f"in cutaway {e['cutaway']['clip']} @ {time_text(e['cutaway']['source_t'])}")
    if e.get("alternative"):
        extra.append(f"alternative {e['alternative']}")
    if e.get("color"):
        what_, _, state = e["color"].partition("/")
        extra.append(f"colour correction {state} for {'the whole cut' if what_ == 'all' else 'shot ' + what_}")
    if e.get("music") and e.get("action") in ("music_softer", "music_louder"):
        extra.append(f"music {e['music']['cue']} @ {time_text(e['music']['t_track'])}")
    content = " ".join(x for x in (what, f"“{e['text']}”" if e.get("text") else "") if x)
    line = f"- [{mark}] {when + rng + ' · ' if when else ''}{e['person']}: {content}" + \
        (f" ({'; '.join(extra)})" if extra else "") + f" · #{e['id']}"
    if e["status"] != "open" or e.get("reply"):
        line += "\n" + f"  - {e['status']}" + (f": {e['reply']}" if e.get("reply") else "")
    return line


# ---------------------------------------------------------------- HTTP

class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, socket.timeout)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cutter-review/1.0"
    sys_version = ""
    timeout = 60

    # --- log: only errors and writing requests
    def log_message(self, fmt, *args):
        pass

    def log_request(self, code="-", size="-"):
        code = getattr(code, "value", code)
        if isinstance(code, int) and (code >= 400 or self.command not in ("GET", "HEAD")):
            sys.stderr.write(f"{self.client_ip()} {self.command} {self.path.split('?')[0]} {code}\n")

    def client_ip(self):
        ip = self.client_address[0]
        if ip in ("127.0.0.1", "::1") and self.headers.get("X-Real-IP"):
            return self.headers["X-Real-IP"].strip()
        return ip

    def send_response(self, code, message=None):
        self._started = True
        super().send_response(code, message)

    def embedded(self):
        """project.json "embedded": true allows frames of the same origin (an app that embeds the frontend)."""
        cfg = getattr(self.server, "app_config", None)
        return bool(cfg and cfg.get().get("embedded"))

    def end_headers(self):
        for k, v in (("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin"),
                     ("X-Frame-Options", "SAMEORIGIN" if self.embedded() else "DENY"), ("X-Robots-Tag", "noindex, nofollow")):
            self.send_header(k, v)
        if getattr(self, "_cookie", None):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={self._cookie}; Path=/; Max-Age={7 * 86400}; HttpOnly; SameSite=Lax{secure}")
            self._cookie = None
        super().end_headers()

    def _handle(self):
        self._started, self._cookie = False, None
        try:
            self.route()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError, socket.timeout):
            self.close_connection = True
        except Exception:
            traceback.print_exc()
            if self._started:
                self.close_connection = True
            else:
                self.send_json({"error": "Internal error"}, 500)

    do_GET = do_HEAD = do_POST = do_PATCH = do_DELETE = _handle

    # --- responses
    def send_body(self, status, body, ctype, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, status=200):
        self.send_body(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def send_text(self, status, text, ctype="text/plain; charset=utf-8", extra=()):
        self.send_body(status, text.encode("utf-8"), ctype, extra)

    def send_file(self, path, csp=False):
        st = os.stat(path)
        size = st.st_size
        etag = f'"{st.st_mtime_ns:x}-{size:x}"'
        modified = email.utils.formatdate(st.st_mtime, usegmt=True)
        ctype = TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        rng = self.headers.get("Range")
        if_range = self.headers.get("If-Range")
        if rng and if_range and if_range.strip() not in (etag, modified):
            rng = None                                   # the file changed: send it whole
        inm = self.headers.get("If-None-Match")
        if not rng and inm and etag in [x.strip() for x in inm.split(",")]:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        start, end, status = 0, size - 1, 200
        if rng:
            m = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", rng)
            if m and (m.group(1) or m.group(2)):            # one range only; multiple ranges -> whole file
                if m.group(1):
                    start = int(m.group(1))
                    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - int(m.group(2)))
                if start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", modified)
        self.send_header("Cache-Control", "no-cache")
        if csp:
            self.send_header("Content-Security-Policy",
                             CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'") if self.embedded() else CSP)
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        with open(path, "rb") as f:
            self.connection.sendfile(f, offset=start, count=length)

    # --- authentication
    def authenticate(self):
        auth = self.server.auth
        if not auth.has_users():
            self.send_text(503, "No password set yet: cutter review password <name> (on a server: python3 app/set_password.py <name>)\n")
            return None
        cookie_name = None
        raw = self.headers.get("Cookie")
        if raw:
            try:
                jar = http.cookies.SimpleCookie()
                jar.load(raw)
                if COOKIE in jar:
                    cookie_name = auth.cookie(jar[COOKIE].value)
            except http.cookies.CookieError:
                pass
        ip = self.client_ip()
        header = self.headers.get("Authorization")
        if header:
            if auth.locked(ip):
                self.send_text(429, "Too many failed attempts. Please try again in 15 minutes.\n", extra=(("Retry-After", str(LOCK_S)),))
                return None
            name = auth.basic(header)
            if name:
                if cookie_name != name:
                    self._cookie = auth.new_cookie(name)
                return name
            auth.failure(ip)
            sys.stderr.write(f"{ip} login failed\n")
        elif cookie_name:
            return cookie_name
        self.send_text(401, "Login required.\n", extra=(("WWW-Authenticate", 'Basic realm="cutter review", charset="UTF-8"'),))
        return None

    # --- routes
    def route(self):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        query = urllib.parse.parse_qs(url.query)
        user = self.authenticate()
        if not user:
            return
        if path.startswith("/api/"):
            return self.api(path, query, user)
        if self.command not in ("GET", "HEAD"):
            return self.send_text(405, "Method not allowed\n", extra=(("Allow", "GET, HEAD"),))
        if path in ("/", "/index.html"):
            return self.send_file(os.path.join(STATIC_DIR, "index.html"), csp=True)
        m = re.fullmatch(r"/media/([^/]+)/([^/]+)", path)
        if m:
            f, name = m.groups()
            if CUT_RE.match(f) and FILE_RE.match(name) and os.path.splitext(name)[1].lower() in MEDIA:
                d = self.server.cuts.folder(f)
                p = os.path.join(d, name) if d else None
                if p and os.path.isfile(p):
                    return self.send_file(p)
            return self.send_text(404, "Not found\n")
        m = re.fullmatch(r"/([^/]+)", path)
        if m and FILE_RE.match(m.group(1)):
            p = os.path.join(STATIC_DIR, m.group(1))
            if os.path.isfile(p):
                return self.send_file(p, csp=p.endswith(".html"))
        self.send_text(404, "Not found\n")

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n <= 0 or n > 65536:
            raise ApiError(400, "Empty or too large request")
        try:
            d = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Invalid JSON")
        if not isinstance(d, dict):
            raise ApiError(400, "JSON object expected")
        return d

    def cut(self, value):
        tl = self.server.cuts.get(value)
        if tl is None:
            raise ApiError(404, f"Cut {value!r} does not exist")
        return tl

    @staticmethod
    def number(value, name, tl, empty=False):
        if value is None and empty:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            raise ApiError(400, f"{name}: number in seconds expected")
        if value < 0 or value > tl.duration + 1:
            raise ApiError(400, f"{name}: outside the cut (0–{tl.duration:.2f} s)")
        return round(float(value), 3)

    @staticmethod
    def content(d, e, optional=False):
        """Check action, reaction, text and copy them into e (only keys that are present). optional: an entry that
        says something by itself (a colour switch) needs none of them."""
        if "action" in d:
            if d["action"] not in (None, "") and d["action"] not in ACTIONS:
                raise ApiError(400, "Unknown action")
            e["action"] = d["action"] or None
        if "reaction" in d:
            if d["reaction"] not in (None, "") and d["reaction"] not in REACTIONS:
                raise ApiError(400, "Unknown reaction")
            e["reaction"] = d["reaction"] or None
        if "text" in d:
            if not isinstance(d["text"], str) or len(d["text"]) > 2000:
                raise ApiError(400, "Text missing or too long (at most 2000 characters)")
            e["text"] = d["text"].strip()
        if not (optional or e.get("action") or e.get("reaction") or e.get("text")):
            raise ApiError(400, "Give an action, a reaction or a text")

    @staticmethod
    def color(value, tl):
        """Check "all/on|off" or "<nr>/on|off" against the shots of the cut; None or "" clears it."""
        if value in (None, ""):
            return None
        m = COLOR_RE.match(value) if isinstance(value, str) else None
        if not m or (m.group(1) != "all" and int(m.group(1)) not in {s.get("nr") for s in tl.shots}):
            raise ApiError(400, 'color: "all/on", "all/off", "<shot nr>/on" or "<shot nr>/off" of this cut expected')
        return value

    @staticmethod
    def alternative(value, tl):
        """Check "<place>/<n>" or "<place>/keep" against the alternatives of the cut; None or "" clears it."""
        if value in (None, ""):
            return None
        m = ALTERNATIVE_RE.match(value) if isinstance(value, str) else None
        place = tl.alternatives.get(m.group(1)) if m else None
        if not place or (m.group(2) != "keep" and int(m.group(2)) > len(place.get("options") or [])):
            raise ApiError(400, 'alternative: "<place id>/<option number>" or "<place id>/keep" of this cut expected')
        return value

    @staticmethod
    def person(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 40:
            raise ApiError(400, "Name missing (at most 40 characters)")
        return " ".join(value.split())

    def api(self, path, query, user):
        store, cuts, m = self.server.store, self.server.cuts, self.command
        arg = lambda k, default=None: (query.get(k) or [default])[0]
        try:
            if path == "/api/cuts" and m == "GET":
                lst = []
                for name in cuts.names():
                    tl = cuts.get(name)
                    z = store.count(name)
                    lst.append({"id": name, "title": tl.d.get("title"), "duration": tl.duration, "created": tl.d.get("created"),
                                "video": os.path.isfile(os.path.join(cuts.folder(name), "review.mp4")),
                                "entries": sum(z.values()), "open": z.get("open", 0)})
                return self.send_json({"login": user, "cuts": lst, "actions": list(ACTIONS), "reactions": REACTIONS,
                                       "statuses": STATUSES, "app": self.server.app_config.get()})

            if path == "/api/entries" and m == "GET":
                f = arg("cut")
                self.cut(f)
                if arg("rev") == store.rev():
                    return self.send_json({"rev": store.rev(), "unchanged": True, "request": store.request(f)})
                rev = store.rev()
                return self.send_json({"rev": rev, "entries": store.list(f), "request": store.request(f)})

            if path == "/api/submit" and m == "POST":
                d = self.read_json()
                self.cut(d.get("cut"))
                request, new = store.new_request(d["cut"], self.person(d.get("person")))
                return self.send_json({"request": request}, 201 if new else 200)

            if path == "/api/entries" and m == "POST":
                d = self.read_json()
                tl = self.cut(d.get("cut"))
                e = {"cut": d["cut"], "person": self.person(d.get("person")), "action": None, "reaction": None, "text": ""}
                e["color"] = self.color(d.get("color"), tl)
                self.content(d, e, optional=bool(e["color"]))
                e["alternative"] = self.alternative(d.get("alternative"), tl)
                e["t"] = self.number(d.get("t"), "t", tl, empty=True)          # None: a general entry (whole film)
                e["t_to"] = self.number(d.get("t_to"), "t_to", tl, empty=True)
                if e["t_to"] is not None and (e["t"] is None or e["t_to"] <= e["t"]):
                    e["t_to"] = None
                tl.enrich(e, d.get("edit_t"))
                mine = (e["alternative"] and store.own_choice(e["cut"], e["person"], e["alternative"].split("/")[0])) \
                    or (e["color"] and store.own_choice(e["cut"], e["person"], e["color"].split("/")[0], "color"))
                if mine:                                   # choosing again changes the earlier choice
                    return self.send_json({"rev": store.rev(), "entry": store.change(mine, e)})
                return self.send_json({"rev": store.rev(), "entry": store.new(e)}, 201)

            hit = re.fullmatch(r"/api/entries/(\d+)", path)
            if hit and m in ("PATCH", "DELETE"):
                eid = int(hit.group(1))
                old = store.one(eid)
                if not old:
                    raise ApiError(404, "Entry not found")
                d = self.read_json() if m == "PATCH" else {"person": arg("person", "")}
                if self.person(d.get("person")).casefold() != old["person"].casefold():
                    raise ApiError(403, "Only your own entries can be changed or deleted")
                if m == "DELETE":
                    store.delete(eid)
                    return self.send_json({"rev": store.rev(), "deleted": eid})
                tl = self.cut(old["cut"])
                e = dict(old)
                if "color" in d:
                    e["color"] = self.color(d["color"], tl)
                self.content(d, e, optional=bool(e.get("color")))
                if "alternative" in d:
                    e["alternative"] = self.alternative(d["alternative"], tl)
                if "t" in d:
                    e["t"] = self.number(d["t"], "t", tl, empty=True)
                if "t_to" in d:
                    e["t_to"] = self.number(d["t_to"], "t_to", tl, empty=True)
                if e.get("t_to") is not None and (e["t"] is None or e["t_to"] <= e["t"]):
                    e["t_to"] = None
                edit_old = (old.get("edit_point") or {}).get("t")
                tl.enrich(e, d.get("edit_t", edit_old if "t" not in d else None))
                return self.send_json({"rev": store.rev(), "entry": store.change(eid, e)})

            if path == "/api/status" and m == "POST":
                d = self.read_json()
                ids = d.get("ids")
                if not isinstance(ids, list) or not ids or len(ids) > 1000 or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                    raise ApiError(400, "ids: list of entry numbers expected")
                if d.get("status") not in STATUSES:
                    raise ApiError(400, f"status: one of {', '.join(STATUSES)}")
                reply = d.get("reply")
                if reply is not None and (not isinstance(reply, str) or len(reply) > 2000):
                    raise ApiError(400, "reply: text (at most 2000 characters)")
                n = store.set_status(ids, d["status"], (reply or "").strip() or None, "reply" in d)
                return self.send_json({"rev": store.rev(), "changed": n})

            if path == "/api/export" and m == "GET":
                f = arg("cut")
                tl = self.cut(f)
                status = arg("status")
                if status and status not in STATUSES:
                    raise ApiError(400, f"status: one of {', '.join(STATUSES)}")
                data = export_data(f, tl, store.list(f, status))
                if arg("format") == "md":
                    return self.send_text(200, export_markdown(data, tl), "text/markdown; charset=utf-8")
                return self.send_json(data)
            raise ApiError(404 if m in ("GET", "POST", "PATCH", "DELETE") else 405, "Unknown API call")
        except ApiError as ex:
            self.send_json({"error": ex.text}, ex.status)


def main():
    here = APP_DIR
    ap = argparse.ArgumentParser(description="cutter review app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--data", default=os.path.join(here, "..", "data"), help="folder with cuts/ and feedback.sqlite")
    ap.add_argument("--media", help="folder with the cuts/ of the rendered videos, if not --data")
    ap.add_argument("--htpasswd", default=os.path.join(here, "..", "htpasswd"), help="file with name:hash lines")
    a = ap.parse_args()
    data = os.path.abspath(a.data)
    os.makedirs(os.path.join(data, "cuts"), exist_ok=True)
    srv = Server((a.host, a.port), Handler)
    srv.data = data
    srv.auth = Auth(os.path.abspath(a.htpasswd))
    srv.cuts = Cuts(os.path.abspath(a.media) if a.media else data)
    srv.store = Store(os.path.join(data, "feedback.sqlite"))
    srv.app_config = AppConfig(os.path.join(data, "project.json"))
    print(f"cutter review on http://{a.host}:{a.port}/  data: {data}  cuts: {', '.join(srv.cuts.names()) or '-'}", flush=True)
    if not srv.auth.has_users():
        print(f"No password yet: python3 {os.path.join(here, 'set_password.py')} <name>", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
