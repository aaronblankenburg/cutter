"""Local golden tests against a real earlier project, read through the reader for German-keyed files.

They run only when the git-ignored manifest tests/local/reference.json exists (paths only, see tests/local/README.md);
otherwise they are skipped. Nothing in the referenced folders is written: all output goes to a temporary folder.
  - timeline: OTIO, FCP 7 XML and SRT from the original edit list, byte for byte as the original pipeline wrote them
  - chain: plan -> retime -> audio levels gives the final edit list of that cut (compared after key mapping)
  - review timeline.json: equal to the original after key mapping (creation time and file locations aside)
  - FilmCraft: a headless run on a copy of the base project gives the original batch (ids renumbered)
"""
import json
import os
import pathlib
import shutil
import unittest

from helpers import FFMPEG, TESTS, TOOLS, TempDir

MANIFEST = TESTS / "local" / "reference.json"
M = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else None


def need(*keys):
    missing = [k for k in keys if not M or not M.get(k)]
    return unittest.skipIf(missing, f"local reference: {', '.join(missing)} not in tests/local/reference.json")


@unittest.skipUnless(M, "no tests/local/reference.json (local golden tests are optional)")
@unittest.skipUnless(TOOLS and FFMPEG, "needs numpy, OpenTimelineIO and ffmpeg")
class LocalReference(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cutter.config import Project
        if not pathlib.Path(M["project"]).is_dir():
            raise unittest.SkipTest(f"the project folder of tests/local/reference.json is not reachable: {M['project']}")
        cls.p = Project.load(M["project"], required=True)
        cls.tmp = TempDir()
        cls.out = cls.tmp.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.__exit__(None, None, None)

    def build(self, edit, name, suffix=".fcp7.xml"):
        from cutter import timeline
        p, t = self.p, self.p["timeline"]
        return timeline.build(edit, p.path("clips"), p.media_base, self.out / name, ns=p.otio_namespace, music_dirs=p.music_dirs(),
                              config_external=t["external_clips"], track_names=t["track_names"], texts=t["texts"],
                              media_roots=p.media_roots(), xml_suffix=suffix, project_dir=p.folder)

    @need("edit", "reference_timeline")
    def test_timeline_is_byte_identical(self):
        self.build(M["edit"], "F", ".fcp7.xml")
        ref = M["reference_timeline"]
        for ext, key in ((".otio", "otio"), (".fcp7.xml", "xml"), (".srt", "srt")):
            with self.subTest(file=ext):
                self.assertEqual((self.out / f"F{ext}").read_bytes(), pathlib.Path(ref[key]).read_bytes())

    @need("edit", "original_files")
    def test_timeline_against_the_files_of_that_time(self):
        """XML and SRT equal the files written back then; the OTIO differs only where the edit list was patched later."""
        self.build(M["edit"], "G", ".fcp7.xml")
        orig = M["original_files"]
        self.assertEqual((self.out / "G.fcp7.xml").read_bytes(), pathlib.Path(orig["xml"]).read_bytes())
        self.assertEqual((self.out / "G.srt").read_bytes(), pathlib.Path(orig["srt"]).read_bytes())
        a = (self.out / "G.otio").read_text(encoding="utf-8").splitlines()
        b = pathlib.Path(orig["otio"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(a), len(b))
        diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        self.assertEqual(len(diff), M.get("known_otio_differences", 0), [a[i][:160] for i in diff[:5]])

    @need("edit", "edit_plan")
    def test_chain_reproduces_the_final_edit_list(self):
        from cutter import retime, timeline
        from cutter.legacy import edit_from_legacy
        from cutter.util import read_json
        p = self.p
        plan, _ = timeline.load_edit(M["edit_plan"])
        clips = {c["id"]: c for c in read_json(p.path("clips"))}
        E, log, Q, total = retime.align(plan, clips, read_json(p.path("music_analysis")), p["retime"], p.path("image_analysis"),
                                        p.external_clip_ids(plan))
        self.assertFalse([L for L in log if "error" in L])
        retimed = self.out / "retimed.json"
        retimed.write_text(json.dumps(E, ensure_ascii=False, indent=1), encoding="utf-8")
        t = p["timeline"]
        final = self.out / "final.json"
        timeline.assemble(retimed, p.path("clips"), p.media_base, self.out / "intermediate", final, ns=p.otio_namespace,
                          music_dirs=p.music_dirs(), config_external=t["external_clips"], track_names=t["track_names"],
                          texts=t["texts"], audio=p["audio"], external_ids=p.external_clip_ids(), media_roots=p.media_roots(),
                          project_dir=p.folder)
        got = json.loads(final.read_text(encoding="utf-8"))
        want = edit_from_legacy(read_json(M["edit"]))[0]
        self.assertEqual(_diff(got, want), [])

    @need("edit", "review_timeline", "transcripts")
    def test_review_timeline_matches_after_key_mapping(self):
        from cutter import review_data
        from cutter.legacy import timeline_from_legacy
        from cutter.util import read_json
        self.build(M["edit"], "R", ".fcp7.xml")
        p = self.p
        orig = timeline_from_legacy(read_json(M["review_timeline"]))
        cut = orig.get("version") or "F7"
        review_data.create(cut, pathlib.Path(M["edit"]), self.out / "R.otio", self.out / "review", pathlib.Path(M["transcripts"]),
                           decisions_path=pathlib.Path(M["decisions"]) if M.get("decisions") else None, timeline_only=True,
                           ns=p.otio_namespace, external=p.external_clip_ids(), fade_out=p["music"]["fade_out_s"],
                           levels={"base": p["music"]["base_lufs"], "under": p["music"]["under_dialogue_lufs"],
                                   "low": p["music"]["low_lufs"]})
        got = read_json(self.out / "review" / cut / "timeline.json")
        ignore = {"created", "source", "video", "thumbnails", "poster", "format"}
        self.assertEqual(_diff({k: v for k, v in got.items() if k not in ignore},
                               {k: v for k, v in orig.items() if k not in ignore}), [])

    @need("edit", "filmcraft_config", "filmcraft_base", "filmcraft_batch", "original_files")
    def test_filmcraft_batch_from_a_headless_run(self):
        from cutter import filmcraft
        from cutter.util import read_json
        cli = M.get("filmcraft_cli") or filmcraft.find_cli()
        if not cli or not os.access(cli, os.X_OK):
            self.skipTest("filmcraft-cli not found")
        p = self.p
        base = self.out / "base.fcproj"
        shutil.copyfile(M["filmcraft_base"], base)
        client = filmcraft.CliClient(cli, base=base, target=self.out / "finished.fcproj", data_dir=self.out / "fc-data")
        fc = filmcraft.load_config(M["filmcraft_config"])
        r = filmcraft.finish(client, fc, read_json(M["edit"]), M["original_files"]["xml"], M["original_files"]["srt"],
                             self.out / "batch.jsonl", base=p.media_base, music_analysis=read_json(p.path("music_analysis")),
                             retime_cues=p["retime"].get("cues"))
        self.assertTrue(any(" 0 failed" in line for line in r["log"]), r["log"])
        got, want = _batch(self.out / "batch.jsonl"), _batch(M["filmcraft_batch"])
        self.assertEqual(len(got), len(want))
        diff = [(i, a, b) for i, (a, b) in enumerate(zip(got, want)) if a != b]
        self.assertEqual(diff, [])


def _batch(path):
    """Batch lines with clip and marker ids renumbered in order of appearance and effect stack positions left out:
    both depend on the state of the project, not on cutter."""
    ids, out = {}, []
    num = lambda x: "#%d" % ids.setdefault(x, len(ids))
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        q = d.get("params", {})
        for k in ("clip", "marker"):
            if isinstance(q.get(k), int):
                q[k] = num((k, q[k]))
        if isinstance(q.get("clips"), list):
            q["clips"] = [num(("clip", x)) if isinstance(x, int) else x for x in q["clips"]]
        if isinstance(q.get("effect"), int):
            q["effect"] = "<position>"
        out.append(json.dumps(d, sort_keys=True))
    return out


def _diff(a, b, path="", out=None, tol=1e-6):
    """Differences between two JSON values (floats with a tolerance), as a list of short texts."""
    out = [] if out is None else out
    if len(out) > 20:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: only in {'got' if k in a else 'expected'}")
            else:
                _diff(a[k], b[k], f"{path}/{k}", out, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: {len(a)} items, expected {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, f"{path}[{i}]", out, tol)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if abs(a - b) > tol:
            out.append(f"{path}: {a} != {b}")
    elif a != b:
        out.append(f"{path}: {str(a)[:80]!r} != {str(b)[:80]!r}")
    return out


if __name__ == "__main__":
    unittest.main()
