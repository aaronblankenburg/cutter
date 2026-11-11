# cutter

**Cut films with an AI agent.** Point Claude Code or Codex at a folder of footage and say:

> *Cut a film from the files in this folder.*

cutter gives the agent everything it needs to do that:
- footage analysis
- beat-synced editing
- timelines
- finishing and export with FilmCraft
- a review web app where people watch each cut and leave feedback at exact moments

cutter grew out of a real documentary project, in which an AI agent and two people went through seven cuts of a travel film. This repository packages the pipeline, the review app and the lessons from that project, so the next film starts where that one ended.

> **Status:** young.
> - Linux (x86_64): used on one real film.
> - macOS (Apple Silicon): supported, but not yet tested on real hardware.
> - Windows: works only through WSL2 and is untested.

**Contents:** [How it works](#how-it-works) · [What you need](#what-you-need) · [Setup](#setup-once-per-computer) · [Your first film](#your-first-film) · [Review app](#reviewing-cuts-in-the-browser) · [FilmCraft](#filmcraft) · [Music](#music-and-licenses) · [Privacy](#privacy-and-safety) · [macOS](#notes-for-macos) · [Commands](#command-reference) · [Storage](#where-your-film-is-stored) · [Project folder](#project-folder) · [Updating](#updating) · [Troubleshooting](#troubleshooting) · [License](#license)

## How it works

You talk to your agent, and the agent runs cutter.

1. **You ask.** For example: *"Cut a film from the files in ~/Footage/Iceland."* German works too: *„Schneide aus den Dateien in Ordner xy einen Film."*
2. **The agent gets the pipeline.** It loads the `video-editing` skill, runs `cutter doctor` and installs whatever is missing. Before large downloads it asks you.
3. **A short briefing.** The agent asks at most five questions, and skips any your request already answers:
   - purpose and audience
   - target length
   - style
   - music
   - people to leave out and other no-gos
4. **Analysis.** cutter does three things:
   - transcribes all speech locally with Whisper, with a timestamp for every word
   - makes contact sheets and film strips of every clip
   - measures the audio

   The agent then reads the transcripts and looks at the contact sheets itself.
5. **First cut.** The agent:
   - builds the story: sections, a common thread, dialogue scenes, B-roll, intro and ending
   - picks music with a compatible license and snaps the cuts to the beat
   - writes the edit plan
6. **Render.** cutter:
   - builds the timeline as OpenTimelineIO, FCP7 XML and SRT subtitles
   - has FilmCraft add titles, subtitles, audio cleanup and music ducking
   - exports an MP4 with the subtitles burned in
7. **Review.** cutter starts the review app and prints a link you can share in your home network. You and your reviewers watch the cut and comment at exact moments.
8. **Next cut.** You say *"Apply the feedback."* The agent:
   - reads the comments on the newest cut
   - makes the changes
   - explains each change at the spot in the film where it happens
   - replies to every comment

   Repeat until the film is done.

Every cut can be reproduced. The agent records its changes in the project folder, as a small script plus a decisions file.

## What you need

| | Linux | macOS | Windows |
|---|---|---|---|
| Docker | Docker Engine, with your user in the `docker` group | Docker Desktop | Docker Desktop with WSL2 (untested) |
| Agent | Claude Code or Codex | Claude Code or Codex | Claude Code or Codex inside WSL2 |
| Git | yes | yes (Xcode command line tools) | inside WSL2 |

- **FilmCraft is included** in the Docker image. Install the desktop app only if you want to fine-tune cuts by hand.
- **No GPU needed.** Transcription and rendering run on the CPU. On Linux, an NVIDIA GPU can speed this up, see [docs/deploy.md](docs/deploy.md#gpu).
- **Disk space:**
  - the Docker image (about 1.8 GB on disk, about 0.7 GB download)
  - the Whisper speech model (about 1.6 GB, downloaded on the first analysis)
  - room for analysis files and exports
- **Time:** measured on a laptop CPU (12th-gen Intel Core i5):
  - FilmCraft renders 1080p at about 6 frames per second, and at about 2 in stabilised scenes. A 10-minute film therefore takes roughly an hour to export.
  - Whisper needs about 20 seconds to load its model. The transcription itself ran much faster than real time in a short test
    (10 seconds of speech in 0.6 seconds), but hours of footage still take a while.
  - The agent runs long jobs in the background.

Other agents that read `AGENTS.md` or Agent Skills may work as well. Only Claude Code and Codex are tested.

## Setup (once per computer)

### Claude Code

```
/plugin marketplace add aaronblankenburg/cutter
/plugin install cutter@cutter
```

The skill appears as `cutter:video-editing`. Claude loads it on its own when you ask for a film. You can also call it with `/cutter:video-editing`.

### Codex

Tell Codex:

> *Set up github.com/aaronblankenburg/cutter for me.*

Codex asks first, then clones cutter to `~/.local/share/cutter` and links the skill to `~/.agents/skills/video-editing`. To do the same yourself:

```sh
git clone https://github.com/aaronblankenburg/cutter ~/.local/share/cutter
~/.local/share/cutter/bin/cutter agent setup --codex
```

Type `/skills` in Codex: `video-editing` should be listed. Call it with `$video-editing`.

Alternatively, ask Codex's built-in `$skill-installer` to install the skill from `https://github.com/aaronblankenburg/cutter/tree/main/skills/video-editing`. The skill fetches the rest of cutter the first time you use it.

### Without setting up the skill

Just say:

> *Use github.com/aaronblankenburg/cutter to cut a film from the files in folder xy.*

The agent reads [AGENTS.md](AGENTS.md) in this repository and sets itself up.

### Command line only, without an agent

```sh
git clone https://github.com/aaronblankenburg/cutter ~/.local/share/cutter
ln -s ~/.local/share/cutter/bin/cutter ~/.local/bin/cutter   # optional: puts cutter on your PATH
cutter doctor
```

`bin/cutter` is a small launcher:
- It runs every command inside the Docker image `ghcr.io/aaronblankenburg/cutter`.
- On first use it pulls the image. If pulling fails, it builds the image locally.
- It mounts your project folder read-write and your footage read-only.

## Your first film

With an agent, one sentence is enough. Name the footage folder and, if you like, the essentials:

> *Cut a 10-minute film from the files in ~/Footage/Iceland. Audience: friends and family. Upbeat, with subtitles. Music: CC BY only.*

The agent works in a project folder of its own: the current directory if it is empty, otherwise a new folder inside it. It never works inside the footage folder.

The same steps by hand:

```sh
cutter new iceland-film --media ~/Footage/Iceland
cd iceland-film
cutter doctor
cutter analyze                       # transcripts, contact sheets, usable ranges, camera moves, sound
# now write cuts/cut1/edit.json (format: docs/formats.md) – normally the agent's job
cutter music license music/*.mp3     # check licenses against cutter.toml
cutter music analyze music/*.mp3     # beats, bars, loudness
cutter retime cut1                   # snap cuts to the music
cutter timeline cut1                 # .otio, .fcp7.xml, .srt
cutter filmcraft build cut1          # FilmCraft project: titles, subtitles, audio, music
cutter filmcraft export cut1         # exports/cut1.mp4 with burned-in subtitles
cutter review-data cut1
cutter review password alice
cutter serve
```

[docs/workflow.md](docs/workflow.md) describes the whole cycle, including how feedback becomes the next cut.

## Reviewing cuts in the browser

The review app shows each cut together with its timeline.
- **Comments:** Reviewers click into the video and comment on a moment or a range.
- **Agent notes:** The agent's change notes appear in violet at the places they refer to. Its replies appear under each comment.
- **"Now at this point":** lists everything about the current moment. While the video plays, the list follows along.
- **New cuts:** When a new cut is published, the app opens it automatically.
- **Phones:** The app works on phones; iPhone Safari is tested.

**Start it:**

```sh
cutter review password alice     # once per reviewer; asks for the password, stores only a scrypt hash
cutter serve
```

cutter prints the addresses, for example:

```
http://localhost:8089
http://192.168.1.23:8089
http://my-mac.local:8089
```

Send the network address to your reviewers, together with their name and password. All they need is a browser in the same network.

- **Port:** change it with `cutter serve --port 9000` or `CUTTER_REVIEW_PORT=9000`.
- **Only on your computer:** `cutter serve --bind 127.0.0.1`.
- **Language:** English by default. Set `ui_language = "de"` in `cutter.toml` for German.
- **Passwords:** stored only as hashes, in `.cutter/review/htpasswd` inside the project folder. They never go into a repository.

> **Security:** the app speaks plain HTTP. Anyone in the same network could read passwords and watch the film. Share only in networks you trust: your home Wi-Fi, not a café. To share beyond your network, deploy the app to a server with HTTPS using `cutter deploy`, see [docs/deploy.md](docs/deploy.md).

## FilmCraft

[FilmCraft](https://github.com/storytold/filmcraft) is an open-source video editor (MIT or Apache-2.0). Its command-line engine runs headless, without a window. The cutter Docker image already contains it, so you don't need to install anything.

- **Fine-tuning by hand:** Every cut gets its own FilmCraft project, e.g. `cuts/cut3/cut3.fcproj`. Open it in the FilmCraft desktop app; downloads for Linux, macOS and Windows are on the [releases page](https://github.com/storytold/filmcraft/releases). Tell the agent about manual changes before the next cut, so they are not lost.
- **Live mode (optional):** `cutter filmcraft gui` starts the desktop app with its control channel. With `--bridge`, cutter then works in the open app instead of headless.
- **No FilmCraft?** The timelines (`.otio`, `.fcp7.xml`, `.srt`) open in DaVinci Resolve, Premiere Pro, Kdenlive and other editors.

cutter is not affiliated with FilmCraft or the ArtCraft team.

## Music and licenses

`cutter music license` reads the license from each file's tags and checks it against `music.allowed_licenses` in `cutter.toml`. By default only CC0 and CC BY are allowed. Add CC BY-NC or CC BY-NC-SA only if your film is non-commercial.

Two traps:
- **ND (no derivatives):** Music under ND usually can't be laid under a film, because putting music to pictures counts as adapting it.
- **BY-SA (share-alike):** Music under BY-SA forces its license onto your film. That clashes with other licenses such as BY-NC-SA.

If the tags say nothing, the agent asks you instead of guessing.

## Privacy and safety

- **Footage:** mounted read-only. cutter never writes to it.
- **Processing:** transcription, analysis and rendering run on your computer. cutter itself sends nothing anywhere. It only downloads the Docker image and the Whisper model (from Hugging Face). For an overview map, the agent fetches map outlines from Natural Earth.
- **Your agent is a cloud service.** Transcripts, contact sheets and frames the agent looks at go to its provider (Anthropic or OpenAI) as part of the conversation. Keep that in mind with private footage.
- **The agent asks first** before:
  - large downloads
  - `sudo`
  - deploying
  - deleting anything
  - changing your shell or agent configuration
- **Secrets:** passwords are stored only as hashes and never in a repository. Server settings live in a git-ignored `deploy.env`.

## Notes for macOS

- **Docker Desktop:** install it. The image runs natively on Apple Silicon (arm64), FilmCraft included.
- **Footage on an external drive:** Docker Desktop must be allowed to read it, under Settings → Resources → File sharing. `/Volumes` is usually shared already.
- **Firewall:** the first time you share the review app, macOS asks whether to accept incoming connections. Allow it.
- **No GPU:** Docker on macOS can't use the GPU, so transcription and rendering run on the CPU. Large amounts of footage take longer.
- **Fine-tuning by hand:** install the native FilmCraft app (`filmcraft-*-macos-universal.dmg`).

**Windows:** use WSL2 with Docker Desktop and run your agent inside WSL2. This setup is untested.

## Command reference

| Command | What it does |
|---|---|
| `cutter new <folder> --media <path>` | Create a project folder with `cutter.toml`, `AGENTS.md` and `CLAUDE.md` |
| `cutter doctor [--json]` | Check Docker, image, speech model, FilmCraft, footage access and disk space, and show how to fix what's missing |
| `cutter analyze` | Clip list, transcripts with word timestamps, contact sheets, film strips; per clip the usable ranges, camera moves, people, sound and place |
| `cutter music analyze` / `license` | Beats, bars and loudness; license check |
| `cutter plan <cut> [--new]` | Write the next cut's edit list from `plan.py` (the feedback as code) |
| `cutter retime <cut>` | Snap cuts to the music |
| `cutter timeline <cut>` | Build OTIO, FCP7 XML and SRT |
| `cutter filmcraft build` / `export <cut>` | Build the FilmCraft project; export the MP4 with burned-in subtitles |
| `cutter filmcraft gui` | Start the FilmCraft desktop app with its control channel (optional) |
| `cutter map` | Overview and route maps for intros |
| `cutter review-data <cut>` | Prepare a cut for the review app |
| `cutter review password <name>` | Add a reviewer or change their password; `--generate` makes one and prints it once, `--delete` removes the reviewer |
| `cutter serve` | Run the review app |
| `cutter feedback fetch` / `reply` | Read the comments; post the agent's replies |
| `cutter deploy` | Publish the review app on a server (SSH, settings in `deploy.env`) |
| `cutter agent setup [--claude] [--codex]` | Link the skill into Claude Code and/or Codex |
| `cutter migrate <file>` | Convert files from earlier versions of the pipeline |
| `cutter update` | Update cutter and its Docker image |
| `cutter demo project <folder>` | A synthetic demo: generated footage and music, ready to cut (see [examples/demo](examples/demo/README.md)) |

Every command that writes, renders or deploys accepts `--dry-run`. For details: `cutter <command> --help`.

## Where your film is stored

Everything stays on your computer, in places you choose:

| What | Where |
|---|---|
| Your footage | where it is. cutter only reads it, through the folders named in `cutter.toml`. |
| Everything cutter makes | the project folder: analysis, music analysis, cuts, timelines, FilmCraft projects, exports. |
| Comments of your reviewers | `.cutter/review/` in the project folder, or on your server if you deployed the app. `cutter feedback fetch` copies them into the project in both cases. |
| Reviewer passwords | `.cutter/review/htpasswd`, as scrypt hashes only. |
| cutter itself | `~/.local/share/cutter` (or wherever you cloned it), plus the Docker image. |
| The Whisper model | the Docker volume `cutter-models` (remove it with `docker volume rm cutter-models`). |

Back up the project folder and you have the whole film history. If the footage moves, change its path in `cutter.toml`; see [docs/workflow.md](docs/workflow.md#moving-footage-other-computers).

## Project folder

```
iceland-film/
├── cutter.toml      settings: footage folders, music licenses, review app, language
├── AGENTS.md        instructions for your agent (CLAUDE.md points to it)
├── analysis/        clip list, transcripts, contact sheets, film strips
├── music/           your music files and music.json (beats, bars, loudness)
├── cuts/
│   ├── cut1/        edit.json, decisions.json, timeline files, FilmCraft project
│   └── cut2/        plan.py (changes since cut1), edit.json, decisions.json, …
├── exports/         rendered films
└── .cutter/         review data and passwords – keep private
```

## Updating

```sh
cutter update
```

This pulls the newest cutter and its Docker image. In Claude Code, update the plugin from the `/plugin` menu.

## Troubleshooting

Start with `cutter doctor`. It lists every missing piece together with the command that fixes it.

- **"Port already in use":** start on another port, e.g. `cutter serve --port 9000`.
- **Reviewers can't connect:**
  - Are they in the same network?
  - Did the firewall ask and get an answer?
  - Try the IP address instead of the `.local` name.
- **Video won't play or seek on a phone:** use the address cutter prints. Don't open the files directly.
- **The agent doesn't find the skill:** in Codex, type `/skills`; in Claude Code, type `/plugin`. Check that `video-editing` is installed.

## Development

Contributor notes are in [AGENTS.md](AGENTS.md) and [docs/](docs/): workflow, file formats, FilmCraft commands, deployment, and how to run the tests.

## License

MIT, see [LICENSE](LICENSE).

The Docker image also contains third-party software under its own licenses, among them:
- FilmCraft (MIT or Apache-2.0)
- ffmpeg (GPL, from Debian)
- the Python libraries listed in `requirements.txt`

Whisper models are downloaded at run time. The FilmCraft and ArtCraft names and logos belong to their owners.
