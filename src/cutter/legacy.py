"""Legacy reader for the older German-keyed file formats, and the reverse mapping.

cutter's file formats use English keys. Projects edited with the earlier, German-keyed scripts keep working:
every reader in cutter passes its input through the functions here, which detect the old format and rename keys
(and enumerated values such as shot kinds) on the fly. `cutter migrate` rewrites old files permanently.

The complete mapping table is documented in docs/formats.md. Content strings (notes, subtitles, titles, names)
are data and are never translated. Unknown keys pass through unchanged.

Timelines built from a legacy edit list write their OTIO metadata with the legacy keys, so that existing timelines
are reproduced byte for byte (see `otio_meta_out`).
"""
import copy

# ---------------------------------------------------------------- edit.json

SHOT_KEYS = {"art": "kind", "notiz": "note", "ton": "audio", "untertitel": "subtitles", "einschnitte": "cutaways",
             "baustein": "block", "nr_vorher": "nr_previous", "takt_frei": "free_timing", "einblenden_s": "fade_in_s",
             "stabilisieren": "stabilize", "out_grenzen": "out_limits", "teil": "part", "schnitt_min": "cut_min",
             "schnitt_max": "cut_max"}
KIND_VALUES = {"dialog": "dialogue", "bild": "picture", "atmo": "ambience", "titel": "title", "schwarz": "black"}
AUDIO_KEYS = {"grund": "reason", "kette": "chain", "hochpass_hz": "highpass_hz", "rauschminderung": "noise_reduction",
              "sprache_mix": "speech_mix", "kompressor": "compressor", "musik_tiefer": "music_lower"}
CHAIN_VALUES = {"voll": "full", "sanft": "gentle"}
SUBTITLE_KEYS = {"von": "from", "bis": "to"}
CUTAWAY_KEYS = {"ab": "at", "notiz": "note"}
ANCHOR_KEYS = {"zeit": "time", "ende": "end", "abschnitt": "section", "kante": "edge"}
EDGE_VALUES = {"ende": "end"}
MUSIC_KEYS = {"titel": "title", "kuenstler": "artist", "lizenz": "license", "suche": "search", "datei": "file",
              "von": "from", "bis": "to", "quelle_start": "source_start", "notiz": "note",
              "taktanfang_erster": "first_downbeat", "ausblenden_s": "fade_out_s"}
MARKER_KEYS = {"zeit": "time", "dauer": "duration", "farbe": "color", "abschnitt": "section", "kante": "edge", "ende": "end"}
TOP_KEYS = {"titel": "title", "hinweis": "note", "externe_clips": "external_clips", "musik": "music", "marker": "markers",
            "abspann_musikzeilen": "credits_music_lines", "ton_hinweis": "audio_note", "schlusstext": "end_text",
            "optional": "optional"}
EXTERNAL_KEYS = {"datei": "file", "dauer": "duration"}
OPTIONAL_KEYS = {"notiz": "note", "passt_nach": "fits_after"}
INTRO_KEYS = {"karte": "map", "etappe": "route", "ueberblick": "overview", "karte_start": "map_start",
              "karte_dauer": "map_duration", "karte_ausblenden": "map_note", "titel_text": "title_text",
              "titel_von": "title_from", "titel_bis": "title_to", "karte_position": "map_position",
              "ueberblick_notiz": "overview_note", "karte_zoom": "map_zoom"}
POSITION_KEYS = {"notiz": "note"}
ZOOM_KEYS = {"start_skalierung": "start_scale", "start_mitte_karte": "start_center", "ziel_mitte_karte": "target_center",
             "etappe_mitte_karte": "route_center", "beschriftung_bis": "labels_until", "beschriftung_weg": "labels_gone",
             "zoom_von": "zoom_from", "zoom_bis": "zoom_to", "ausblenden_von": "fade_from", "ausblenden_bis": "fade_to",
             "ueberblendung_skalierung": "crossfade_scale", "hintergrund_weiss_bis": "background_white_until",
             "hintergrund_normal": "background_normal", "ueberblick": "overview", "beschriftung": "labels",
             "georeferenz": "georeference", "notiz": "note"}
