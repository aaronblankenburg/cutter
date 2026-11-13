# The next cut: from feedback to cut n+1

Reviewers comment on the newest cut in the review app. Each further cut applies their feedback, explains every
change at the spot in the film where it happens, and answers every comment.

## 1. Fetch

```sh
cutter feedback fetch            # newest cut -> .cutter/review/feedback-<cut>.json
```

Only feedback on the **newest** cut counts; comments on older cuts were answered then. Read every entry: time,
shot number (`nr`), action (shorter, remove, other_shot, music_softer, question, …), text, person.

## 2. Decide

Group the comments by place. For each, decide: do it, do it differently, or not (with a reason). When reviewers
disagree, say so and ask the user. Changes made by hand in the FilmCraft desktop app since the last cut: ask the
user, they would be lost when the project is rebuilt.

## 3. Write the plan

```sh
cutter plan cut3 --new           # cuts/cut3/plan.py from the template, reading cut2's edit list
```

Edit `plan.py`: every change is a few lines of code with a comment naming the person, the entry number and why.
Then `cutter plan cut3` writes `cuts/cut3/edit.json`. The plan keeps the old shot numbers as `nr_previous`.

## 4. Explain

Write `cuts/cut3/decisions.json` (format: [docs/formats.md](../../../docs/formats.md#decisionsjson)): one entry
per change or decision, placed in the new cut (`t`/`t_to`, `nr`/`nr_to`, `cut_before` or `music`), with `title`,
`text`, `why`, an optional `question` for the reviewers and `refs` to the comments it answers. The review app shows
these notes in violet at their places.

## 5. Render and publish

```sh
cutter retime cut3 && cutter timeline cut3     # with proxies: cutter timeline cut3 --proxies (a draft)
cutter filmcraft build cut3      # starts from cut2's FilmCraft project; cut2.fcproj stays as it is
cutter filmcraft export cut3
cutter review-data cut3
cutter deploy cut3               # only with a review server (deploy.env), and only after asking
```

When the user accepts a cut that was a draft from proxies, build that cut from the footage before handing it over:
`cutter timeline cut3 && cutter filmcraft build cut3 --overwrite && cutter filmcraft export cut3`.

With a local review app, `cutter serve` serves the new cut right away; the reviewers' apps open it by themselves.

## 6. Reply to every comment

Write a JSON list and post it:

```json
[{"id": 12, "status": "done", "reply": "Shortened by 1.2 s, see the note at 2:14."},
 {"id": 15, "status": "rejected", "reply": "Kept: the scene explains why we turn back."}]
```

```sh
cutter feedback reply cut2 --file replies.json   # backs up the database first
```

A rejected comment always gets a reason. Replies are short and say where to look in the new cut.
