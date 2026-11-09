#!/usr/bin/env python3
"""Synthetic test data for the browser tests of the review app (tests/browser).

  python tests/browser/make_fixture.py DIR [--ui-language en|de] [--login tester]

Writes into DIR:
  data/cuts/cut4, cut5, cut6/  review.mp4 (640x360, 25 fps: ffmpeg testsrc2 with a frame counter, a shot label and a
                               tone), thumbs_NN.jpg and thumbs.vtt, poster.jpg, timeline.json (format 2)
  data/project.json            title, the reviewers "Reviewer A" and "Reviewer B", the agent "Claude", ui_language
  htpasswd, password.txt       a random password made now and its scrypt hash (both mode 600)

Everything is invented: shots, sections, music cues, spoken text, subtitles and the agent's change notes (decisions in
cut5 and cut6, none in cut4, like a first cut). Shot boundaries come from a seeded random generator, except the places
the tests rely on, which are fixed in CUTS and verified by check_assumptions() before anything is rendered.
Needs ffmpeg and ffprobe on PATH; the thumbnails and the decisions are made with cutter.review_data of this checkout.
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import random
import secrets
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.dont_write_bytecode = True                      # importing cutter must not leave __pycache__ in the checkout
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "cutter" / "review_app"))
from cutter import review_data  # noqa: E402
import set_password  # noqa: E402

FPS, W, H = 25, 640, 360
PROJECT_TITLE = "Test Pattern Film"
PEOPLE = [{"name": "Reviewer A", "color": "#38bdf8"}, {"name": "Reviewer B", "color": "#f59e0b"}]
AGENT = "Claude"
SECTION_COLORS = ["5b8def", "e0719c", "f0a43c", "3fbf9f", "a78bfa", "f87171"]
MUSIC_TITLES = {"M1": "Test Theme A", "M2": "Test Theme B", "M3": "Test Theme C", "M4": "Test Theme D"}
NOTES = ["colour bars", "moving pattern", "wide shot of the pattern", "close-up of the counter", "static frame",
         "slow pan over the bars", "short insert", "pattern in motion"]
SENTENCES = ["The pattern starts to move now.", "Every frame carries its own number.", "We count the frames one by one.",
             "The tone beeps once every second.", "This shot is only here for the test.", "That is all for this part."]

# The places the tests rely on are written out here; everything else is generated. Times in seconds.
CUTS = {
    "cut4": {
        "title": f"{PROJECT_TITLE} – Cut 4 (first assembly)", "duration": 499.0, "seed": 4,
        "sections": [("Opening", 0.0), ("Arrival", 40.0), ("Talk", 120.0), ("Workshop", 198.0), ("Montage", 300.0),
                     ("Ending", 420.0)],
        # test_app.js: "Cut later" at 64.5 refers to the edit at 64.84, the edit preview at 97.0 loops around the edit
        # at 97.24, the range 200-203.5 spans the edit at 202.0; 5.0 ends the title card, 495.0 starts the black end
        "edits": [5.0, 64.84, 97.24, 202.0, 495.0],
        "quiet": [(62.5, 67.0), (95.5, 99.0), (152.0, 158.5), (196.0, 206.0), (247.0, 253.0), (297.0, 303.0)],
        "dialogue": [((130.0, 150.0), (136.0, 140.0), True), ((160.0, 176.0), (168.0, 171.0), False)],
        "music": [("M1", 0.0, 120.0), ("M2", 120.0, 300.0), ("M3", 300.0, 499.0)],
        "decisions": None,
    },
    "cut5": {
        "title": f"{PROJECT_TITLE} – Cut 5 (tighter middle)", "duration": 480.0, "seed": 5,
        "sections": [("Opening", 0.0), ("Arrival", 40.0), ("Talk", 118.0), ("Workshop", 196.0), ("Montage", 296.0),
                     ("Ending", 410.0)],
        # 126-134: one shot (decision "market" by shot number); 196.0: decision "voice" around this edit;
        # 445-457: one shot for the comment at 451.44 in test_comments.js (the list follows playback from 447)
        "edits": [5.0, 126.0, 134.0, 476.0],
        "quiet": [(124.0, 136.0), (445.0, 457.0), (82.0, 90.0)],
        "dialogue": [((140.0, 158.0), (146.0, 150.0), True), ((176.0, 190.0), (181.0, 184.0), False)],
        "music": [("M1", 0.0, 118.0), ("M2", 118.0, 240.0), ("M3", 240.0, 300.0), ("M4", 300.0, 480.0)],
        "decisions": {
            "title": "Changes and decisions in cut5", "intro": "What changed since cut4 and why.", "compared_to": "cut4",
            "date": "2026-01-15",
            "entries": [
                {"id": "overview", "title": "cut5 at a glance",
                 "text": "Cut5 is 19 seconds shorter than cut4. The middle part moves faster and the talk keeps its subtitles."},
                {"id": "approach", "title": "How this list works",
                 "text": "Every card names a place that changed since cut4. A comment on a card goes straight to the editor.",
                 "question": "Is this list useful, or would a short summary be enough?"},
                {"id": "map", "title": "Map: overview first, then zoom in", "t": 23.1, "t_to": 30.0,
                 "text": "The map now starts with the whole area and zooms in on the route afterwards.",
                 "why": "Viewers asked where the journey takes place before the first close-up.",
                 "refs": [{"person": "Reviewer A", "t": 26.4, "text": "Where does this take place?"}]},
                {"id": "stairs", "title": "Stairs scene shortened", "t": 54.8, "t_to": 66.0,
                 "text": "The walk up the stairs lost four seconds; the cut now comes on the step.",
                 "why": "The scene felt long after the music change.", "question": "Is the new rhythm too abrupt?",
                 "refs": [{"person": "Reviewer B", "t": 58.2, "text": "This drags a little."}]},
                {"id": "focus", "title": "Sharper take for the close-up", "t": 95.0, "t_to": 101.5,
                 "text": "The blurred close-up is replaced by a sharper take of the same moment.",
                 "why": "The old take was out of focus for most of its length."},
                {"id": "market", "title": "Market shot moved before the talk", "nr_at": 130.0,
                 "text": "The market shot now comes before the talk instead of after it.",
                 "why": "It introduces the place where the talk happens.", "question": "Does the new order make sense?"},
                {"id": "colour", "title": "Colour of the evening shots matched", "t": 160.0, "t_to": 168.0,
                 "text": "The evening shots get the same warm grade as the rest of the sequence.",
                 "why": "In cut4 the colours jumped between shots."},
                {"id": "voice", "title": "Voice starts before the cut", "cut_before_at": 196.0,
                 "text": "The voice of the next scene now starts one second before the picture changes.",
                 "why": "A sound bridge makes the transition smoother."},
                {"id": "theme", "title": "New music for the workshop: Test Theme C", "music": "M3",
                 "text": "The workshop gets its own theme instead of continuing the previous track.",
                 "why": "The previous track ended in the middle of the scene.", "question": "Is the new theme too loud?"},
                {"id": "harbour", "title": "Wide harbour shot opens the montage", "t": 330.0, "t_to": 338.0,
                 "text": "A wide shot of the harbour now opens the montage.", "question": "Wide shot or close shot here?"},
                {"id": "crowd", "title": "Crowd noise lowered", "t": 370.0, "t_to": 380.0,
                 "text": "The crowd noise is lowered so that the music carries the montage."},
                {"id": "signs", "title": "Text on the sign subtitled", "t": 400.0, "t_to": 408.0,
                 "text": "The sign in the picture gets a subtitle."},
                {"id": "ending", "title": "Longer last shot", "t": 460.0, "t_to": 470.0,
                 "text": "The last shot stays two seconds longer before the black end card."},
            ],
        },
    },
    "cut6": {
        "title": f"{PROJECT_TITLE} – Cut 6 (new ending)", "duration": 460.0, "seed": 6,
        "sections": [("Opening", 0.0), ("Arrival", 40.0), ("Talk", 116.0), ("Workshop", 190.0), ("Montage", 290.0),
                     ("Ending", 400.0)],
        "edits": [5.0, 456.0],
        "quiet": [],
        "dialogue": [((136.0, 154.0), (142.0, 146.0), True), ((168.0, 182.0), (173.0, 176.0), False)],
        "music": [("M1", 0.0, 116.0), ("M2", 116.0, 290.0), ("M4", 290.0, 460.0)],
        "decisions": {
            "title": "Changes and decisions in cut6", "intro": "What changed since cut5 and why.", "compared_to": "cut5",
            "date": "2026-02-01",
            "entries": [
                {"id": "overview", "title": "cut6 at a glance",
                 "text": "Cut6 is 20 seconds shorter than cut5 and has a new ending."},
                {"id": "levels", "title": "Sound levels evened out",
                 "text": "All dialogue now has the same level; the music under dialogue is lowered evenly.",
                 "question": "Should the music be quieter overall?"},
                {"id": "map", "title": "Map unchanged", "t": 23.1, "t_to": 30.0,
                 "text": "The map stays as in cut5; only its music starts earlier."},
                {"id": "pace", "title": "Faster pace in the arrival", "t": 79.9, "t_to": 88.0,
                 "text": "Three shots of the arrival are shorter, so the scene now follows the beat of the music.",
                 "why": "The arrival felt slow compared with the rest of the film.",
                 "refs": [{"person": "Reviewer B", "t": 84.3, "text": "Could this be quicker?"}]},
                {"id": "bridge", "title": "Bridge between workshop and talk", "t": 226.0, "t_to": 236.0,
                 "text": "A short shot of the counter now links the workshop to the talk.",
                 "question": "Is the transition clear now?"},
                {"id": "horizon", "title": "Horizon straightened", "t": 296.0, "t_to": 306.0,
                 "text": "The tilted horizon of the wide shot is corrected."},
                {"id": "sunset", "title": "Sunset moved to the end of the montage", "t": 350.0, "t_to": 362.0,
                 "text": "The sunset now closes the montage instead of opening it.",
                 "question": "Does the sunset belong here?"},
                {"id": "silence", "title": "Two seconds of silence before the ending", "t": 410.0, "t_to": 418.0,
                 "text": "The music stops two seconds before the last shot.",
                 "why": "The quiet makes the ending stand out."},
            ],
        },
    },
}


def q(t):
    """Round to whole frames."""
    return round(round(t * FPS) / FPS, 3)


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


# ---------------------------------------------------------------- timeline

def make_shots(name, spec):
    d = spec["duration"]
    fixed = {q(x) for x in spec["edits"]} | {q(start) for _, start in spec["sections"][1:]}
    for (a, b), _, _ in spec["dialogue"]:
        fixed |= {q(a), q(b)}
    quiet = list(spec["quiet"]) + [rng for rng, _, _ in spec["dialogue"]]
    rnd, cuts, t = random.Random(spec["seed"]), set(fixed), 0.0
    while True:
        t = q(t + rnd.uniform(3.0, 8.0))
        if t > d - 4.0:
            break
        if any(abs(t - f) < 1.5 for f in fixed) or any(a - 0.5 < t < b + 0.5 for a, b in quiet):
            continue
        cuts.add(t)
    bounds = [0.0] + sorted(c for c in cuts if 0.0 < c < d) + [d]
    dialogue = {q(rng[0]): (rng, cw, subtitled) for rng, cw, subtitled in spec["dialogue"]}

    def section_at(x):
        return [n for n, start in spec["sections"] if start <= x + 1e-6][-1]
    shots = []
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        nr = i + 1
        s = {"nr": nr, "start": a, "end": b, "section": section_at(a), "kind": "picture",
             "clip": f"C{(nr * 7) % 24 + 1:03d}", "file": None, "source_in": None, "source_out": None,
             "note": NOTES[nr % len(NOTES)], "block": None, "nr_previous": None, "tag": None, "audio_db": -10.0,
             "music_lower": False, "cutaways": [], "subtitles": [], "sentences": [], "text": ""}
        if i == 0 or i == len(bounds) - 2:
            s.update(kind="title" if i == 0 else "black", clip="TXT", audio_db=None,
                     note="title card" if i == 0 else "black with the end card")
        else:
            if a in dialogue:
                s.update(kind="dialogue", audio_db=None, note="the tone that speaks")
            elif nr % 4 == 0:
                s["kind"] = "ambience"
            s["file"] = f"clip_{int(s['clip'][1:]):03d}.mp4"
            s["source_in"] = q(1.0 + (nr * 3.7) % 30.0)
            s["source_out"] = round(s["source_in"] + (b - a), 3)
        if a in dialogue:
            _, (ca, cb), subtitled = dialogue[a]
            k = (nr % 3) * 2
            parts = [(a + 0.5, a + (b - a) * 0.3), (a + (b - a) * 0.35, a + (b - a) * 0.65), (a + (b - a) * 0.7, b - 0.8)]
            s["sentences"] = [{"start": round(x, 2), "end": round(y, 2), "text": SENTENCES[(k + j) % len(SENTENCES)]}
                              for j, (x, y) in enumerate(parts)]
            s["text"] = " ".join(x["text"] for x in s["sentences"])
            if subtitled:
                s["subtitles"] = [dict(x) for x in s["sentences"]]
            s["cutaways"] = [{"start": q(ca), "end": q(cb), "clip": "C030", "file": "clip_030.mp4", "source_in": 4.0,
                              "source_out": round(4.0 + cb - ca, 3), "note": "cutaway to the counter"}]
        shots.append(s)
    return shots


def make_timeline(name, spec):
    shots = make_shots(name, spec)
    edits = [{"t": shots[i]["start"], "before": shots[i - 1]["nr"], "after": shots[i]["nr"], "kind": "shot"}
             for i in range(1, len(shots))]
    for s in shots:
        for e in s["cutaways"]:
            if e["start"] > s["start"] + 0.02:
                edits.append({"t": e["start"], "before": s["nr"], "after": s["nr"], "kind": "cutaway_in"})
            if e["end"] < s["end"] - 0.02:
                edits.append({"t": e["end"], "before": s["nr"], "after": s["nr"], "kind": "cutaway_out"})
    edits.sort(key=lambda c: c["t"])
    sections = []
    for s in shots:
        if sections and sections[-1]["name"] == s["section"]:
            sections[-1]["end"] = s["end"]
        else:
            sections.append({"name": s["section"], "start": s["start"], "end": s["end"]})
    music = [{"cue": cue, "title": MUSIC_TITLES[cue], "artist": "Synthetic Ensemble", "license": "CC0 (generated)",
              "start": a, "end": b, "source_start": 0.0, "bpm": 120, "note": "generated test tone",
              "file": f"{cue.lower()}_test_tone.wav", "fade_out_s": 2.0} for cue, a, b in spec["music"]]
    return {"format": 2, "version": name, "title": spec["title"], "fps": FPS, "duration": spec["duration"],
            "sections": sections, "shots": shots, "edit_points": edits, "music": music,
            "subtitles": sorted((u for s in shots for u in s["subtitles"]), key=lambda u: u["start"]),
            "markers": [{"name": f"{x['name']} starts", "t": x["start"], "duration": 0.0} for x in sections[1:]],
            "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "source": {"edit": f"edit_{name}.json", "otio": f"{name}.otio",
                       "video": "synthetic test pattern (tests/browser/make_fixture.py)", "video_kind": "rough"}}


def shot_at(tl, x):
    return [s for s in tl["shots"] if s["start"] <= x + 1e-6][-1]


def add_decisions(name, spec, tl, work):
    """The agent's change notes, written like decisions_<cut>.json and converted with review_data.decisions()."""
    d = json.loads(json.dumps(spec["decisions"]))
    for e in d["entries"]:
        if "nr_at" in e:
            e["nr"] = shot_at(tl, e.pop("nr_at"))["nr"]
        if "cut_before_at" in e:
            x = e.pop("cut_before_at")
            e["cut_before"] = next(s["nr"] for s in tl["shots"] if abs(s["start"] - x) < 1e-6)
        for r in e.get("refs", []):
            r.setdefault("version", d["compared_to"])
    path = work / f"decisions_{name}.json"
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tl["decisions"] = review_data.decisions(path, tl)
    tl["source"]["decisions"] = path.name