OVERVIEW_KEYS = {"origin_karte": "origin", "faktor": "factor", "mitte_karte": "center"}
LABEL_KEYS = {"farbe": "color", "stil": "style", "sperren": "tracking", "rechts": "right"}
GEOREF_KEYS = {"k_px_pro_rad": "k_px_per_rad", "guete_iou_wasser": "fit_iou_water"}
ENDTEXT_KEYS = {"quelle": "source", "stil": "style", "musik_absenken_db": "music_dip_db", "tafeln": "cards"}
ENDSTYLE_KEYS = {"mitte_y": "center_y"}
BAND_KEYS = {"farbe": "color", "deckkraft": "opacity", "hoehe": "height"}
CARD_KEYS = {"ab": "from", "bis": "to"}


def _keys(d, m):
    return {m.get(k, k): v for k, v in d.items()}


def _inv(m):
    return {v: k for k, v in m.items()}


def is_legacy_edit(e):
    shots = [s for sec in e.get("sections", []) for s in sec.get("shots", [])]
    return "titel" in e or "musik" in e or any("art" in s or "notiz" in s for s in shots)


def _anchor(a, km=ANCHOR_KEYS, ev=EDGE_VALUES):
    a = _keys(a, km)
    if "edge" in a:
        a["edge"] = ev.get(a["edge"], a["edge"])
    return a


def audio_from_legacy(t):
    a = _keys(t, AUDIO_KEYS)
    if "chain" in a:
        a["chain"] = CHAIN_VALUES.get(a["chain"], a["chain"])
    return a


def audio_to_legacy(a):
    t = _keys(a, _inv(AUDIO_KEYS))
    if "kette" in t:
        t["kette"] = _inv(CHAIN_VALUES).get(t["kette"], t["kette"])
    return t


def shot_from_legacy(s):
    s = _keys(s, SHOT_KEYS)
    if "kind" in s:
        s["kind"] = KIND_VALUES.get(s["kind"], s["kind"])
    if isinstance(s.get("audio"), dict):
        s["audio"] = audio_from_legacy(s["audio"])
    if "subtitles" in s:
        s["subtitles"] = [_keys(u, SUBTITLE_KEYS) for u in s["subtitles"]]
    if "cutaways" in s:
        s["cutaways"] = [_keys(c, CUTAWAY_KEYS) for c in s["cutaways"]]
    return s


def edit_from_legacy(e):
    """Edit list with English keys. Returns (edit, was_legacy). Non-legacy input is returned unchanged (a copy)."""
    e = copy.deepcopy(e)
    if not is_legacy_edit(e):
        return e, False
    out = _keys(e, TOP_KEYS)
    out["sections"] = [{"name": sec["name"], "shots": [shot_from_legacy(s) for s in sec["shots"]]}
                       for sec in e.get("sections", [])]
    if "external_clips" in out:
        out["external_clips"] = {k: _keys(v, EXTERNAL_KEYS) for k, v in out["external_clips"].items()}
    if "optional" in out:
        out["optional"] = [_keys(o, OPTIONAL_KEYS) for o in out["optional"]]
    if "music" in out:
        mus = []
        for m in out["music"]:
            m = _keys(m, MUSIC_KEYS)
            for k in ("from", "to"):
                if isinstance(m.get(k), dict):
                    m[k] = _anchor(m[k])
            mus.append(m)
        out["music"] = mus
    if "markers" in out:
        out["markers"] = [_anchor(mk, MARKER_KEYS) for mk in out["markers"]]
    if isinstance(out.get("intro"), dict):
        i = _keys(out["intro"], INTRO_KEYS)
        if isinstance(i.get("map_position"), dict):
            i["map_position"] = _keys(i["map_position"], POSITION_KEYS)
        if isinstance(i.get("map_zoom"), dict):
            z = _keys(i["map_zoom"], ZOOM_KEYS)
            if isinstance(z.get("overview"), dict):
                z["overview"] = _keys(z["overview"], OVERVIEW_KEYS)
            if isinstance(z.get("labels"), list):
                z["labels"] = [_keys(l, LABEL_KEYS) for l in z["labels"]]
            if isinstance(z.get("georeference"), dict):
                z["georeference"] = _keys(z["georeference"], GEOREF_KEYS)
            i["map_zoom"] = z
        out["intro"] = i
    if isinstance(out.get("end_text"), dict):
        t = _keys(out["end_text"], ENDTEXT_KEYS)
        if isinstance(t.get("style"), dict):
            st = _keys(t["style"], ENDSTYLE_KEYS)
            if isinstance(st.get("band"), dict):
                st["band"] = _keys(st["band"], BAND_KEYS)
            t["style"] = st
        if isinstance(t.get("cards"), list):
            t["cards"] = [_keys(c, CARD_KEYS) for c in t["cards"]]
        out["end_text"] = t
    return out, True


