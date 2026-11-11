# Development

Rules for contributors (and agents) are in [AGENTS.md](../AGENTS.md). This page explains the tests, the image and
releases.

## Running the code

- **Through the image** (what users run): `bin/cutter <command>`. `CUTTER_DEV=1 bin/cutter …` mounts this
  checkout's `src/` into the image, so changes take effect without rebuilding.
- **Natively:** Python 3.11+ with `pip install -r requirements.txt` and `pip install --no-deps scenedetect==0.7.2`,
  ffmpeg on the PATH, then `PYTHONPATH=src python -m cutter …`. `FILMCRAFT_CLI=/path/to/filmcraft-cli` points to a
  FilmCraft CLI (from the release tarball).

## Tests

```sh
python -m unittest discover -s tests -v
```

Standard-library `unittest`. Tests that need ffmpeg, the tool packages, `filmcraft-cli`, dash, Docker or Chrome
skip themselves when these are missing. Test servers listen only on 127.0.0.1, ports 8290–8299; passwords and
`htpasswd` files are created at run time in temporary folders. Nothing reaches a real server: the tests set
`CUTTER_TEST=1`, which makes cutter ignore the checkout's `deploy.env` and refuse every ssh, rsync and deploy that is
not a dry run; subprocesses get an empty HOME and no ssh agent (`test_feedback_deploy.NoServerInTests` checks this).

| File | What |
|---|---|
| `test_golden_demo.py` | the synthetic demo from a fixed analysis through retime, timeline, review data and the FilmCraft batch, compared with `tests/golden/demo/expected/`; portability of footage roots |
| `test_music.py` | license names and verdicts, the license check on file tags, tempo and downbeat of the demo track |
| `test_config_legacy.py` | cutter.toml, footage roots, the reader for old German-keyed files, `cutter migrate` |
| `test_review_server.py` | Basic Auth (401/200/503), range requests (206/416), the comment API, `review password`, `serve` refusing to start without a password |
| `test_feedback_deploy.py` | `feedback fetch`/`reply` on a local database, `deploy --dry-run`, server templates |
| `test_launcher_agent.py` | the launcher under dash with a Docker stub (mounts, paths, ports, macOS branches), `agent setup` with a temporary HOME, the skill and plugin manifests |
| `test_footage_quality.py` | camera moves, usable ranges and flaws from samples; wind against an engine; GoPro GPMF and DJI flight logs built at run time; the quality bar; the whole analysis on a generated clip with a known pan and blur (needs OpenCV, so it runs in the image) |
| `test_cli.py` | help for every command, JSON results and errors, `new`, `plan`, `doctor`, dry runs |
| `test_docs.py` | the legacy table in formats.md, relative links |
| `test_filmcraft_headless.py` | builds the demo's FilmCraft project with the real CLI and renders frames (needs `filmcraft-cli`) |
| `test_local_reference.py` | optional, see below |
| `test_e2e_launcher.py` | opt-in (`CUTTER_E2E=1`): the whole cycle through the launcher and the image, ending with the review app |
| `test_browser.py` | opt-in (`CUTTER_BROWSER=1`): runs the browser tests below |

**Golden files:** after an intended change of the outputs run `python tests/golden_demo.py --update` and review the
diff. The inputs (`tests/golden/demo/input`) are a fixed analysis of the generated demo footage; the click track is
generated, so no media is checked in.

**Inside the image:**

```sh
docker run --rm --entrypoint python ghcr.io/aaronblankenburg/cutter:0.1.0 -m unittest discover -s /opt/cutter/tests
```

### Local reference tests

`tests/test_local_reference.py` compares cutter with the files of a real earlier project – byte for byte for OTIO,
FCP 7 XML and SRT, after key mapping for the edit list and the review timeline, and a headless FilmCraft run against
the original batch. It runs only when `tests/local/reference.json` exists (git-ignored, paths only):

```json
{"project": "/path/to/folder/with/cutter.json", "edit": "…/edit.json", "edit_plan": "…/edit_plan.json",
 "reference_timeline": {"otio": "…", "xml": "…", "srt": "…"},
 "original_files": {"otio": "…", "xml": "…", "srt": "…"}, "known_otio_differences": 0,
 "review_timeline": "…/timeline.json", "decisions": "…/decisions.json", "transcripts": "…/transcripts",
 "filmcraft_config": "…/filmcraft.json", "filmcraft_base": "…/base.fcproj", "filmcraft_batch": "…/batch.jsonl",
 "filmcraft_cli": "/path/to/filmcraft-cli"}
```

Keys that are missing skip the corresponding test. Nothing in the referenced folders is written.

### Browser tests

`tests/browser/` tests the review app in headless Chrome with playwright-core, against synthetic cuts generated at
run time (`make_fixture.py`: three cuts with test-pattern videos, timelines, decisions, a random password):
`test_app.js`, `test_comments.js`, `test_new_cut.js`, `test_decisions.js` and `test_width.js` in English, then
`test_smoke_de.js` and `test_width.js` in German – 174 checks.

```sh
cd tests/browser && npm ci          # once (playwright-core, no browser download)
python run.py                       # CHROME=/path/to/chrome if it is not /usr/bin/google-chrome
```

`run.py` starts the review server on 127.0.0.1 (a free port of 8295–8299) with an empty database for every file;
`--only test_app.js`, `--keep` and `--shots DIR` help with debugging. Instead of `npm ci`, `NODE_PATH` may point to
a `node_modules` folder that contains playwright-core.

## Agent integration

- **Claude Code:** the repository is its own plugin marketplace (`.claude-plugin/marketplace.json`, plugin root `./`);
  the plugin contributes `skills/video-editing` as `cutter:video-editing`. Without the plugin,
  `bin/cutter agent setup --claude` links the skill to `~/.claude/skills/video-editing`.
- **Codex:** `bin/cutter agent setup --codex` links the skill to `~/.agents/skills/video-editing`; inside this
  repository Codex also finds it through `.agents/skills/video-editing`. Older Codex versions read skills from
  `$CODEX_HOME/skills` (default `~/.codex/skills`); link it there by hand if `/skills` does not list it:
  `ln -s ~/.local/share/cutter/skills/video-editing ~/.codex/skills/video-editing`.
- The skill refers to `docs/` with relative links (`../../docs/…`), which resolve in the plugin, through the links
  and in the checkout. A skill installed on its own (for example with Codex's `$skill-installer`) sets up the
  checkout first.

## The image

One image for everything: Python 3.12 (Debian slim, pinned by digest), ffmpeg, fonts, the pinned packages of
`requirements.txt`, FilmCraft's CLI (see [filmcraft.md](filmcraft.md)), ssh and rsync for deploy, and cutter
itself. It runs as the caller's UID:GID (the entrypoint adds a passwd entry for ssh); `/models` is the Hugging Face
cache (the volume `cutter-models`).

```sh
bin/cutter image build              # docker build -t ghcr.io/<owner>/cutter:<version> .
```

`.github/workflows/image.yml` builds `linux/amd64` and `linux/arm64` with QEMU and pushes to GHCR on tags `v*` (and
on manual runs). After the first push, make the package public on GitHub (Package settings → Change visibility),
otherwise `docker pull` needs a login. The arm64 image is built only there; it has not been run on Apple Silicon yet.

## Releases

The version is in `src/cutter/__init__.py` (`VERSION`), `pyproject.toml`, `.claude-plugin/*.json` and the image tag
in `compose.yaml`. The launcher pulls `ghcr.io/<owner>/cutter:<VERSION>`, so a release is: bump the version
everywhere, commit, tag `vX.Y.Z`, push the tag; the workflow publishes the image.