def notes_at(tl, x):
    entries = (tl.get("decisions") or {}).get("entries", [])
    return [e["id"] for e in entries if e["t"] is not None and e["t"] - 1e-3 <= x < (e["t_to"] if e["t_to"] is not None else e["t"] + 2)]


def nearest_edit(tl, x):
    return min(tl["edit_points"], key=lambda c: abs(c["t"] - x))


def check_assumptions(tls):
    """What the browser tests take for granted about these timelines (fails here rather than in the browser)."""
    c4, c5, c6 = tls["cut4"], tls["cut5"], tls["cut6"]
    problems = []

    def need(cond, text):
        if not cond:
            problems.append(text)
    # test_app.js
    need(nearest_edit(c4, 64.5)["t"] == 64.84 and nearest_edit(c4, 64.5)["kind"] == "shot", "cut4: nearest edit to 64.5 is 64.84")
    need(nearest_edit(c4, 97.0)["t"] == 97.24, "cut4: nearest edit to 97.0 is 97.24")
    need(shot_at(c4, 200.0)["nr"] != shot_at(c4, 203.5)["nr"], "cut4: the range 200-203.5 spans an edit")
    need(shot_at(c4, 200.0)["source_in"] is not None, "cut4: picture shot at 200")
    need(shot_at(c4, 155.0)["start"] < 154.0 and shot_at(c4, 155.0)["end"] > 156.5, "cut4: 155 well inside a shot")
    need(not c4.get("decisions"), "cut4: no decisions")
    # test_comments.js and test_decisions.js
    d5 = {e["id"]: e for e in c5["decisions"]["entries"]}
    d6 = {e["id"]: e for e in c6["decisions"]["entries"]}
    need(len(d5) == 13 and sum(1 for e in d5.values() if e["t"] is None) == 2, "cut5: 13 decisions, 2 general")
    need(sum(1 for e in d5.values() if e["question"]) == 5, "cut5: 5 questions")
    for tl, dd in ((c5, d5), (c6, d6)):
        first = [e for e in tl["decisions"]["entries"] if e["t"] is not None][0]
        need(first["id"] == "map" and first["t"] == 23.1, f"{tl['version']}: first timed decision 'map' at 23.1")
        need(not notes_at(tl, 0.0) and not notes_at(tl, 10.0) and not notes_at(tl, 15.0), f"{tl['version']}: nothing at 0-15 s")
    need(notes_at(c5, 60.0) == ["stairs"] and d5["stairs"]["t"] == 54.8, "cut5: 'stairs' at 54.8 covers 60")
    need(notes_at(c5, 98.0) == ["focus"] and notes_at(c5, 130.0) == ["market"], "cut5: 'focus' at 98, 'market' at 130")
    need(notes_at(c5, 250.0) == ["theme"] and notes_at(c5, 260.0) == ["theme"], "cut5: 'theme' (M3) covers 250 and 260")
    need(not any(notes_at(c5, x / 10) for x in range(4400, 4560)), "cut5: no decision at 440-456")
    need(not any(notes_at(c5, x / 10) for x in range(840, 900)), "cut5: no decision at 84-90")
    s447 = shot_at(c5, 447.0)
    need(s447["start"] <= 445.0 and s447["end"] >= 457.0, "cut5: one shot from before 445 to after 457")
    need(all(d5[k]["why"] for k in ("market", "map")), "cut5: 'market' and 'map' have a why")
    need(len(d6) == 8 and d6["pace"]["t"] == 79.9 and len(d6["pace"]["text"]) >= 40, "cut6: 8 decisions, 'pace' at 79.9")
    need(notes_at(c6, 230.0) == ["bridge"] and notes_at(c6, 300.0) == ["horizon"], "cut6: 'bridge' at 230, 'horizon' at 300")
    need(notes_at(c6, 80.9) == ["pace"] and d6["silence"]["why"], "cut6: 'pace' at 80.9, 'silence' has a why")
    if problems:
        sys.exit("make_fixture: the timelines no longer fit the tests:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- video

def render(name, tl, folder, work):
    """review.mp4: testsrc2 with a band in the colour of the section, the shot label, a frame counter and a tone."""
    colors = {s["name"]: SECTION_COLORS[i % len(SECTION_COLORS)] for i, s in enumerate(tl["sections"])}
    cmds = work / f"{name}_labels.txt"
    cmds.write_text("".join(f"{s['start']:.3f} drawtext@shot reinit 'text={name} · shot {s['nr']} · {s['section']}', "
                            f"drawbox@band color 0x{colors[s['section']]}@0.85;\n" for s in tl["shots"]), encoding="utf-8")
    font = f"fontfile={review_data.FONT}:" if review_data.FONT else ""
    graph = (f"[0:v]format=yuv420p,sendcmd=f={cmds.name},"
             f"drawbox@band=x=0:y=ih-52:w=iw:h=52:color=0x777777@0.85:t=fill,"
             f"drawtext@shot={font}text='{name}':x=14:y=h-40:fontsize=22:fontcolor=white,"
             f"drawtext={font}text='%{{frame_num}}':x=w-tw-14:y=h-43:fontsize=30:fontcolor=white[v]")
    video = folder / "review.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}",
         "-f", "lavfi", "-i", "sine=frequency=440:beep_factor=2:sample_rate=48000", "-t", f"{tl['duration']:.3f}",
         "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "superfast", "-crf", 40,
         "-g", 2 * FPS, "-c:a", "aac", "-b:a", "48k", "-ac", 1, "-movflags", "+faststart", video], cwd=work)
    cmds.unlink()
    p = review_data.probe(video)
    count = review_data.thumbnails(video, folder, p["duration"])
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{min(35.0, p['duration'] / 2):.2f}", "-i", video,
         "-frames:v", 1, "-q:v", 3, folder / "poster.jpg"])
    tl["video"] = {"file": "review.mp4", "width": p["width"], "height": p["height"], "fps": round(p["fps"], 3),
                   "duration": round(p["duration"], 3), "numbers_burned_in": True}
    tl["thumbnails"] = {"vtt": "thumbs.vtt", "interval": review_data.THUMB_STEP, "width": review_data.THUMB_W,
                        "height": review_data.THUMB_H, "count": count}
    tl["poster"] = "poster.jpg"
    (folder / "timeline.json").write_text(json.dumps(tl, ensure_ascii=False, indent=1), encoding="utf-8")
    return p["duration"]


