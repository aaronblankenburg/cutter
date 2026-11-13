"""Who is in the picture: the faces of a clip grouped by person, and the same people found across the project.

image_analysis describes every face it finds once a second by a vector (SFace); faces of one person have vectors
that point the same way. This module needs only numpy:

  clip_faces(found)                  the faces of one clip grouped into people -> [{"seconds", "faces", "size",
                                     "vector", "best"}]
  update(clips, image_dir, file)     the people of the project -> <analysis>/people.json and people.jpg
  rename(file, id, name), merge(file, ids), split(file, id, which, clips, image_dir)
                                     corrections by the user; update() keeps them

people.json: {"people": [{"id": "P1", "name", "seconds", "clips": {"C001": [{"start", "end"}]}, "pictures",
"vectors"}], "unassigned_faces"}. IDs and names survive a new analysis: a person is found again through its
vectors, which are those of all the groups of faces filed under it: a group whose vector a person holds always
stays with that person. Merging joins the vectors of two people; splitting moves the vectors of the faces that are
someone else.
The vectors describe faces (biometric data): the file stays in the project and is not part of the review data.
"""
import pathlib

from . import quality
from .util import CutterError, MISSING, USAGE, dump_json, read_json

SAME = 0.40             # average cosine similarity from which faces (or groups of faces) are the same person
MIN_SECONDS = 3         # a face seen for less than this in the whole project gets no ID
EXACT = 0.9999          # similarity from which a vector is the one a person holds: those faces are that person
PICTURES = 8            # per person on the sheet (people.json lists all)


def unit(v):
    import numpy as np
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


def group(vectors, threshold=SAME):
    """A label per vector. Two groups belong together when their members are, on average, similar enough to each
    other (average linkage). The similarity of the groups' centres would not do: the noise of single faces averages
    out in a centre, so the centres of two different people come out more alike than any two of their faces."""
    import numpy as np
    v = unit(vectors) if len(vectors) else np.zeros((0, 1), dtype=np.float32)
    sums, sizes, labels = [], [], [-1] * len(v)
    for i in range(len(v)):                        # first pass: join the group the vector is closest to, or start one
        if sums:
            sim = (np.array(sums) / np.array(sizes)[:, None]) @ v[i]
            k = int(sim.argmax())
            if sim[k] >= threshold:
                sums[k] = sums[k] + v[i]
                sizes[k] += 1
                labels[i] = k
                continue
        sums.append(v[i].copy())
        sizes.append(1)
        labels[i] = len(sums) - 1
    while len(sums) > 1:                           # then join the groups that came out close to each other
        mean = np.array(sums) / np.array(sizes)[:, None]
        sim = mean @ mean.T                        # the average similarity between the members of two groups
        np.fill_diagonal(sim, -1)
        a, b = sorted(divmod(int(sim.argmax()), len(sums)))
        if sim[a, b] < threshold:
            break
        sums[a] = sums[a] + sums[b]
        sizes[a] += sizes[b]
        del sums[b], sizes[b]
        labels = [a if x == b else x - 1 if x > b else x for x in labels]
    return labels


def clip_faces(found):
    """found: [{"t", "v", "size", "score"}] of one clip. Returns the people in it, most seen first; "best" is the
    index in found of the face to show."""
    import numpy as np
    if not found:
        return []
    labels = group([f["v"] for f in found])
    out = []
    for k in sorted(set(labels)):
        idx = [i for i, x in enumerate(labels) if x == k]
        mean = unit(np.sum([found[i]["v"] for i in idx], axis=0))
        out.append({"seconds": sorted({found[i]["t"] for i in idx}), "faces": len(idx),
                    "size": round(max(found[i]["size"] for i in idx), 3),
                    "vector": [round(float(x), 4) for x in mean],
                    "best": max(idx, key=lambda i: found[i]["size"] * found[i]["score"])})
    return sorted(out, key=lambda p: -len(p["seconds"]))


def _number(pid):
    try:
        return int(str(pid)[1:])
    except ValueError:
        return 0