# ---------------------------------------------------------------- OTIO metadata written by the timeline builder

OTIO_META_KEYS = {"section": "abschnitt", "kind": "art", "note": "notiz", "source_resolution": "quelle_aufloesung",
                  "scale_percent": "skalierung_prozent", "audio": "ton", "cutaway_of": "einschnitt_in"}


def otio_meta_out(meta, legacy):
    """Metadata dict for one OTIO item: English keys, or the legacy keys when the edit list was legacy."""
    if not legacy:
        return meta
    out = {OTIO_META_KEYS.get(k, k): v for k, v in meta.items()}
    if "art" in out:
        out["art"] = _inv(KIND_VALUES).get(out["art"], out["art"])
    if isinstance(out.get("ton"), dict):
        out["ton"] = audio_to_legacy(out["ton"])
    return out


def otio_meta_in(md):
    """Read OTIO item metadata written by either version: always English keys and values."""
    if not md:
        return {}
    out = {_inv(OTIO_META_KEYS).get(k, k): v for k, v in md.items()}
    if "kind" in out:
        out["kind"] = KIND_VALUES.get(out["kind"], out["kind"])
    if isinstance(out.get("audio"), dict):
        out["audio"] = audio_from_legacy(out["audio"])
    return out


# ---------------------------------------------------------------- decisions.json

DECISION_TOP = {"fassung": "version", "vergleich": "compared_to", "stand": "date", "titel": "title",
                "einleitung": "intro", "eintraege": "entries"}
DECISION_ENTRY = {"titel": "title", "warum": "why", "frage": "question", "nr_bis": "nr_to", "schnitt_vor": "cut_before",
                  "musik": "music", "t_bis": "t_to", "bezug": "refs"}
REF_KEYS = {"fassung": "version"}


def decisions_from_legacy(d):
    if "eintraege" not in d and "fassung" not in d:
        return copy.deepcopy(d)
    out = _keys(d, DECISION_TOP)
    out["entries"] = []
    for e in d.get("eintraege", []):
        e = _keys(e, DECISION_ENTRY)
        if "refs" in e:
            e["refs"] = [_keys(r, REF_KEYS) for r in e["refs"]]
        out["entries"].append(e)
    return out


# ---------------------------------------------------------------- review timeline.json

TL_TOP = {"fassung": "version", "titel": "title", "dauer": "duration", "abschnitte": "sections", "einstellungen": "shots",
          "schnitte": "edit_points", "musik": "music", "untertitel": "subtitles", "marker": "markers", "erzeugt": "created",
          "quelle": "source", "entscheidungen": "decisions", "vorschau": "thumbnails"}
TL_SECTION = {"ende": "end"}
TL_SHOT = {"ende": "end", "abschnitt": "section", "art": "kind", "datei": "file", "quelle_in": "source_in",
           "quelle_out": "source_out", "notiz": "note", "baustein": "block", "nr_vorher": "nr_previous", "ton_db": "audio_db",
           "musik_tiefer": "music_lower", "einschnitte": "cutaways", "untertitel": "subtitles", "saetze": "sentences"}
TL_CUTAWAY = {"ende": "end", "datei": "file", "quelle_in": "source_in", "quelle_out": "source_out", "notiz": "note"}
TL_SPAN = {"ende": "end"}
TL_CUT = {"vor": "before", "nach": "after", "art": "kind"}
CUT_KINDS = {"einstellung": "shot", "einschnitt_an": "cutaway_in", "einschnitt_aus": "cutaway_out"}
TL_MUSIC = {"titel": "title", "kuenstler": "artist", "lizenz": "license", "ende": "end", "quelle_start": "source_start",
            "notiz": "note", "datei": "file", "ausblenden_s": "fade_out_s"}
TL_MARKER = {"dauer": "duration"}
TL_SOURCE = {"video_art": "video_kind", "entscheidungen": "decisions"}
VIDEO_KINDS = {"grob": "rough"}
TL_VIDEO = {"datei": "file", "breite": "width", "hoehe": "height", "dauer": "duration", "nummern_eingebrannt": "numbers_burned_in"}
TL_THUMBS = {"intervall": "interval", "breite": "width", "hoehe": "height", "anzahl": "count"}
TL_DECISIONS = {"titel": "title", "einleitung": "intro", "vergleich": "compared_to", "stand": "date", "eintraege": "entries"}
TL_DECISION = {"titel": "title", "t_bis": "t_to", "warum": "why", "frage": "question", "bezug": "refs"}


