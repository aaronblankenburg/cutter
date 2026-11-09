---
name: video-editing
description: Cut a film from a folder of the user's own footage with cutter, then turn reviewers' feedback into the next cut. Covers setup, footage analysis (Whisper transcripts, contact sheets), story and edit plan, music with a checked license and cuts on the beat, timelines (OTIO, FCP7 XML, SRT), FilmCraft finishing and export, and the review web app. Use when the user asks to make, edit or revise a film or video from their footage, for example "cut a film from the files in this folder", "make the next cut", "apply the feedback"; in German for example "schneide einen Film", "schneide aus den Dateien in Ordner xy einen Film", "neue Fassung", "Feedback umsetzen".
license: MIT
---

# Video editing with cutter

You cut the film; cutter does the heavy lifting. Talk to the user in the user's language (German if they write
German). Keep cutter's own terms (cut1, edit.json, decisions.json) as they are.

## 1. Set up (once per computer)

cutter lives in a git checkout, by default `${CUTTER_HOME:-~/.local/share/cutter}`; every command runs through its
launcher `bin/cutter`, which runs the tools in a Docker image. Below, `cutter` means that launcher.

1. If this skill came with the checkout (Claude Code plugin, or a link made by `cutter agent setup`), the checkout
   is two folders above this file. Otherwise ask the user, then clone it:
   `git clone https://github.com/aaronblankenburg/cutter ~/.local/share/cutter`
2. Run `cutter doctor --json`. Each check that is not ok has a `fix`. Work through the required ones.
3. **Ask first** before you:
   - download anything large: the Docker image (about 0.7 GB download, 1.8 GB on disk), the Whisper model
     (about 1.6 GB, fetched by the first `cutter analyze`)
   - use `sudo`, install system packages, or add the user to the `docker` group
   - change the shell configuration, `~/.claude`, `~/.codex` or `~/.agents` (for example `cutter agent setup`)
   - deploy anything (`cutter deploy`), start the review app in the network (`cutter serve`)
   - delete anything
4. Never use `--bridge` or the FilmCraft desktop app unless the user asks; headless is the default.

## 2. Rules that always hold

- **Never change the footage.** It is mounted read-only; never write, move or rename anything in it.
- **Project folder:** one per film, never inside the footage folder. Default: the current directory if it is
  empty, otherwise a new folder `<name>-film` inside it. Create it with
  `cutter new <folder> --media <footage folder>` (several `--media` allowed).
- Long jobs (analysis of hours of footage, exports) run in the background; tell the user how long they take.
- Every command accepts `--json` (one JSON object on stdout); everything that writes, renders or deploys accepts
  `--dry-run`. Exit codes: 0 ok, 1 failed, 2 wrong usage, 3 something missing, 4 a check found a problem.
- Transcripts and frames you look at go to your provider. If the footage looks private, mention it once.

## 3. The first cut

Follow [references/first-cut.md](references/first-cut.md): a short briefing (at most five questions), analysis,
the story, music (license first, then beats), the edit list, render, review. The edit list format is in
[docs/formats.md](../../docs/formats.md), the whole cycle in [docs/workflow.md](../../docs/workflow.md).

```sh
cutter new <folder> --media <footage>       # then cd <folder>
cutter doctor
cutter analyze                              # transcripts, contact sheets, film strips -> analysis/
cutter music license && cutter music analyze
# write cuts/cut1/edit.json and cuts/cut1/decisions.json
cutter retime cut1 && cutter timeline cut1
cutter filmcraft build cut1 && cutter filmcraft export cut1
cutter review-data cut1
cutter review password <name> && cutter serve   # ask first; the user types the password
```

## 4. Every further cut

Follow [references/next-cut.md](references/next-cut.md): fetch the feedback on the newest cut, write
`cuts/cut<n>/plan.py` (the changes as code) and `decisions.json` (why, at the right moment in the film), render,
publish, and reply to every comment.

## 5. When something fails

Read the error and its hint (`--json` gives `error`, `code`, `hint`). `cutter doctor` lists missing pieces.
FilmCraft details: [docs/filmcraft.md](../../docs/filmcraft.md). Servers and GPUs: [docs/deploy.md](../../docs/deploy.md).
If the docs are not next to this skill (installed on its own), set up the checkout first (section 1).