def _place(groups, known=()):
    """The known people and the new ones, each with "parts": the groups of faces that are this person. A group whose
    vector a known person holds is that person; the others go to the person they are similar enough to on average
    and are held by it from then on. So what was once filed under a person stays there, whatever is merged into it
    or split from it later."""
    people = [{"id": k["id"], "name": k.get("name", ""), "vectors": [list(v) for v in k.get("vectors", [])], "parts": []}
              for k in known if k.get("vectors")]
    refs = [unit(p["vectors"]) for p in people]
    rest = []
    for g in sorted(groups, key=lambda g: -g["faces"]):
        v = unit(g["vector"])
        held = [float((r @ v).max()) for r in refs]
        sims = [float((r @ v).mean()) for r in refs]
        if held and max(held) >= EXACT:
            people[held.index(max(held))]["parts"].append(g)
        elif sims and max(sims) >= SAME:
            people[sims.index(max(sims))]["parts"].append(g)
            people[sims.index(max(sims))]["vectors"].append(g["vector"])
        else:
            rest.append(g)
    labels = group([g["vector"] for g in rest])
    fresh, unassigned = [], 0
    for k in sorted(set(labels)):
        parts = [g for g, x in zip(rest, labels) if x == k]
        if sum(len(g["seconds"]) for g in parts) < MIN_SECONDS:
            unassigned += sum(g["faces"] for g in parts)
            continue
        fresh.append({"name": "", "vectors": [g["vector"] for g in parts], "parts": parts})
    fresh.sort(key=lambda p: -sum(len(g["seconds"]) for g in p["parts"]))
    last = max([_number(p["id"]) for p in people] + [0])
    for n, p in enumerate(fresh, last + 1):
        p["id"] = f"P{n}"
    return people + fresh, unassigned


def assign(groups, known=()):
    """groups: [{"clip", "seconds", "faces", "vector", "thumb", "duration"}] from the clip files; known: the people
    of an earlier people.json. Returns (people, number of faces without an ID)."""
    placed, unassigned = _place(groups, known)
    out = []
    for p in placed:
        if not p["parts"] and not p["name"]:
            continue
        clips, seconds = {}, 0
        for g in sorted(p["parts"], key=lambda g: g["clip"]):
            if g["clip"] in clips:
                continue
            secs = sorted(set(sec for h in p["parts"] if h["clip"] == g["clip"] for sec in h["seconds"]))
            seconds += len(secs)
            clips[g["clip"]] = quality.ranges(secs, [True] * len(secs), min_len=1.0, close_gap=2.0, end=g.get("duration"))
        pictures = []
        for g in sorted(p["parts"], key=lambda g: -g["faces"]):
            if g.get("thumb"):
                pictures.append(g["thumb"])
        out.append({"id": p["id"], "name": p["name"], "seconds": seconds,
                    "clips": clips, "pictures": pictures, "vectors": p["vectors"]})
    return out, unassigned


def _groups(clips, image_dir):
    """The groups of faces in the clip files, and whether any clip was looked at for faces."""
    groups, measured = [], False
    for c in clips:
        f = pathlib.Path(image_dir) / f'{c["id"]}.json'
        if not f.exists():
            continue
        faces = read_json(f).get("faces")
        measured = measured or faces is not None
        for g in faces or []:
            groups.append(dict(g, clip=c["id"], duration=c.get("duration")))
    return groups, measured


def load(file):
    file = pathlib.Path(file)
    return read_json(file) if file.exists() else {"people": []}