def timeline_from_legacy(t):
    """Review timeline.json with English keys (the reverse is not needed: cutter only writes the English format)."""
    if "einstellungen" not in t and "fassung" not in t:
        return copy.deepcopy(t)
    o = _keys(t, TL_TOP)
    o["sections"] = [_keys(a, TL_SECTION) for a in t.get("abschnitte", [])]
    shots = []
    for s in t.get("einstellungen", []):
        s = _keys(s, TL_SHOT)
        s["kind"] = KIND_VALUES.get(s.get("kind"), s.get("kind"))
        s["cutaways"] = [_keys(c, TL_CUTAWAY) for c in s.get("cutaways", [])]
        s["subtitles"] = [_keys(u, TL_SPAN) for u in s.get("subtitles", [])]
        s["sentences"] = [_keys(u, TL_SPAN) for u in s.get("sentences", [])]
        shots.append(s)
    o["shots"] = shots
    edits = []
    for c in t.get("schnitte", []):
        c = _keys(c, TL_CUT)
        c["kind"] = CUT_KINDS.get(c.get("kind"), c.get("kind"))
        edits.append(c)
    o["edit_points"] = edits
    o["music"] = [_keys(m, TL_MUSIC) for m in t.get("musik", [])]
    o["subtitles"] = [_keys(u, TL_SPAN) for u in t.get("untertitel", [])]
    o["markers"] = [_keys(m, TL_MARKER) for m in t.get("marker", [])]
    if isinstance(o.get("source"), dict):
        src = _keys(o["source"], TL_SOURCE)
        src["video_kind"] = VIDEO_KINDS.get(src.get("video_kind"), src.get("video_kind"))
        o["source"] = src
    if isinstance(o.get("video"), dict):
        o["video"] = _keys(o["video"], TL_VIDEO)
    if isinstance(o.get("thumbnails"), dict):
        o["thumbnails"] = _keys(o["thumbnails"], TL_THUMBS)
    if isinstance(o.get("decisions"), dict):
        d = _keys(o["decisions"], TL_DECISIONS)
        ents = []
        for e in d.get("entries", []):
            e = _keys(e, TL_DECISION)
            e["refs"] = [_keys(r, REF_KEYS) for r in e.get("refs", [])]
            ents.append(e)
        d["entries"] = ents
        o["decisions"] = d
    return o


# ---------------------------------------------------------------- image analysis per clip (analysis/<id>.json)

ANALYSIS_SECOND = {"schaerfe": "sharpness", "wackeln": "shake", "belichtung": "exposure", "bewegung": "motion",
                   "schwenk": "pan", "schlagworte": "tags", "einstellung": "shot_size"}
EXPOSURE_KEYS = {"ausgefressen": "clipped", "zu_dunkel": "too_dark", "mittel": "mean"}


def analysis_from_legacy(a):
    if not a or "per_second" not in a:
        return a
    out = dict(a)
    ps = []
    for p in a["per_second"]:
        p = _keys(p, ANALYSIS_SECOND)
        if isinstance(p.get("exposure"), dict):
            p["exposure"] = _keys(p["exposure"], EXPOSURE_KEYS)
        ps.append(p)
    out["per_second"] = ps
    return out


# ---------------------------------------------------------------- clips.json

CREATED_SOURCE = {"dateiname": "filename", "metadaten": "metadata", "dateidatum": "file_date"}


def clips_from_legacy(clips):
    out = []
    for c in clips:
        c = dict(c)
        if c.get("created_source") in CREATED_SOURCE:
            c["created_source"] = CREATED_SOURCE[c["created_source"]]
        out.append(c)
    return out


# ---------------------------------------------------------------- music analysis (music.json)

def music_from_legacy(m):
    out = {}
    for cue, d in m.items():
        d = dict(d)
        if "dur" in d and "duration" not in d:
            d["duration"] = d.pop("dur")
        out[cue] = d
    return out


# ---------------------------------------------------------------- feedback export

