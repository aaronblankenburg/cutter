"""The `cutter` command line. Subcommands import their packages only when needed, so help, new, agent, deploy and
feedback also run without the tool packages (numpy, librosa, OpenTimelineIO, faster-whisper).

Exit codes: 0 ok · 1 failed · 2 wrong usage · 3 something is missing (file, program, configuration) ·
4 a check found a problem (e.g. a forbidden music license, or doctor finds something missing).
"""
import argparse
import json
import os
import pathlib
import sys
import time

from . import VERSION
from .config import Project, cut_name, previous_cut
from .util import CutterError, FINDING, MISSING, Output, USAGE, dump_json, read_json, write_json

DESCRIPTION = """cutter – footage analysis, beat-synced editing, timelines, FilmCraft finishing, review app.

Cut cycle (details: docs/workflow.md):
  cutter analyze                       transcripts, contact sheets, clip list
  cutter music license / analyze       license check; beats, bars, loudness
  cutter plan cut2 [--new]             feedback as code: cuts/cut2/plan.py -> cuts/cut2/edit.json
  cutter retime cut2                   snap picture cuts to the music
  cutter timeline cut2                 audio levels, .otio, .fcp7.xml, .srt
  cutter filmcraft build cut2          FilmCraft project cuts/cut2/cut2.fcproj (headless)
  cutter filmcraft export cut2         exports/cut2.mp4 with burned-in subtitles
  cutter review-data cut2              review video, thumbnails, timeline.json
  cutter serve                         review app in your network
  cutter feedback fetch / reply        read the comments; post the agent's replies

Exit codes: 0 ok · 1 failed · 2 wrong usage · 3 something missing · 4 a check found a problem.
With --json a command prints one JSON object on stdout; messages go to stderr.
"""


def _project(a, required=True):
    return Project.load(getattr(a, "project", None), required=required)


def _dry(out, data, text):
    data = dict(data, dry_run=True)
    out.result(data, "Dry run – nothing written:\n" + text)
    return data


# ---------------------------------------------------------------- commands

def c_version(a, out):
    out.result({"version": VERSION}, f"cutter {VERSION}")


def c_new(a, out):
    from . import new
    r = new.create(a.folder, media=a.media, title=a.title, license=a.license, review_url=a.review_url, language=a.language,
                   dry_run=a.dry_run)
    out.result(r, r["text"])


def c_doctor(a, out):
    from . import doctor
    r = doctor.check(project=_project(a, required=False), filmcraft_port=a.filmcraft_port, model=a.model,
                     host_facts=a.host_facts)
    out.result(r, doctor.as_text(r))
    if not r["ok"]:
        raise SystemExit(FINDING)


def c_analyze(a, out):
    p = _project(a)
    from . import analyze
    media = a.media or [str(pathlib.Path(os.path.expanduser(m))) for m in p.media_roots().values()]
    if not media:
        raise CutterError("No footage given (cutter.toml → [media] or --media)", MISSING)
    target = pathlib.Path(a.output) if a.output else p.path("clips").parent
    with_proxies = (a.proxies or p["proxies"].get("make")) and not a.list
    if a.dry_run:
        return _dry(out, {"media": media, "output": str(target), "proxies": str(p.path("proxies")) if with_proxies else None},
                    f"  footage: {', '.join(media)}\n  output:  {target} (clips.json, transcripts/, sheets/, strips/, quality.txt)"
                    + (f"\n  proxies: {p.path('proxies')} ({p['proxies']['height']}p, for the clips that need one)" if with_proxies else ""))
    r = analyze.run_analysis(media, target, list_only=a.list, since=a.since, until=a.until, exclude=a.exclude, only=a.only,
                             force=a.force, no_transcripts=a.no_transcripts, no_sheets=a.no_sheets, model=a.model,
                             language=a.language or p.get("language") or "auto", device=a.device, threads=a.threads, max_frames=a.max_frames,
                             interval=a.interval, roots=p.media_roots())
    made = table = None
    if not a.list:
        from . import proxies, timeline
        clips = list(timeline.resolve_clips({c["id"]: c for c in read_json(target / "clips.json")}, p.media_roots()).values())
        if with_proxies:                              # before the image analysis, which then reads them
            made = proxies.make(clips, p.path("proxies"), height=p["proxies"]["height"], only=a.only, force=a.force)
        if proxies.load(p.path("proxies")):
            table = proxies.table(clips, p.path("proxies"))[0]
    if not a.no_image and not a.list:
        from . import image_analysis
        try:
            r["image"] = image_analysis.run(target / "clips.json", p.path("image_analysis"), only=a.only, force=a.force,
                                            proxies=table,
                                            device=a.device, text_path=target / "quality.txt",
                                            people_path=target / "people.json",
                                            recognize=(p.get("image_analysis") or {}).get("recognize_faces", True),
                                            content_tags=(p.get("image_analysis") or {}).get("content_tags"))
        except CutterError as ex:
            if ex.code != MISSING:
                raise
            out.warn(f"{ex.text}: no image analysis")
        else:
            r["image"]["sheets"] = analyze.add_quality_bars(target, p.path("image_analysis"))
            r["text"] = (r.get("text") or "") + f"\nUsable ranges, camera moves, people, sound and place per clip: {target / 'quality.txt'}"
            if r["image"]["people"]:
                r["text"] += f"\nPeople by their faces ({len(r['image']['people'])}): {target / 'people.jpg'} – name them with cutter people name"
    if made:
        r["proxies"] = x = made
        r["text"] = (r.get("text") or "") + (
            f"\nProxies ({x['height']}p): {x['made']} made, {x['kept']} were there, {x['as_is']} clips need none"
            f" -> {x['folder']} ({x['mb']:.0f} MB) – cutter timeline <cut> --proxies builds a draft from them"
            + "".join(f"\n  ! {f['clip']}: no proxy ({f['problem']})" for f in x["failed"]))
    out.result(r, r.get("text"))