# ---------------------------------------------------------------- project, login

def write_private(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o600)


def write_project(data, ui_language):
    (data / "project.json").write_text(json.dumps({"title": PROJECT_TITLE, "people": PEOPLE, "agent": AGENT,
                                                   "ui_language": ui_language}, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Synthetic test data for the browser tests of the review app")
    ap.add_argument("folder", help="target folder (created; existing cuts in it are replaced)")
    ap.add_argument("--ui-language", default="en", choices=["en", "de"], help="ui_language in project.json")
    ap.add_argument("--login", default="tester", help="login name in the htpasswd file")
    a = ap.parse_args()
    root = pathlib.Path(a.folder).resolve()
    data, work = root / "data", root / "work"
    for p in (data / "cuts", work):
        p.mkdir(parents=True, exist_ok=True)

    tls = {name: make_timeline(name, spec) for name, spec in CUTS.items()}
    for name, spec in CUTS.items():
        if spec["decisions"]:
            add_decisions(name, spec, tls[name], work)
    check_assumptions(tls)

    def one(name):
        folder = data / "cuts" / name
        folder.mkdir(parents=True, exist_ok=True)
        return render(name, tls[name], folder, work)
    with ThreadPoolExecutor(max_workers=len(CUTS)) as ex:
        durations = dict(zip(CUTS, ex.map(one, CUTS)))

    write_project(data, a.ui_language)
    password = secrets.token_urlsafe(18)
    write_private(root / "password.txt", password + "\n")
    write_private(root / "htpasswd", f"{a.login}:{set_password.new_hash(password)}\n")
    for p in work.iterdir():
        p.unlink()
    work.rmdir()
    for name, tl in tls.items():
        n = len((tl.get("decisions") or {}).get("entries", []))
        print(f"{name}: {durations[name]:.2f} s, {len(tl['shots'])} shots, {len(tl['edit_points'])} edit points, "
              f"{len(tl['music'])} music cues, {n} decisions")
    print(f"Test data in {data} (login {a.login}, password in {root / 'password.txt'})")


if __name__ == "__main__":
    main()