FEEDBACK_TOP = {"fassung": "cut", "titel": "title", "dauer": "duration", "quelle": "source", "exportiert": "exported",
                "personen": "people", "anzahl": "counts", "aktionen": "actions", "eintraege": "entries"}
FEEDBACK_ENTRY = {"t_bis": "t_to", "nr_bis": "nr_to", "quelle_t": "source_t", "einschnitt": "cutaway", "abschnitt": "section",
                  "musik": "music", "schnitt": "edit_point", "aktion": "action", "aktion_text": "action_text", "reaktion": "reaction",
                  "antwort": "reply", "erstellt": "created", "geaendert": "changed"}
STATUS_VALUES = {"offen": "open", "erledigt": "done", "verworfen": "rejected"}
ACTION_VALUES = {"raus": "remove", "kuerzer": "shorter", "laenger": "longer", "schnitt_frueher": "cut_earlier",
                 "schnitt_spaeter": "cut_later", "anderes_bild": "other_shot", "musik_leiser": "music_softer",
                 "musik_lauter": "music_louder", "unverstaendlich": "unclear", "gefaellt": "like", "frage": "question"}


def feedback_entry_from_legacy(e):
    e = _keys(e, FEEDBACK_ENTRY)
    e["status"] = STATUS_VALUES.get(e.get("status"), e.get("status"))
    e["action"] = ACTION_VALUES.get(e.get("action"), e.get("action"))
    if isinstance(e.get("cutaway"), dict):
        e["cutaway"] = _keys(e["cutaway"], {"quelle_t": "source_t"})
    if isinstance(e.get("music"), dict):
        e["music"] = _keys(e["music"], {"titel": "title", "t_stueck": "t_track"})
    if isinstance(e.get("edit_point"), dict):
        c = _keys(e["edit_point"], TL_CUT)
        c["kind"] = CUT_KINDS.get(c.get("kind"), c.get("kind"))
        e["edit_point"] = c
    return e


def feedback_from_legacy(d):
    if "eintraege" not in d:
        return copy.deepcopy(d)
    o = _keys(d, FEEDBACK_TOP)
    o["entries"] = [feedback_entry_from_legacy(e) for e in d.get("eintraege", [])]
    if isinstance(o.get("counts"), dict):
        o["counts"] = {("total" if k == "gesamt" else STATUS_VALUES.get(k, k)): v for k, v in o["counts"].items()}
    if isinstance(o.get("people"), list):
        o["people"] = [{"name": p.get("name"), "entries": p.get("eintraege", p.get("entries"))} for p in o["people"]]
    if isinstance(o.get("actions"), dict):
        o["actions"] = {ACTION_VALUES.get(k, k): v for k, v in o["actions"].items()}
    if isinstance(o.get("source"), dict):
        o["source"] = _keys(o["source"], TL_SOURCE)
    o["format"] = 2
    return o


# ---------------------------------------------------------------- migrate

def detect(data):
    """Which legacy format is this? 'edit', 'decisions', 'timeline', 'feedback', 'music', 'analysis', 'clips' or None."""
    if isinstance(data, list):
        legacy = any(isinstance(c, dict) and c.get("created_source") in CREATED_SOURCE for c in data)
        return "clips" if legacy else None
    if not isinstance(data, dict):
        return None
    if "einstellungen" in data:
        return "timeline"
    if "sections" in data and is_legacy_edit(data):
        return "edit"
    if "eintraege" in data and ("aktionen" in data or "exportiert" in data):
        return "feedback"
    if "eintraege" in data:
        return "decisions"
    if "per_second" in data and any("schaerfe" in p for p in data["per_second"]):
        return "analysis"
    if data and all(isinstance(v, dict) and "beats" in v for v in data.values()) and any("dur" in v for v in data.values()):
        return "music"
    return None


def migrate(data):
    """Rewrite legacy data with English keys. Returns (new_data, kind) or (data, None) if nothing to do."""
    kind = detect(data)
    if kind == "edit":
        return edit_from_legacy(data)[0], kind
    if kind == "decisions":
        return decisions_from_legacy(data), kind
    if kind == "timeline":
        return timeline_from_legacy(data), kind
    if kind == "feedback":
        return feedback_from_legacy(data), kind
    if kind == "analysis":
        return analysis_from_legacy(data), kind
    if kind == "music":
        return music_from_legacy(data), kind
    if kind == "clips":
        return clips_from_legacy(data), kind
    return data, None