def c_music(a, out):
    p = _project(a)
    from . import music
    if a.music_cmd == "license":
        r = music.license_check(p, files=a.files, edit_path=p.path("edit", a.cut) if a.cut else None)
        out.result(r, r["text"])
        if not r["ok"]:
            raise SystemExit(FINDING)
        return
    files = music.find_files(p, a.files)
    target = p.path("music_analysis")
    if not files:
        raise CutterError(f"No music files given or found in {p.path('music')}", MISSING)
    if a.dry_run:
        return _dry(out, {"files": [str(f) for f in files], "target": str(target)}, "\n".join(f"  {f}" for f in files) + f"\n  -> {target}")
    r = music.analyze(files, target, start_bpm=a.start_bpm)
    out.result(r, r["text"])


def c_plan(a, out):
    p = _project(a)
    from . import new
    r = new.plan(p, a.cut, new=a.new, previous=a.previous, dry_run=a.dry_run)
    out.result(r, r["text"])


def c_retime(a, out):
    p = _project(a)
    from . import retime
    from .timeline import load_edit
    cut = cut_name(a.cut)
    edit_p = p.path("edit", cut)
    for q, what in ((edit_p, "Edit list"), (p.path("clips"), "clips.json"), (p.path("music_analysis"), "Music analysis")):
        if not q.exists():
            raise CutterError(f"{what} not found: {q}", MISSING, "cutter music analyze" if what == "Music analysis" else None)
    work = p.path("work", cut)
    log_p = work / "retime_log.json"
    if a.dry_run:
        return _dry(out, {"edit": str(edit_p), "log": str(log_p)}, f"  {edit_p} (rewritten in place, the input kept as {work / 'edit.before-retime.json'})")
    plan, _ = load_edit(edit_p)
    clips = {c["id"]: c for c in read_json(p.path("clips"))}
    E, log, Q, total = retime.align(plan, clips, read_json(p.path("music_analysis")), p["retime"], p.path("image_analysis"),
                                    p.external_clip_ids(plan))
    before = retime.duration(plan, clips, p["retime"].get("fps", 25), p.external_clip_ids(plan))
    work.mkdir(parents=True, exist_ok=True)
    (work / "edit.before-retime.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    dump_json(edit_p, E, ensure_ascii=False, indent=1)
    dump_json(log_p, log, ensure_ascii=False, indent=1, default=float)
    errors = [L for L in log if "error" in L]
    r = {"edit": str(edit_p), "log": str(log_p), "entry_points": Q, "duration": round(total, 3), "duration_before": round(before, 3),
         "moved": sum(1 for L in log if "new" in L), "without_beat": len(errors)}
    out.result(r, retime.log_text(log, Q, total, before) + f"\n-> {edit_p}")


def _timeline_args(p):
    t = p["timeline"]
    return dict(ns=p.otio_namespace, music_dirs=p.music_dirs(), config_external=t["external_clips"],
                track_names=t["track_names"], texts=t["texts"], media_roots=p.media_roots(), project_dir=p.folder)


def c_timeline(a, out):
    p = _project(a)
    from . import timeline
    cut = cut_name(a.cut)
    edit_p = p.path("edit", cut)
    target = p.path("timeline", cut)
    if not edit_p.exists():
        raise CutterError(f"Edit list not found: {edit_p}", MISSING)
    table = None
    if a.proxies:
        from . import proxies
        clips = timeline.resolve_clips({c["id"]: c for c in read_json(p.path("clips"))}, p.media_roots())
        table, missing = proxies.table(list(clips.values()), p.path("proxies"))
        if missing:
            out.warn(f"No current proxy for {', '.join(missing[:8])}{' …' if len(missing) > 8 else ''}: the footage is used "
                     f"(cutter analyze --proxies makes them)")
    if a.dry_run:
        return _dry(out, {"edit": str(edit_p), "out": str(target), "media": "proxies" if a.proxies else "footage"},
                    f"  {edit_p}" + ("" if a.build_only else " (audio levels set in place)") + f"\n  -> {target}.otio, {target}.fcp7.xml, {target}.srt"
                    + ("\n  a draft that plays the proxies" if a.proxies else ""))
    r = {}
    if not a.build_only:
        r = timeline.assemble(edit_p, p.path("clips"), p.media_base, p.path("work", cut) / "intermediate", edit_p,
                              audio=p["audio"], external_ids=p.external_clip_ids(), **_timeline_args(p))
    b = timeline.build(edit_p, p.path("clips"), p.media_base, target, proxies=table, **_timeline_args(p))
    r["timeline"], r["media"] = b, b["media"]
    text = (f"Camera audio: {r['lowered']} lowered under music, {r['measured']} brought to the target loudness\n" if not a.build_only else "") + b["text"]
    out.result(r, text)


def _timeline_media(p, cut):
    """"proxies" when the timeline of the cut is a draft that plays the proxies, else "footage"."""
    f = pathlib.Path(str(p.path("timeline", cut)) + ".otio")
    try:
        return (read_json(f).get("metadata") or {}).get(p.otio_namespace, {}).get("media") or "footage"
    except (OSError, ValueError, AttributeError):
        return "footage"


def _fc_config(p):
    from . import filmcraft
    cfg = p["filmcraft"]
    path = cfg.get("config")
    fc = filmcraft.load_config(p.path(path) if path else None)
    for cue, sec in (p["music"].get("fade_out_s") or {}).items():
        fc["music"]["fade_out_s"].setdefault(cue, sec)            # cutter.toml → music.fade_out_s
    if not fc["captions"].get("language") and p.get("language"):
        fc["captions"]["language"] = p.get("language")          # subtitle track language = spoken language
    for k, v in cfg.items():                          # [filmcraft] keys in cutter.toml override
        if k in filmcraft.DEFAULTS and not isinstance(v, dict):
            fc[k] = v
    return fc


def c_filmcraft(a, out):
    from . import filmcraft
    p = _project(a)
    fc = _fc_config(p)
    cut = cut_name(a.cut)
    bridge = a.bridge
    if bridge is not None and int(bridge) == 9876 and os.environ.get("CUTTER_TEST"):
        raise CutterError("Tests never use the control channel on port 9876", USAGE)
    cli = filmcraft.find_cli()
    xml = pathlib.Path(str(p.path("timeline", cut)) + ".fcp7.xml")
    srt = pathlib.Path(str(p.path("timeline", cut)) + ".srt")
    project_file = p.path("filmcraft_project", cut)
    data_dir = p.folder / ".cutter" / "filmcraft-data"

    def client(base, target):
        if a.dry_run:
            return filmcraft.FakeFilmCraft(recording=getattr(a, "recording", None))
        if bridge is not None:
            return filmcraft.CliClient(cli, bridge_port=int(bridge), token_file=p["filmcraft"].get("token_file"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return filmcraft.CliClient(cli, base=base, target=target, data_dir=data_dir)

    if a.fc_cmd == "build":
        edit_p = p.path("edit", cut)
        for q, what in ((edit_p, "Edit list"), (xml, "FCP 7 XML")):
            if not q.exists():
                raise CutterError(f"{what} not found: {q}", MISSING, f"First: cutter timeline {cut}")
        prev = previous_cut(cut)
        base = pathlib.Path(a.base) if a.base else (p.path("filmcraft_project", prev) if prev else None)
        if base is not None and not base.exists():
            base = None
        music_analysis = read_json(p.path("music_analysis")) if p.path("music_analysis").exists() else None
        if bridge is None and not a.dry_run and project_file.exists():
            if not a.overwrite:
                raise CutterError(f"The project file exists already: {project_file}", MISSING, "--overwrite rebuilds it from the timeline")
            project_file.unlink()
        cl = client(base, project_file)
        r = filmcraft.finish(cl, fc, read_json(edit_p), xml, srt, p.path("work", cut) / "filmcraft_batch.jsonl", base=p.media_base,
                             backup=(p.path("work", cut) / "backup_before_build.fcproj") if bridge is not None else None,
                             music_analysis=music_analysis, retime_cues=p["retime"].get("cues"), execute=not a.dry_run)
        r["mode"] = "dry-run" if a.dry_run else ("bridge" if bridge is not None else "headless")
        r["media"] = _timeline_media(p, cut)
        if r["media"] == "proxies":
            r["log"].append("Draft: the project plays the proxies")
        if not a.dry_run:
            write_json(p.path("work", cut) / "filmcraft_media.json", {"media": r["media"]})
        if bridge is None and not a.dry_run:
            r["project"] = str(project_file)
        return out.result(r, "\n".join(r["log"]) + (f"\n-> {project_file}" if r.get("project") else ""))
    if a.fc_cmd == "export":
        target = pathlib.Path(a.output) if a.output else p.path("exports") / f"{cut}.mp4"
        preset = a.preset or fc["export"].get("preset") or p["filmcraft"].get("export_preset")
        settings = dict(fc["export"].get("settings") or {}, burnCaptions=True)       # subtitles are always burned in
        if a.dry_run:
            cmd = ["filmcraft-cli", "--project", str(project_file), "export", str(target), "--preset", preset, "--settings",
                   json.dumps(settings)] + (["--start", str(a.start), "--end", str(a.end)] if a.start is not None else [])
            return _dry(out, {"command": cmd}, "  " + " ".join(cmd))
        if bridge is None and not project_file.exists():
            raise CutterError(f"FilmCraft project of the cut missing: {project_file}", MISSING, f"First: cutter filmcraft build {cut}")
        target.parent.mkdir(parents=True, exist_ok=True)
        cl = client(project_file, project_file.with_name(project_file.stem + ".export.fcproj"))
        if bridge is None:
            cl.saved, cl.target = True, project_file
        t0 = time.time()
        res = cl.export(target, preset, settings, a.start, a.end)
        built = p.path("work", cut) / "filmcraft_media.json"
        media = read_json(built)["media"] if built.exists() else _timeline_media(p, cut)
        r = {"output": str(target), "seconds": round(time.time() - t0, 1), "settings": settings, "result": res, "media": media}
        draft = ("\nThis is a draft from the proxies. For the final film: cutter timeline " + cut + ", cutter filmcraft build "
                 + cut + " --overwrite, then export again") if media == "proxies" else ""
        return out.result(r, f"Exported: {target} ({r['seconds']} s, subtitles burned in)" + draft)
    if a.fc_cmd == "gui":
        raise CutterError("cutter filmcraft gui starts the desktop app on your computer; run it through the launcher bin/cutter", USAGE)


def c_map(a, out):
    from . import maps
    cfg = read_json(a.config)
    if a.dry_run:
        return _dry(out, {"config": a.config, "output": cfg.get("output")}, f"  {a.config} -> {cfg.get('output')}")
    base = pathlib.Path(a.config).resolve().parent
    r = maps.overview(cfg, base) if a.map_cmd == "overview" else maps.route(cfg, base)
    out.result(r, r["text"])


def c_review_data(a, out):
    p = _project(a)
    from . import review_data
    cut = cut_name(a.cut)
    edit = p.path("edit", cut)
    otio = pathlib.Path(str(p.path("timeline", cut)) + ".otio")
    target = p.path("review") / "cuts"
    dec = p.path("decisions", cut)
    video = a.video
    if video is None and not a.rough and (p.path("exports") / f"{cut}.mp4").exists():
        video = str(p.path("exports") / f"{cut}.mp4")
    if a.dry_run:
        return _dry(out, {"edit": str(edit), "otio": str(otio), "target": str(target / cut), "video": video,
                          "decisions": str(dec) if dec.exists() else None},
                    f"  {edit} + {otio}" + (f" + {dec}" if dec.exists() else "") + f"\n  video: {video or 'rough render'}\n  -> {target / cut}")
    os.environ["CUTTER_JSON"] = "1" if out.as_json else ""
    r = review_data.create(cut, edit, otio, target, p.path("transcripts"), video=video, decisions_path=dec if dec.exists() else None,
                           numbers_in_export=a.numbers, no_numbers=a.no_numbers, timeline_only=a.timeline_only, jobs=a.jobs,
                           ns=p.otio_namespace, external=p.external_clip_ids(), fade_out=p["music"]["fade_out_s"],
                           levels={"base": p["music"]["base_lufs"], "under": p["music"]["under_dialogue_lufs"], "low": p["music"]["low_lufs"]})
    from .deploy import write_app_config
    write_app_config(p, p.path("review"))
    r["media"] = "footage" if video else _timeline_media(p, cut)
    out.result(r, f"Review data of {cut}: {r['folder']}" + (" (rough render from the proxies)" if r["media"] == "proxies" else ""))


def c_review(a, out):
    p = _project(a)
    if a.review_cmd == "password":
        from .review_app import set_password
        f = p.path("htpasswd")
        try:
            set_password.check_name(a.name)
            if a.delete and a.generate:
                raise set_password.PasswordError("--delete and --generate exclude each other.")
            if a.dry_run:
                todo = "remove" if a.delete else "make a password for" if a.generate else "set the password of"
                return _dry(out, {"file": str(f), "name": a.name, "delete": a.delete, "generate": a.generate,
                                  "exists": a.name in set_password.names(f)},
                            f"  {todo} {a.name} in {f} (only a scrypt hash is stored)")
            r, text = set_password.run(f, a.name, delete=a.delete, make=a.generate)
        except set_password.PasswordError as ex:
            raise CutterError(str(ex), USAGE, None if a.delete or a.generate else
                              f"cutter review password {a.name} --generate makes a password and prints it once") from None
        out.result(r, text)


def c_people(a, out):
    p = _project(a)
    from . import image_analysis, people
    analysis = p.path("clips").parent
    file = analysis / "people.json"
    if a.people_cmd == "list":
        data = people.load(file)
        rows = [{k: x[k] for k in ("id", "name", "seconds")} | {"clips": sorted(x["clips"]), "pictures": [pathlib.PurePosixPath(n).stem for n in x.get("pictures", [])]}
                for x in data["people"]]
        text = "\n".join(f'  {x["id"]:<4} {x["name"] or "(no name)":<20} {x["seconds"]:>4} s  {", ".join(x["clips"][:12])}'
                         + (" …" if len(x["clips"]) > 12 else "") for x in rows)
        return out.result({"file": str(file), "sheet": str(file.with_suffix(".jpg")) if data.get("sheet") else None, "people": rows},
                          (f"People by their faces (pictures: {file.with_suffix('.jpg')}):\n{text}" if rows else
                           "No people found yet (cutter analyze finds them by their faces)."))
    if a.people_cmd == "name":
        who = people.rename(file, a.id, a.name, dry_run=a.dry_run)
        text = f'{who["id"]} is {who["name"] or "without a name again"}'
    elif a.people_cmd == "merge":
        if not a.dry_run and p.path("clips").exists():         # a file of 0.2.0 does not hold all faces of a person yet
            people.update(read_json(p.path("clips")), p.path("image_analysis"), file)
        who = people.merge(file, a.ids, dry_run=a.dry_run)
        text = f'{", ".join(x.upper() for x in a.ids[1:])} merged into {who["id"]}{" " + who["name"] if who["name"] else ""}'
    clips = read_json(p.path("clips")) if p.path("clips").exists() else []
    if a.people_cmd == "split":
        old, who, moved = people.split(file, a.id, a.which, clips, p.path("image_analysis"), to=a.to, dry_run=a.dry_run)
        text = (f'The faces of {old["id"]} in {", ".join(moved)} are {who["id"]}{" " + who["name"] if who["name"] else ""}'
                + ("" if a.to else f' (a new ID; cutter people name {who["id"]} "…" names it)'))
    r = {"file": str(file), "id": who["id"], "name": who["name"]}
    if a.people_cmd == "split":
        r.update({"from": old["id"], "moved": moved})
    if a.dry_run:
        return _dry(out, r, "  " + text)
    known = people.update(clips, p.path("image_analysis"), file)["people"]
    if (analysis / "quality.txt").exists():
        (analysis / "quality.txt").write_text(image_analysis.readable(clips, p.path("image_analysis"), known), encoding="utf-8")
    out.result(r, text)


def c_serve(a, out):
    p = _project(a, required=False)
    data = p.path("review")
    htpasswd = p.path("htpasswd")
    port = a.port or int(os.environ.get("CUTTER_REVIEW_PORT") or 8089)
    bind = a.bind or os.environ.get("CUTTER_REVIEW_BIND") or "0.0.0.0"
    users = [z for z in (htpasswd.read_text(encoding="utf-8").splitlines() if htpasswd.exists() else []) if ":" in z and not z.startswith("#")]
    if not users:
        raise CutterError(f"No reviewer password set ({htpasswd}); the review app never runs without Basic Auth.", MISSING,
                          "Add a reviewer first: cutter review password <name>")
    data.mkdir(parents=True, exist_ok=True)
    if p.file:
        from .deploy import write_app_config
        write_app_config(p, data)
    server = pathlib.Path(__file__).resolve().parent / "review_app" / "server.py"
    if a.dry_run:
        return _dry(out, {"server": str(server), "data": str(data), "htpasswd": str(htpasswd), "bind": bind, "port": port},
                    f"  python3 {server} --host {bind} --port {port} --data {data} --htpasswd {htpasswd}")
    if not os.environ.get("CUTTER_IN_CONTAINER"):
        from .lan import addresses
        for u in addresses(port, bind):
            print(u, flush=True)
    os.execv(sys.executable, [sys.executable, str(server), "--host", bind, "--port", str(port), "--data", str(data),
                              "--htpasswd", str(htpasswd)])


def c_deploy(a, out):
    p = _project(a, required=False)
    from . import deploy
    r = deploy.run(p, a.what, env_file=a.env, dry_run=a.dry_run)
    out.result(r, r["text"])


def c_feedback(a, out):
    p = _project(a, required=False)
    from . import feedback
    if a.fb_cmd == "fetch":
        r = feedback.fetch(p, cut=a.cut, local=a.local, data=a.data, output=a.output, status=a.status, env_file=a.env, dry_run=a.dry_run)
    else:
        r = feedback.reply(p, a.file, cut=a.cut, local=a.local, env_file=a.env, dry_run=a.dry_run, restart=not a.no_restart)
    out.result(r, r["text"])


def c_agent(a, out):
    from . import agent
    r = agent.setup(claude=a.claude, codex=a.codex, dry_run=a.dry_run, replace=a.replace)
    out.result(r, r["text"])


def c_migrate(a, out):
    from .legacy import migrate
    results = []
    for f in a.files:
        src = pathlib.Path(f)
        data = read_json(src)
        new, kind = migrate(data)
        if kind is None:
            results.append({"file": str(src), "kind": None, "changed": False})
            continue
        target = pathlib.Path(a.output) if a.output and len(a.files) == 1 else src
        if not a.dry_run:
            if target == src:
                src.with_name(src.name + ".bak").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            target.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
        results.append({"file": str(src), "kind": kind, "changed": True, "output": str(target)})
    text = "\n".join(f"  {r['file']}: " + (f"{r['kind']} -> {r['output']}" + (" (dry run)" if a.dry_run else "") if r["changed"] else "nothing to migrate")
                     for r in results)
    out.result({"files": results, "dry_run": a.dry_run}, text)


def c_demo(a, out):
    from . import demo
    if a.demo_cmd == "media":
        r = demo.media(a.folder, dry_run=a.dry_run)
    elif a.demo_cmd == "cut":
        r = demo.first_cut(_project(a), cut=cut_name(a.cut), dry_run=a.dry_run)
    else:
        r = demo.project(a.folder, dry_run=a.dry_run)
    out.result(r, r["text"])


def c_tool(a, out):
    from . import tools
    p = _project(a)
    if a.tool_cmd == "window":
        r = tools.window_sheet(p, a.output, a.specs)
    elif a.tool_cmd == "frames":
        r = tools.frame_sheet(p, a.output, a.spec)
    else:
        r = tools.asr(p, a.specs, model=a.model, prompt=a.prompt, language=a.language or p.get("language") or None)
    out.result(r, r["text"])


# ---------------------------------------------------------------- parser

def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="print the result as one JSON object")
    common.add_argument("--project", default=argparse.SUPPRESS, help="project folder with cutter.toml (default: current folder or above)")
    dry = argparse.ArgumentParser(add_help=False)
    dry.add_argument("--dry-run", action="store_true", help="show what would happen; write, render or send nothing")

    ap = argparse.ArgumentParser(prog="cutter", description=DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    ap.add_argument("--version", action="version", version=f"cutter {VERSION}")
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")

    def cmd(name, fn, help_, parents=(common,), **kw):
        p = sub.add_parser(name, help=help_, description=help_, parents=list(parents), **kw)
        p.set_defaults(fn=fn)
        return p

    cmd("version", c_version, "show the version")

    p = cmd("new", c_new, "create a project folder with cutter.toml, AGENTS.md and CLAUDE.md", (common, dry))
    p.add_argument("folder", nargs="?", help="project folder, apart from the footage (default: here if empty, else ./<name>-film)")
    p.add_argument("--media", action="append", default=[], help="footage folder (several allowed; mounted read-only)")
    p.add_argument("--title", default="")
    p.add_argument("--license", default="", help="license of the film, e.g. CC BY-NC-SA 4.0")
    p.add_argument("--review-url", default="", help="address of a deployed review app")
    p.add_argument("--language", default="", help="spoken language of the footage, e.g. de or en (default: detected)")

    p = cmd("doctor", c_doctor, "check Docker, image, speech model, FilmCraft, footage access and disk space")
    p.add_argument("--filmcraft-port", type=int, help="also check the control channel of a running FilmCraft on this port")
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--host-facts", help=argparse.SUPPRESS)

    p = cmd("analyze", c_analyze, "clip list, transcripts with word timestamps, contact sheets, film strips", (common, dry))
    p.add_argument("--media", action="append", help="instead of cutter.toml → [media]")
    p.add_argument("--output", help="output folder (default: analysis/)")
    p.add_argument("--list", action="store_true", help="only an overview by folder and day")
    p.add_argument("--since"); p.add_argument("--until")
    p.add_argument("--exclude", action="append", default=[], help="skip files whose path contains this text")
    p.add_argument("--only", help="only these clip IDs, e.g. C017,C023")
    p.add_argument("--force", action="store_true", help="redo existing results")
    p.add_argument("--no-transcripts", action="store_true"); p.add_argument("--no-sheets", action="store_true")
    p.add_argument("--proxies", action="store_true",
                   help="also make proxies of large footage (analysis/proxies/) for fast drafts: cutter timeline --proxies")
    p.add_argument("--no-image", action="store_true",
                   help="skip the image and sound analysis (usable ranges, camera moves, people, wind, place)")
    p.add_argument("--image", action="store_true", help=argparse.SUPPRESS)      # now the default; kept for old scripts
    p.add_argument("--model", default="large-v3-turbo")
    p.add_argument("--language", help="spoken language, e.g. de or en (default: cutter.toml → language, else detected)")
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    p.add_argument("--threads", type=int, default=max(4, (os.cpu_count() or 8) // 2))
    p.add_argument("--max-frames", type=int, default=24); p.add_argument("--interval", type=float)

    p = cmd("music", c_music, "music: beats, bars and loudness (analyze); license check (license)")
    ms = p.add_subparsers(dest="music_cmd", required=True)
    q = ms.add_parser("analyze", help="beats, downbeats and loudness -> music/music.json", parents=[common, dry])
    q.add_argument("files", nargs="*", help="music files (default: everything in music/)")
    q.add_argument("--start-bpm", type=float)
    q = ms.add_parser("license", help="check licenses from tags and the edit list against cutter.toml", parents=[common])
    q.add_argument("files", nargs="*", help="music files (default: everything in music/)")
    q.add_argument("--cut", help="also check the music of this cut's edit list")

    p = cmd("plan", c_plan, "run cuts/<cut>/plan.py (feedback as code); --new creates it from the template", (common, dry))
    p.add_argument("cut"); p.add_argument("--new", action="store_true")
    p.add_argument("--previous", help="previous cut (default: number minus one)")

    p = cmd("retime", c_retime, "snap picture cuts under music to beats, section changes to downbeats", (common, dry))
    p.add_argument("cut")

    p = cmd("timeline", c_timeline, "set audio levels and build OTIO, FCP 7 XML and SRT", (common, dry))
    p.add_argument("cut"); p.add_argument("--build-only", action="store_true", help="only build, keep the audio levels")
    p.add_argument("--proxies", action="store_true",
                   help="a draft: the timeline plays the proxies (cutter analyze --proxies), so the rough render and the "
                        "FilmCraft export are fast; build it without this option for the final film")

    p = cmd("filmcraft", c_filmcraft, "FilmCraft: build the project of a cut (headless), export it, or start the desktop app")
    fs = p.add_subparsers(dest="fc_cmd", required=True)
    for name, help_ in (("build", "import the timeline and add titles, credits, map, audio chain, music ducking, subtitles"),
                        ("export", "export the cut; subtitles are always burned in")):
        q = fs.add_parser(name, help=help_, parents=[common, dry])
        q.add_argument("cut")
        q.add_argument("--bridge", nargs="?", const=9876, type=int, metavar="PORT",
                       help="work in the open desktop app through its control channel instead of headless")
    q = fs.choices["build"]
    q.add_argument("--base", help="start from this .fcproj (default: the previous cut's project); it is never changed")
    q.add_argument("--overwrite", action="store_true", help="rebuild an existing cut<n>.fcproj")
    q.add_argument("--recording", help="dry run with this recording of `inspect sequence`")
    q = fs.choices["export"]
    q.add_argument("--output", help="video file (default: exports/<cut>.mp4)"); q.add_argument("--preset")
    q.add_argument("--start", type=float); q.add_argument("--end", type=float)
    fs.add_parser("gui", help="start the FilmCraft desktop app with its control channel (on your computer)", parents=[common])

    p = cmd("map", c_map, "maps for intros: overview from Natural Earth (georeferenced) or a route from stage images")
    mp = p.add_subparsers(dest="map_cmd", required=True)
    for name in ("overview", "route"):
        q = mp.add_parser(name, parents=[common, dry]); q.add_argument("--config", required=True)

    p = cmd("review-data", c_review_data, "prepare a cut for the review app (video, thumbnails, timeline.json)", (common, dry))
    p.add_argument("cut"); p.add_argument("--video", help="exported film (default: exports/<cut>.mp4 if it exists)")
    p.add_argument("--rough", action="store_true", help="rough render from the timeline even if an export exists")
    p.add_argument("--numbers", action="store_true", help="burn shot numbers into the export too")
    p.add_argument("--no-numbers", action="store_true", help="no shot numbers in the rough render")
    p.add_argument("--timeline-only", action="store_true", help="only rewrite timeline.json")
    p.add_argument("--jobs", type=int)

    p = cmd("review", c_review, "review app settings")
    rs = p.add_subparsers(dest="review_cmd", required=True)
    q = rs.add_parser("password", help="add a reviewer or change their password (stores only a scrypt hash)", parents=[common, dry])
    q.add_argument("name", help="login name of the reviewer")
    q.add_argument("--generate", action="store_true", help="make a random password and print it once (needs no terminal)")
    q.add_argument("--delete", action="store_true", help="remove the reviewer")

    p = cmd("people", c_people, "the people cutter analyze found by their faces: list, name, merge, split")
    ps = p.add_subparsers(dest="people_cmd", required=True)
    ps.add_parser("list", help="who was found, for how long and in which clips (pictures: analysis/people.jpg)", parents=[common])
    q = ps.add_parser("name", help='give a person a name, e.g. cutter people name P1 "Anna" (an empty name removes it)', parents=[common, dry])
    q.add_argument("id"); q.add_argument("name")
    q = ps.add_parser("merge", help="two or more IDs are the same person; the first ID stays", parents=[common, dry])
    q.add_argument("ids", nargs="+", metavar="id")
    q = ps.add_parser("split", parents=[common, dry],
                      help="an ID shows more than one person: file its faces in the given clips (C004) or pictures "
                           "(C004_1, the names on people.jpg) under a new ID, or under --to")
    q.add_argument("id"); q.add_argument("which", nargs="+", metavar="clip")
    q.add_argument("--to", metavar="ID", help="the person these faces are (default: a new ID)")

    p = cmd("serve", c_serve, "run the review app (Basic Auth required)", (common, dry))
    p.add_argument("--port", type=int, help="port (default: CUTTER_REVIEW_PORT or 8089)")
    p.add_argument("--bind", help="address to listen on (default: all interfaces; 127.0.0.1 = only this computer)")

    p = cmd("feedback", c_feedback, "read the comments on a cut; post the agent's replies")
    fb = p.add_subparsers(dest="fb_cmd", required=True)
    q = fb.add_parser("fetch", help="copy the comments of the newest (or given) cut into .cutter/review/feedback-<cut>.json", parents=[common, dry])
    q.add_argument("cut", nargs="?"); q.add_argument("--status", choices=["open", "done", "rejected"]); q.add_argument("--output")
    q.add_argument("--local", metavar="DB", help="read this SQLite file instead of the server")
    q.add_argument("--data", help="with --local: folder with cuts/"); q.add_argument("--env")
    q = fb.add_parser("reply", help="set status and replies (database backup first, service restart afterwards)", parents=[common, dry])
    q.add_argument("cut", nargs="?")
    q.add_argument("--file", required=True, help='JSON: [{"id": 12, "status": "done", "reply": "…"}]')
    q.add_argument("--local", metavar="DB"); q.add_argument("--env"); q.add_argument("--no-restart", action="store_true")

    p = cmd("deploy", c_deploy, "copy the app, server files or cuts to the review server (settings in deploy.env)", (common, dry))
    p.add_argument("what", nargs="+", help="app, server and/or cuts (cut3 …)")
    p.add_argument("--env", help="deploy.env (default: project folder, then the cutter folder)")

    p = cmd("agent", c_agent, "set up the video-editing skill for Claude Code and Codex")
    ag = p.add_subparsers(dest="agent_cmd", required=True)
    q = ag.add_parser("setup", help="link skills/video-editing into ~/.claude/skills and/or ~/.agents/skills", parents=[common, dry])
    q.add_argument("--claude", action="store_true", help="~/.claude/skills/video-editing")
    q.add_argument("--codex", action="store_true", help="~/.agents/skills/video-editing")
    q.add_argument("--replace", action="store_true", help="replace existing links")

    p = cmd("migrate", c_migrate, "convert files from earlier (German-keyed) versions of the pipeline", (common, dry))
    p.add_argument("files", nargs="+"); p.add_argument("--output", help="target file (only with one input file)")

    p = cmd("demo", c_demo, "synthetic demo (test-pattern clips with tones, a click track as music)")
    ds = p.add_subparsers(dest="demo_cmd", required=True)
    q = ds.add_parser("media", help="create only the demo footage", parents=[common, dry]); q.add_argument("folder")
    q = ds.add_parser("cut", help="write a first edit list from clips.json (instead of the agent)", parents=[common, dry]); q.add_argument("cut", nargs="?", default="cut1")
    q = ds.add_parser("project", help="demo footage plus a finished project", parents=[common, dry]); q.add_argument("folder")

    p = cmd("tool", c_tool, "helpers: contact sheets of time windows, single frames, Whisper on excerpts")
    ts = p.add_subparsers(dest="tool_cmd", required=True)
    q = ts.add_parser("window", help="CLIP:START[:DURATION] … one row per window, 2 frames per second", parents=[common])
    q.add_argument("output"); q.add_argument("specs", nargs="+")
    q = ts.add_parser("frames", help='single frames as one sheet: "C058:5,15;C057:58"', parents=[common])
    q.add_argument("output"); q.add_argument("spec")
    q = ts.add_parser("asr", help="CLIP:FROM:TO[:chain] with Whisper (word times), chain = raw | full", parents=[common])
    q.add_argument("specs", nargs="+"); q.add_argument("--model", default="large-v3-turbo"); q.add_argument("--prompt")
    q.add_argument("--language", help="default: cutter.toml → language, else detected")
    return ap


def main(argv=None):
    ap = parser()
    a = ap.parse_args(argv)
    a.json = getattr(a, "json", False)
    a.project = getattr(a, "project", None)
    out = Output(a.json)
    os.environ["CUTTER_JSON"] = "1" if a.json else ""        # progress messages (util.log) then go to stderr
    if not getattr(a, "fn", None):
        ap.print_help()
        return 0
    try:
        a.fn(a, out)
    except CutterError as ex:
        if a.json:
            print(json.dumps({"error": ex.text, "code": ex.code, "hint": ex.hint}, ensure_ascii=False))
        print(f"Error: {ex.text}" + (f"\nHint: {ex.hint}" if ex.hint else ""), file=sys.stderr)
        sys.exit(ex.code)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        sys.exit(130)
    return 0


if __name__ == "__main__":
    main()
