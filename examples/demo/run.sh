#!/bin/sh
# The cutter demo, end to end: synthetic footage (test patterns with tones), a project, analysis, a first cut on a
# drum loop with a known tempo, timelines, a FilmCraft project and export, review data. Nothing is downloaded except
# the Docker image (asked first) and, for the transcripts, the Whisper model (about 1.6 GB; skip it with
# NO_TRANSCRIPTS=1). Everything lands next to this script in footage/ and film/ (both git-ignored).
#   examples/demo/run.sh            then: cd examples/demo/film && ../../../bin/cutter review password me && ../../../bin/cutter serve
set -eu
here=$(cd -- "$(dirname -- "$0")" && pwd -P)
cutter=$here/../../bin/cutter
cd "$here"
[ -d footage ] || "$cutter" demo media footage
[ -f film/cutter.toml ] || "$cutter" new film --media footage --title "cutter demo" --language en
cd film
"$cutter" doctor
if [ "${NO_TRANSCRIPTS:-}" = 1 ]; then "$cutter" analyze --no-transcripts; else "$cutter" analyze; fi
"$cutter" demo cut                 # the edit list an agent would write: cuts/cut1/edit.json, decisions.json, music
"$cutter" music license
"$cutter" music analyze
"$cutter" retime cut1
"$cutter" timeline cut1
"$cutter" filmcraft build cut1 --overwrite
"$cutter" filmcraft export cut1
"$cutter" review-data cut1
echo "Done. Film: $here/film/exports/cut1.mp4 – FilmCraft project: $here/film/cuts/cut1/cut1.fcproj"
