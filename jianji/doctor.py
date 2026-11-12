"""`jian doctor`: is the install complete, and does your layer (~/.config/jianji) match this version of jian?"""
from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

from . import core
from .core import FFMPEG, LIST_FIELDS, MIGRATIONS, PKG, SCHEMA, _toml, global_config, profiles, style_path


def _keys(d: dict) -> set[str]:
    return {f"{sec}.{opt}" for sec, v in d.items() if isinstance(v, dict) for opt in v}


def check() -> tuple[list[str], list[str]]:
    """(problems, notes). A problem breaks or silently changes a run; a note is worth knowing."""
    bad, notes = [], []
    HOME, GLOBAL_CONFIG = core.HOME, core.GLOBAL_CONFIG
    if not HOME.exists():
        notes.append(f"no personal layer at {HOME}: built-in profiles and styles only (see README, 'Your layer')")
    s = global_config().get("schema")
    if GLOBAL_CONFIG.exists():
        if s is None:
            bad.append(f"{GLOBAL_CONFIG}: no schema line. Add `schema = {SCHEMA}` at the top.")
        elif s > SCHEMA:
            bad.append(f"your layer is schema {s}, this jian knows {SCHEMA}: update jian (git pull, then uv tool upgrade).")
        elif s < SCHEMA:
            steps = [f"  {k}: {MIGRATIONS.get(k, 'no manual change')}" for k in range(s + 1, SCHEMA + 1)]
            bad.append(f"your layer is schema {s}, jian expects {SCHEMA}. Change, then set `schema = {SCHEMA}`:\n"
                       + "\n".join(steps))
    known = set().union(*(_keys(_toml(f)) for f in (PKG / "profiles").glob("*.toml")))
    mine = [*(HOME / "profiles").glob("*.toml"), HOME / "brand.toml", *(HOME / "extensions").glob("*.toml")]
    for f in mine:
        d = _toml(f)
        for k in sorted(_keys(d) - known):
            bad.append(f"{f}: '{k}' isn't a setting of any built-in profile (renamed or removed?)")
        for k, v in d.items():
            if not isinstance(v, dict) and k not in LIST_FIELDS:
                bad.append(f"{f}: top-level '{k}' isn't used (settings go under a [section])")
    for k in sorted(_keys(global_config().get("defaults", {})) - known):
        bad.append(f"{GLOBAL_CONFIG} [defaults]: '{k}' isn't a setting of any built-in profile")
    for p in profiles():
        if not style_path(p):
            bad.append(f"profile '{p}' has no style guide: add {HOME / 'styles' / f'{p}.md'}")
    root = global_config().get("videos_root")
    if not root:
        notes.append(f"no videos_root in {GLOBAL_CONFIG}: `jian new <name>` needs a full path")
    elif not Path(root).expanduser().is_dir():
        bad.append(f"videos_root {root} doesn't exist (external drive not mounted?)")
    for k, v in global_config().get("hooks", {}).items():
        exe = Path((shlex.split(v) if isinstance(v, str) else v or [""])[0]).expanduser() if k.startswith("on_") else None
        if exe is not None and not shutil.which(str(exe)):
            bad.append(f"{GLOBAL_CONFIG} [hooks] {k}: {exe} isn't an executable (chmod +x, or the full path)")
    if not shutil.which(FFMPEG):
        bad.append("ffmpeg not found: brew install ffmpeg-full")
    else:
        filters = subprocess.run([FFMPEG, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        if " subtitles " not in filters:
            bad.append(f"{FFMPEG} has no libass (burned captions): brew install ffmpeg-full")
    if not shutil.which("swiftc"):
        notes.append("no swiftc: text placement and OCR need the Xcode command line tools (xcode-select --install)")
    skills = Path("~/.claude/skills").expanduser()
    for sk in ("jian-edit", "jian-revise"):
        if not (skills / sk / "SKILL.md").exists():
            notes.append(f"skill /{sk} not installed: ln -s {PKG.parent / '.claude' / 'skills' / sk} {skills / sk}")
    return bad, notes


def doctor():
    bad, notes = check()
    print(f"jian, layer schema {SCHEMA}: {core.HOME}  profiles: {', '.join(profiles())}")
    for n in notes:
        print(f"  note: {n}")
    for b in bad:
        print(f"  PROBLEM: {b}")
    print("ok" if not bad else f"{len(bad)} problem(s)")
    raise SystemExit(1 if bad else 0)
