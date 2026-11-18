# Non-interactive mode

A tool (for example the web app Cutlery) may run you without a person at the keyboard: `claude -p` in a container,
one session per cut. You know you are in this mode when the project folder contains **`briefing.md`**. Nobody will
answer a question, so never ask one and never wait for an answer.

## What is different

- **Setup is done.** `cutter` is installed in the container (`CUTTER_IN_CONTAINER=1`); there is no `bin/cutter`
  launcher and no checkout to clone. Models are downloaded on first use without asking. Do not run fixes that need
  `sudo`, do not change `~/.claude` or the shell.
- **The briefing is `briefing.md`.** It holds what the questions of first-cut.md would have asked: purpose and
  audience, target length, style, music wishes, no-gos and people who must not appear, plus the files the user gave
  (storyboard, images, music with their licences). It is written in the user's language; write your decisions and
  replies in that language.
- **The user's selection** (`selects.json`, also summarised in briefing.md): favourites to use where they fit,
  rejects to leave out (first-cut.md, “The story”).
- **Decide missing points yourself** and record every assumption in the cut's `decisions.json` as an entry with
  `"kind": "assumption"`: a short `title`, `text` (what you assumed) and `why`. The review shows them, so the user
  can correct you in the next cut. Examples: target length not given, style unclear, which take of two to use.
- **People:** use the names the tool set (`cutter people list`); do not ask who is who. Unnamed people are described
  neutrally. Never show a person the briefing excludes.
- **Music:** use only tracks whose licence is stated (briefing.md or the file's tags) and allowed by cutter.toml.
  A track without a clear licence is left out; record that as an assumption. If the briefing allows searching,
  search only the sources it names.
- **Reviewers disagree:** decide, and record the decision as an assumption that names both comments.
- **Not in this mode:** `cutter serve`, `cutter deploy`, `cutter review password`, the FilmCraft desktop app,
  deleting anything you did not create in this session. The tool publishes the cut; your last step is
  `cutter review-data <cut>`.
- **Answer every review comment** of the previous cut (next-cut.md, step "Reply") in the language of that comment,
  general ones (`"t": null`) and choices among alternatives (`"alternative"`) included.
- **Alternatives** are how the user, who may not know the footage any more, sees what else there was: record the
  real candidates (first-cut.md, “Alternatives”), with `why` texts in the user's language.

## Done means

- first cut: `cuts/cut1/edit.json` and `decisions.json` (plus `alternatives.json` where you weighed other clips
  or ranges), the timeline, the FilmCraft export and the review data;
- next cut: the same for the new cut, plus the replies to all comments of the previous one.

End with a short summary of the cut (length, sections, music, assumptions). If you cannot finish (for example the
analysis is incomplete), say exactly what is missing and stop; the tool shows your last message to the user.