def sheet(people, image_dir, target):
    """One row per person with its faces, to see who is who. Returns the path, or None without Pillow or faces."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    rows = [p for p in people if p["pictures"]]
    if not rows:
        pathlib.Path(target).unlink(missing_ok=True)
        return None
    side, label = 112, 150
    img = Image.new("RGB", (label + PICTURES * side, len(rows) * side), (24, 24, 24))
    draw = ImageDraw.Draw(img)
    for y, p in enumerate(rows):
        draw.text((8, y * side + 8), p["id"], fill=(255, 255, 255))
        draw.text((8, y * side + 28), (p["name"] or "(no name)")[:22], fill=(255, 255, 255))
        draw.text((8, y * side + 48), f'{p["seconds"]} s in {len(p["clips"])} clips', fill=(170, 170, 170))
        for x, pic in enumerate(p["pictures"][:PICTURES]):
            try:
                with Image.open(pathlib.Path(image_dir) / pic) as face:
                    img.paste(face.convert("RGB").resize((side, side)), (label + x * side, y * side))
            except OSError:
                continue
            name = pathlib.PurePosixPath(pic).stem             # the name cutter people split takes
            draw.rectangle((label + x * side, (y + 1) * side - 14, label + x * side + 7 * len(name) + 4, (y + 1) * side),
                           fill=(24, 24, 24))
            draw.text((label + x * side + 3, (y + 1) * side - 13), name, fill=(255, 255, 255))
    img.save(target, quality=88)
    return str(target)


def update(clips, image_dir, file, dry_run=False):
    """Find the people of the project in the clip files and write people.json and people.jpg next to it."""
    image_dir, file = pathlib.Path(image_dir), pathlib.Path(file)
    groups, measured = _groups(clips, image_dir)
    old = load(file)
    if not measured and not old["people"]:
        return {"people": [], "unassigned_faces": 0}
    people, unassigned = assign(groups, old["people"])
    data = {"version": 1, "people": people, "unassigned_faces": unassigned}
    if not dry_run:
        picture = sheet(people, image_dir, file.with_suffix(".jpg"))
        if picture:
            data["sheet"] = pathlib.Path(picture).name
        dump_json(file, data, ensure_ascii=False, indent=1)
    return data


def _find(data, pid, file):
    for p in data["people"]:
        if p["id"].upper() == str(pid).upper():
            return p
    known = ", ".join(p["id"] for p in data["people"]) or "none (run cutter analyze first)"
    raise CutterError(f"No person {pid} in {file}", MISSING if not data["people"] else USAGE, f"People: {known}")


def rename(file, pid, name, dry_run=False):
    data = load(file)
    p = _find(data, pid, file)
    name = " ".join(str(name).split())
    for other in data["people"]:
        if name and other is not p and other["name"].lower() == name.lower():
            raise CutterError(f'{other["id"]} is already called {name}', USAGE,
                              f'If they are the same person: cutter people merge {other["id"]} {p["id"]}')
    p["name"] = name
    if not dry_run:
        dump_json(file, data, ensure_ascii=False, indent=1)
    return p


def merge(file, ids, dry_run=False):
    """The people `ids` are one person: the first keeps its ID and takes the vectors (and a name) of the others."""
    data = load(file)
    found = [_find(data, pid, file) for pid in ids]
    if len({p["id"] for p in found}) < 2:
        raise CutterError("Give two or more different people to merge", USAGE)
    first = found[0]
    for p in found[1:]:
        first["name"] = first["name"] or p["name"]
        first["vectors"] = first["vectors"] + p["vectors"]
        data["people"].remove(p)
    if not dry_run:
        dump_json(file, data, ensure_ascii=False, indent=1)
    return first


def _held(vector, vectors):
    return any(float(unit(vector) @ unit(v)) >= EXACT for v in vectors)


def split(file, pid, which, clips, image_dir, to=None, dry_run=False):
    """Some of the faces filed under `pid` are someone else. which: clips ("C004": the faces of pid in that clip) or
    pictures ("C004_1", the names on people.jpg). They become a new person, or go to the person `to`.
    Returns (the person they left, the person they are now, the pictures moved)."""
    data = load(file)
    p = _find(data, pid, file)
    target = _find(data, to, file) if to else None
    if target is p:
        raise CutterError(f'The faces are already filed under {p["id"]}', USAGE)
    placed, _ = _place(_groups(clips, image_dir)[0], data["people"])
    for x in placed:                                           # everyone holds what is filed under them now, so
        for q in data["people"]:                               # nothing else moves when the vectors change
            if q["id"] == x["id"]:
                q["vectors"] = x["vectors"]
    parts = next((x["parts"] for x in placed if x["id"] == p["id"]), [])

    def picture(g):
        return pathlib.PurePosixPath(g.get("thumb") or g["clip"]).stem

    wanted = {str(w).upper() for w in which}
    moved = [g for g in parts if {g["clip"].upper(), picture(g).upper()} & wanted]
    unknown = wanted - {n.upper() for g in moved for n in (g["clip"], picture(g))}
    if unknown:
        raise CutterError(f'{p["id"]} has no faces in {", ".join(sorted(unknown))}', USAGE,
                          f'{p["id"]} is in: {", ".join(sorted(picture(g) for g in parts)) or "no clip"}')
    if len(moved) == len(parts):
        raise CutterError(f'These are all the faces of {p["id"]}: nothing would be left of it', USAGE,
                          f'cutter people merge {target["id"]} {p["id"]} files all of them under {target["id"]}'
                          if target else "Name the clips or pictures that show someone else")
    gone = [g["vector"] for g in moved]
    p["vectors"] = [v for v in p["vectors"] if not _held(v, gone)]
    if target:
        target["vectors"] = target["vectors"] + [v for v in gone if not _held(v, target["vectors"])]
    else:
        target = {"id": f'P{max(_number(x["id"]) for x in data["people"]) + 1}', "name": "", "seconds": 0, "clips": {},
                  "pictures": [], "vectors": gone}
        data["people"].append(target)
    if not dry_run:
        dump_json(file, data, ensure_ascii=False, indent=1)
    return p, target, sorted(picture(g) for g in moved)
