"""The public golden pipeline: the synthetic demo from a fixed analysis to timelines, review data and FilmCraft batch.

Inputs (tests/golden/demo/input): cutter.toml with the footage root /demo/footage (it does not need to exist),
clips.json and music.json as measured once from the generated demo footage. The edit list, decisions and FilmCraft
settings come from cutter.demo; the click track is generated (deterministic).
Expected results (tests/golden/demo/expected) are compared exactly after replacing the temporary project folder with
/demo/film. After an intended change regenerate them with:  python tests/golden_demo.py --update
"""
import contextlib
import io
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from helpers import GOLDEN  # noqa: E402

DEMO = GOLDEN / "demo"
PROJECT_NAME = "/demo/film"
OUTPUTS = {  # expected file name: path inside the project
    "edit.json": "cuts/cut1/edit.json",
    "retime_log.json": "cuts/cut1/work/retime_log.json",
    "cut1.otio": "cuts/cut1/cut1.otio",
    "cut1.fcp7.xml": "cuts/cut1/cut1.fcp7.xml",
    "cut1.srt": "cuts/cut1/cut1.srt",
    "timeline.json": ".cutter/review/data/cuts/cut1/timeline.json",
    "filmcraft_batch.jsonl": "cuts/cut1/work/filmcraft_batch.jsonl",
}


def setup_project(folder, media_root="/demo/footage"):
    """A demo project in folder (film/) from the golden inputs; returns its path."""
    from cutter import demo
    film = pathlib.Path(folder) / "film"
    for sub in ("analysis", "music", "cuts/cut1", ".cutter/review"):
        (film / sub).mkdir(parents=True, exist_ok=True)
    toml = (DEMO / "input" / "cutter.toml").read_text(encoding="utf-8").replace("/demo/footage", media_root)
    (film / "cutter.toml").write_text(toml, encoding="utf-8")
    shutil.copyfile(DEMO / "input" / "clips.json", film / "analysis" / "clips.json")
    shutil.copyfile(DEMO / "input" / "music.json", film / "music" / "music.json")
    demo.click_track(film / "music" / demo.MUSIC_FILE)
    (film / "cuts/cut1/edit.json").write_text(json.dumps(demo.edit_list("cut1"), ensure_ascii=False, indent=1), encoding="utf-8")
    (film / "cuts/cut1/decisions.json").write_text(json.dumps(demo.decisions("cut1"), ensure_ascii=False, indent=1), encoding="utf-8")
    (film / "filmcraft.json").write_text(json.dumps(demo.FILMCRAFT, ensure_ascii=False, indent=1), encoding="utf-8")
    return film


def cli(*args):
    """Run a cutter command in this process; returns its JSON result."""
    from cutter import cli as c
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        c.main([*map(str, args), "--json"])
    return json.loads(buf.getvalue())


def run_pipeline(film):
    """retime -> timeline -> review-data (timeline only) -> FilmCraft batch (dry run with the stand-in)."""
    film = pathlib.Path(film)
    results = {"retime": cli("retime", "cut1", "--project", film),
               "timeline": cli("timeline", "cut1", "--project", film),
               "review": cli("review-data", "cut1", "--timeline-only", "--project", film),
               "filmcraft": cli("filmcraft", "build", "cut1", "--dry-run", "--project", film)}
    return results


def normalized(film, name):
    """An output as text with the temporary project folder replaced; timeline.json without its creation time."""
    text = (pathlib.Path(film) / OUTPUTS[name]).read_text(encoding="utf-8")
    text = text.replace(str(pathlib.Path(film)), PROJECT_NAME)
    if name == "timeline.json":
        data = json.loads(text)
        data.pop("created", None)
        text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    return text


def update():
    import tempfile
    with tempfile.TemporaryDirectory(prefix="cutter_golden_") as tmp:
        film = setup_project(tmp)
        run_pipeline(film)
        target = DEMO / "expected"
        target.mkdir(parents=True, exist_ok=True)
        for name in OUTPUTS:
            (target / name).write_text(normalized(film, name), encoding="utf-8")
            print(f"updated {target / name}")


if __name__ == "__main__":
    if "--update" not in sys.argv:
        sys.exit("usage: python tests/golden_demo.py --update   (regenerates tests/golden/demo/expected)")
    update()
