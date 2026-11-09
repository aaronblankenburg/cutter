"""Set up the video-editing skill for Claude Code and Codex: symlinks to skills/video-editing of this checkout.

  Claude Code: ~/.claude/skills/video-editing   (or install the plugin: /plugin install cutter@cutter)
  Codex:       ~/.agents/skills/video-editing   (current Codex; older versions used $CODEX_HOME/skills, see docs)
This changes files in the home folder, so an agent asks the user before running it. Existing links pointing
elsewhere are only replaced with --replace; real directories are never removed.
"""
import os
import pathlib

from .util import CutterError, MISSING

SKILL = "video-editing"


def skill_dir():
    d = pathlib.Path(__file__).resolve().parents[2] / "skills" / SKILL
    if not (d / "SKILL.md").is_file():
        raise CutterError(f"Skill not found: {d}", MISSING, "Run this from a cutter checkout (e.g. ~/.local/share/cutter/bin/cutter)")
    return d


def setup(claude=False, codex=False, dry_run=False, replace=False, home=None):
    if not claude and not codex:
        claude = codex = True
    home = pathlib.Path(home or os.path.expanduser("~"))
    source = skill_dir()
    targets = []
    if claude:
        targets.append(("Claude Code", home / ".claude" / "skills" / SKILL))
    if codex:
        targets.append(("Codex", home / ".agents" / "skills" / SKILL))
    done, lines = [], []
    for tool, link in targets:
        state = "new"
        if link.is_symlink():
            if pathlib.Path(os.readlink(link)) == source or link.resolve() == source:
                state = "exists"
            elif replace:
                state = "replace"
            else:
                raise CutterError(f"{link} already points to {os.readlink(link)}", MISSING, "--replace replaces the link")
        elif link.exists():
            raise CutterError(f"{link} exists and is not a link; remove it yourself if it is not needed", MISSING)
        if not dry_run and state in ("new", "replace"):
            link.parent.mkdir(parents=True, exist_ok=True)
            if state == "replace":
                link.unlink()
            link.symlink_to(source, target_is_directory=True)
        done.append({"tool": tool, "link": str(link), "target": str(source), "state": state})
        lines.append(f"  {tool}: {link} -> {source} ({'unchanged' if state == 'exists' else ('dry run' if dry_run else state)})")
    checks = "Check: Claude Code lists video-editing under /skills (as a plugin: cutter:video-editing); Codex: /skills shows video-editing."
    return {"links": done, "dry_run": dry_run, "text": "\n".join((["Dry run:"] if dry_run else []) + lines + [checks])}
