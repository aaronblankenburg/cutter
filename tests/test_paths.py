"""Paths and file URLs on the editing computer (paths.py)."""
import unicodedata
import unittest

from cutter import paths
from cutter.paths import PathError

WIN = {"os": "windows", "root": "D:\\Videos\\Island 2026"}
MAC = {"os": "macos", "root": "/Volumes/Karte A/Island 2026"}
LIN = {"os": "linux", "root": "/home/me/Footage"}
NFD_E = unicodedata.normalize("NFD", "é")


class Join(unittest.TestCase):
    def test_windows(self):
        self.assertEqual(paths.join(WIN, "Tag 3/IMG_4821.MOV"), "D:\\Videos\\Island 2026\\Tag 3\\IMG_4821.MOV")

    def test_windows_root_with_trailing_or_forward_slashes(self):
        self.assertEqual(paths.join({"os": "windows", "root": "d:/Videos/"}, "a.mov"), "D:\\Videos\\a.mov")
        self.assertEqual(paths.join({"os": "windows", "root": "E:\\"}, "a.mov"), "E:\\a.mov")

    def test_unc(self):
        t = {"os": "windows", "root": "\\\\nas\\film\\Island"}
        self.assertEqual(paths.join(t, "Tag 1/a.mov"), "\\\\nas\\film\\Island\\Tag 1\\a.mov")
        self.assertEqual(paths.url(t, "Tag 1/a.mov"), "file://nas/film/Island/Tag%201/a.mov")

    def test_macos_and_linux(self):
        self.assertEqual(paths.join(MAC, "Tag 3/a.mov"), "/Volumes/Karte A/Island 2026/Tag 3/a.mov")
        self.assertEqual(paths.join(LIN, "a.mov"), "/home/me/Footage/a.mov")

    def test_package_files(self):
        self.assertEqual(paths.join(WIN, "music/song.mp3", under_package=True),
                         "D:\\Videos\\Island 2026\\_cutlery\\music\\song.mp3")
        t = dict(MAC, package="/Users/me/Export")
        self.assertEqual(paths.join(t, "titles/t01.png", under_package=True), "/Users/me/Export/titles/t01.png")

    def test_refuses_paths_outside_the_folder(self):
        for bad in ("../x.mov", "a/../../x.mov", "", "/"):
            with self.assertRaises(PathError):
                paths.join(WIN, bad)

    def test_checks_the_root_for_the_system(self):
        for t in ({"os": "windows", "root": "/Volumes/x"}, {"os": "macos", "root": "D:\\x"}, {"os": "linux", "root": "x"},
                  {"os": "amiga", "root": "/x"}, {"os": "macos", "root": ""}):
            with self.assertRaises(PathError):
                paths.check_target(t)


class Urls(unittest.TestCase):
    def test_windows_drive(self):
        self.assertEqual(paths.url(WIN, "Tag 3/IMG 4821 Gletscher.MOV"),
                         "file://localhost/D:/Videos/Island%202026/Tag%203/IMG%204821%20Gletscher.MOV")
        self.assertEqual(paths.url(WIN, "a.mov", colon="%3a"), "file://localhost/D%3a/Videos/Island%202026/a.mov")

    def test_macos(self):
        self.assertEqual(paths.url(MAC, "Tag 3/a.mov"), "file://localhost/Volumes/Karte%20A/Island%202026/Tag%203/a.mov")

    def test_special_characters(self):
        u = paths.url(LIN, "Ä Ö Ü ß/#1 50% & more [x] {y}.mov")
        self.assertEqual(u, "file://localhost/home/me/Footage/%C3%84%20%C3%96%20%C3%9C%20%C3%9F/"
                            "%231%2050%25%20&%20more%20%5Bx%5D%20%7By%7D.mov")
        self.assertEqual(paths.url(LIN, "日本/動画.mp4"), "file://localhost/home/me/Footage/%E6%97%A5%E6%9C%AC/%E5%8B%95%E7%94%BB.mp4")

    def test_unicode_normalization(self):
        nfc, nfd = "Caf\u00e9.mov", "Cafe\u0301.mov"
        self.assertEqual(paths.url(MAC, nfd).rsplit("/", 1)[1], "Cafe%CC%81.mov")          # kept as given
        self.assertEqual(paths.url(MAC, nfc).rsplit("/", 1)[1], "Caf%C3%A9.mov")
        self.assertEqual(paths.url(dict(MAC, normalize="nfc"), nfd).rsplit("/", 1)[1], "Caf%C3%A9.mov")
        self.assertEqual(paths.url(dict(MAC, normalize="nfd"), nfc).rsplit("/", 1)[1], "Cafe%CC%81.mov")
        # the root is converted too
        root = dict(MAC, root=f"/Users/Ren{NFD_E}/Film", normalize="nfc")
        self.assertIn("Ren%C3%A9", paths.url(root, "a.mov"))

    def test_display_name_is_never_encoded(self):
        self.assertEqual(paths.display_name("Tag 3/IMG 4821 Gletscher.MOV"), "IMG 4821 Gletscher.MOV")


if __name__ == "__main__":
    unittest.main()
