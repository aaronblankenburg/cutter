# cutter – notes for agents

This file is for any coding agent (Claude Code reads it through `CLAUDE.md`, Codex reads it directly).

## For agents: start here

**The user wants a film cut** (for example *"Use github.com/aaronblankenburg/cutter to cut a film from the files in
folder xy"*): read [skills/video-editing/SKILL.md](skills/video-editing/SKILL.md) and follow it. It covers the
setup (clone to `~/.local/share/cutter`, `bin/cutter doctor`), what to ask the user before doing it, the first
cut and every cut after that. Talk to the user in the user's language.

To make the skill available permanently, ask the user, then run `bin/cutter agent setup --claude` (Claude Code)
or `bin/cutter agent setup --codex` (Codex). In Claude Code the plugin is the alternative:
`/plugin marketplace add aaronblankenburg/cutter`, then `/plugin install cutter@cutter`.

Always:
- Never change the footage; it is mounted read-only.
- Ask before large downloads, `sudo`, changes to the shell or agent configuration, deploying and deleting.
- Use `--json` to read results and `--dry-run` to preview anything that writes, renders or deploys.

## Working on cutter

### Layout

| Path | What |
|---|---|
| `bin/cutter` | launcher: runs every command in the Docker image, same-path mounts (POSIX sh) |
| `src/cutter/cli.py` | the command line; each command imports its module lazily |
| `src/cutter/` | analyze, image_analysis (with quality, audio_analysis, telemetry, people), proxies, music, retime, timeline, filmcraft, review_data, maps, feedback, deploy, doctor, legacy … |
| `src/cutter/review_app/` | review app: `server.py` (standard library only), `static/` (vanilla JS, `i18n.js`) |
| `src/cutter/templates/` | project files (`cutter new`, `cutter plan`) and server files (`cutter deploy server`) |
| `skills/video-editing/` | the agent skill (also the Claude Code plugin's skill; `.agents/skills` links to it) |
| `docs/` | workflow, formats, FilmCraft, deployment, development – the single source for details |
| `tests/` | unit and golden tests (unittest), browser tests (`tests/browser`), local reference tests |
| `Dockerfile`, `compose.yaml` | the one image: tools, FilmCraft CLI, review app |

### Rules

- **English** for code, identifiers, comments, docs, CLI messages, tests and commit messages. The review app has
  English and German strings in `static/i18n.js`: every new string needs both.
- **Formats** use English keys (`docs/formats.md`). Files of the earlier German-keyed pipeline are read through
  `legacy.py` and converted with `cutter migrate`; never write the old keys.
- **CLI conventions:** every command takes `--json` (exactly one JSON object on stdout, messages on stderr);
  everything that writes, renders or deploys takes `--dry-run`. Errors are `CutterError(text, code, hint)` with the
  exit codes 0 ok, 1 failed, 2 usage, 3 missing, 4 a check found a problem.
- **Launcher:** POSIX sh that runs under dash and with the BSD tools of macOS (no `readlink -f`, no `sed -i`, no
  bash arrays). Tests stub Docker and the macOS commands.
- **Paths:** clips are stored relative to named footage roots (`[media]` in cutter.toml); timelines are built from
  them, so a project survives moving its footage. Everything runs at the same absolute paths in the container.
- **Public repository:** never commit `deploy.env`, `htpasswd`, `*.sqlite`, feedback exports, tokens, footage,
  music or real project data, and no personal names, host names, IP addresses or e-mail addresses.

### Tests

```sh
python -m unittest discover -s tests -v       # needs the packages of requirements.txt, ffmpeg; the rest skips itself
python tests/golden_demo.py --update          # after an intended change of outputs; review the diff
CUTTER_DEV=1 bin/cutter …                     # run this checkout's code in the image without rebuilding
```

Test servers listen only on 127.0.0.1, ports 8290–8299, with passwords made at run time. Browser tests never run
against real data. Details, the browser tests and the optional local reference tests: `docs/development.md`.
