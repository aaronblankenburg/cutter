"""cutter.toml, named footage roots, the reader for files of the earlier German-keyed pipeline, and cutter migrate."""
import json
import pathlib
import unittest

from helpers import TempDir, run_cutter
from cutter import legacy
from cutter.config import Project, cut_name, previous_cut


class Config(unittest.TestCase):
    def test_defaults_and_media_roots(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text('title = "T"\n[media]\ncam = "/footage/cam"\nphone = "~/phone"\n'
                                           '[music]\nallowed_licenses = ["CC0", "CC BY", "CC BY-NC"]\n', encoding="utf-8")
            p = Project.load(str(d), required=True)
            self.assertEqual(p.media_roots()["cam"], "/footage/cam")
            self.assertFalse(p.media_roots()["phone"].startswith("~"))
            self.assertEqual(p["music"]["allowed_licenses"], ["CC0", "CC BY", "CC BY-NC"])
            self.assertEqual(p.path("edit", "3"), d / "cuts" / "cut3" / "edit.json")
            self.assertEqual(p.path("htpasswd"), d / ".cutter" / "review" / "htpasswd")
            self.assertEqual(p.ui_language(), "")

    def test_default_music_licenses(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text("", encoding="utf-8")
            self.assertEqual(Project.load(str(d))["music"]["allowed_licenses"], ["CC0", "CC BY"])

    def test_project_is_found_from_a_subfolder(self):
        with TempDir() as d:
            (d / "cutter.toml").write_text('title = "Up"\n', encoding="utf-8")
            (d / "cuts" / "cut1").mkdir(parents=True)
            import os
            old = os.getcwd()
            try:
                os.chdir(d / "cuts" / "cut1")
                self.assertEqual(Project.load().folder, d)
            finally:
                os.chdir(old)

    def test_cut_names(self):
        self.assertEqual(cut_name("3"), "cut3")
        self.assertEqual(cut_name("cut12"), "cut12")
        self.assertEqual(previous_cut("cut3"), "cut2")
        self.assertIsNone(previous_cut("cut1"))

    def test_legacy_config_keys(self):
        with TempDir() as d:
            (d / "cutter.json").write_text(json.dumps({"titel": "Alt", "material": ["/footage"], "musik": {"fade_out_s": {"M1": 2}}}),
                                           encoding="utf-8")
            p = Project.load(str(d))
            self.assertEqual(p["title"], "Alt")
            self.assertEqual(list(p.media_roots().values()), ["/footage"])


LEGACY_EDIT = {
    "titel": "Alt", "fps": 25,
    "sections": [{"name": "Anfang", "shots": [
        {"nr": 1, "clip": "C001", "in": 1.0, "out": 3.0, "art": "dialog", "notiz": "erste",
         "ton": {"kette": "sanft", "hochpass_hz": 90}, "untertitel": [{"von": 0.2, "bis": 1.5, "text": "Hallo"}],
         "einschnitte": [{"clip": "C002", "in": 0.0, "out": 1.0, "ab": 0.5}]},
        {"nr": 2, "clip": "TXT", "in": 0, "out": 2, "art": "schwarz", "notiz": "Ende"}]}],
    "musik": [{"cue": "M1", "titel": "Song", "kuenstler": "X", "lizenz": "CC BY 4.0", "von": {"abschnitt": "Anfang", "kante": "start"},
               "bis": {"ende": True}, "quelle_start": 0.0}],
}


class Legacy(unittest.TestCase):
    def test_edit_from_legacy(self):
        e, was = legacy.edit_from_legacy(LEGACY_EDIT)
        self.assertTrue(was)
        s = e["sections"][0]["shots"][0]
        self.assertEqual(s["kind"], "dialogue")
        self.assertEqual(s["audio"], {"chain": "gentle", "highpass_hz": 90})
        self.assertEqual(s["subtitles"][0], {"from": 0.2, "to": 1.5, "text": "Hallo"})
        self.assertEqual(s["cutaways"][0]["at"], 0.5)
        self.assertEqual(e["sections"][0]["shots"][1]["kind"], "black")
        m = e["music"][0]
        self.assertEqual((m["title"], m["artist"], m["license"]), ("Song", "X", "CC BY 4.0"))
        self.assertEqual(m["from"], {"section": "Anfang", "edge": "start"})

    def test_english_edit_is_unchanged(self):
        e, _ = legacy.edit_from_legacy(LEGACY_EDIT)
        again, was = legacy.edit_from_legacy(e)
        self.assertFalse(was)
        self.assertEqual(again, e)

    def test_detect_and_migrate(self):
        self.assertEqual(legacy.detect(LEGACY_EDIT), "edit")
        self.assertEqual(legacy.detect({"fassung": "F7", "eintraege": []}), "decisions")
        self.assertEqual(legacy.detect([{"id": "C001", "created_source": "dateiname"}]), "clips")
        self.assertIsNone(legacy.detect([{"id": "C001", "created_source": "filename"}]))
        new, kind = legacy.migrate({"fassung": "F7", "titel": "T", "eintraege": [{"id": "a", "titel": "x", "warum": "y"}]})
        self.assertEqual(kind, "decisions")
        self.assertEqual(new["version"], "F7")
        self.assertEqual(new["entries"][0]["why"], "y")

    def test_feedback_from_legacy(self):
        d = legacy.feedback_from_legacy({"fassung": "F7", "eintraege": [{"id": 1, "aktion": "kuerzer", "status": "offen",
                                                                        "antwort": None, "t": 3.0}],
                                         "anzahl": {"gesamt": 1}})
        self.assertEqual(d["cut"], "F7")
        self.assertEqual(d["entries"][0]["action"], "shorter")
        self.assertEqual(d["entries"][0]["status"], "open")

    def test_cutter_migrate_writes_a_backup(self):
        with TempDir() as d:
            f = d / "edit_old.json"
            f.write_text(json.dumps(LEGACY_EDIT), encoding="utf-8")
            r = run_cutter("migrate", f, "--json")
            out = json.loads(r.stdout)
            self.assertEqual(out["files"][0]["kind"], "edit")
            self.assertTrue((d / "edit_old.json.bak").exists())
            self.assertEqual(json.loads(f.read_text())["sections"][0]["shots"][0]["kind"], "dialogue")
            r = run_cutter("migrate", f, "--json")
            self.assertFalse(json.loads(r.stdout)["files"][0]["changed"])


if __name__ == "__main__":
    unittest.main()
