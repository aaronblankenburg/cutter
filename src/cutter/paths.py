"""Paths and file URLs on another computer: the one the timeline is opened on.

cutter runs in a container (or on a server) where the footage has other paths than on the editor's computer. A
*target* describes that computer:

    {"os": "windows" | "macos" | "linux", "root": "D:\\Videos\\Iceland", "package": "_cutlery",
     "normalize": "keep" | "nfc" | "nfd"}

`root` is the footage folder as the editor sees it, `package` the folder (relative to root, or absolute) that holds
the files cutter adds: music, title pictures, maps. A clip at `rel_path` (always with '/') is then at
`root/rel_path` in the target's own path syntax, and its URL is made the way editing applications write them:

    windows  D:\\Videos\\Iceland\\Day 3\\IMG 1.MOV   ->  file://localhost/D:/Videos/Iceland/Day%203/IMG%201.MOV
    UNC      \\\\nas\\film\\Day 3\\a.mov             ->  file://nas/film/Day%203/a.mov
    macos    /Volumes/Card/Tag 3/Straße.mov        ->  file://localhost/Volumes/Card/Tag%203/Stra%C3%9Fe.mov

Unicode: macOS file systems may store names decomposed (NFD: "e" + combining accent) while other systems and most
text input use the composed form (NFC). With `normalize = "keep"` (the default) every part is written as it was
given (the browser reports names as the file system stores them); "nfc"/"nfd" convert the whole path.
"""
import re
import unicodedata
from urllib.parse import quote

OSES = ("windows", "macos", "linux")
NORMALIZE = ("keep", "nfc", "nfd")
DRIVE = re.compile(r"^([A-Za-z]):[\\/]?")
SAFE = "/-._~!$&'()*+,;=@"           # RFC 3986 path characters that stay as they are (":" only after the drive letter)


class PathError(ValueError):
    """The target or a path cannot be used; the text says why."""


def check_target(target):
    """A complete target dict with defaults; raises PathError."""
    if not isinstance(target, dict):
        raise PathError("target must be an object")
    os_ = target.get("os")
    if os_ not in OSES:
        raise PathError(f"target os must be one of {', '.join(OSES)}")
    root = (target.get("root") or "").strip()
    if not root:
        raise PathError("target root (the footage folder on the editing computer) is missing")
    norm = target.get("normalize", "keep")
    if norm not in NORMALIZE:
        raise PathError(f"normalize must be one of {', '.join(NORMALIZE)}")
    if os_ == "windows":
        if not (DRIVE.match(root) or root.startswith("\\\\") or root.startswith("//")):
            raise PathError(f"a Windows folder starts with a drive letter (D:\\…) or \\\\server\\share: {root!r}")
    elif not root.startswith("/"):
        raise PathError(f"a {'macOS' if os_ == 'macos' else 'Linux'} folder starts with /: {root!r}")
    return {"os": os_, "root": root, "package": target.get("package") or "_cutlery", "normalize": norm}


def _parts(path, os_):
    """(prefix, [parts]) of a native path. prefix: "D:" for a drive, "//server/share" for UNC, "" for POSIX."""
    p = path.replace("\\", "/") if os_ == "windows" else path
    if os_ == "windows":
        m = DRIVE.match(p)
        if m:
            return m.group(1).upper() + ":", [x for x in p[m.end():].split("/") if x]
        if p.startswith("//"):
            bits = [x for x in p[2:].split("/") if x]
            if len(bits) < 2:
                raise PathError(f"a UNC path needs a server and a share: {path!r}")
            return "//" + "/".join(bits[:2]), bits[2:]
    return "", [x for x in p.split("/") if x]


def _normalize(text, how):
    return text if how == "keep" else unicodedata.normalize(how.upper(), text)


def _check_rel(rel):
    parts = [x for x in str(rel).replace("\\", "/").split("/") if x not in ("", ".")]
    if not parts or ".." in parts:
        raise PathError(f"not a relative path inside the footage folder: {rel!r}")
    return parts


def join(target, rel_path, under_package=False):
    """The native path of a file on the target computer (root/rel_path or root/package/rel_path)."""
    t = check_target(target)
    prefix, parts = _parts(t["root"], t["os"])
    if under_package:
        pkg = t["package"]
        absolute = pkg.startswith("/") or (t["os"] == "windows" and (DRIVE.match(pkg) or pkg.startswith("\\\\")))
        if absolute:
            prefix, parts = _parts(pkg, t["os"])
        else:
            parts = parts + _check_rel(pkg)
    parts = [_normalize(x, t["normalize"]) for x in parts + _check_rel(rel_path)]
    if t["os"] == "windows":
        if prefix.startswith("//"):
            return "\\\\" + prefix[2:].replace("/", "\\") + ("\\" + "\\".join(parts) if parts else "")
        return prefix + "\\" + "\\".join(parts)
    return "/" + "/".join(parts)


def file_url(native, os_, colon=":"):
    """The file URL of a native path on a computer of kind `os_`, percent-encoded as UTF-8.
    colon: how the colon after a drive letter is written (":" or "%3A"; Premiere writes "%3a" itself, reads both)."""
    if os_ not in OSES:
        raise PathError(f"os must be one of {', '.join(OSES)}")
    prefix, parts = _parts(native, os_)
    enc = "/".join(quote(x, safe=SAFE.replace("/", "")) for x in parts)
    if os_ == "windows":
        if prefix.startswith("//"):
            server, share = prefix[2:].split("/", 1)
            return f"file://{quote(server, safe='')}/{quote(share, safe='')}" + (f"/{enc}" if enc else "")
        return f"file://localhost/{prefix[0]}{colon}/{enc}"
    return f"file://localhost/{enc}"


def url(target, rel_path, under_package=False, colon=":"):
    t = check_target(target)
    return file_url(join(t, rel_path, under_package), t["os"], colon)


def display_name(rel_path):
    """The file name as it appears in the editing application (never URL-encoded)."""
    return _check_rel(rel_path)[-1]
