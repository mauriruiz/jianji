"""Paths, config, caching, metrics and subprocess helpers shared by every stage."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

PKG = Path(__file__).resolve().parent  # built-in profiles/ and styles/ ship inside the package
HOME = Path(os.environ.get("JIANJI_HOME", "~/.config/jianji")).expanduser()  # your layer: overrides the built-ins
GLOBAL_CONFIG = HOME / "config.toml"
SCHEMA = 1  # format of your layer; when it changes, MIGRATIONS says what to edit and `jian doctor` shows it
MIGRATIONS: dict[int, str] = {}
WORK, LEGACY = "jianji", "vedit"  # per-video work folder; folders made before the rename keep the old name
_FULL = Path("/opt/homebrew/opt/ffmpeg-full/bin")
# ffmpeg-full carries libass/drawtext for burn-ins; plain Homebrew ffmpeg does not.
FFMPEG = str(_FULL / "ffmpeg") if (_FULL / "ffmpeg").exists() else "ffmpeg"
FFPROBE = str(_FULL / "ffprobe") if (_FULL / "ffprobe").exists() else "ffprobe"


def die(msg: str):
    raise SystemExit(f"jian: {msg}")


def run(cmd, **kw) -> subprocess.CompletedProcess:
    cmd = [str(c) for c in cmd]
    p = subprocess.run(cmd, capture_output=True, **kw)
    if p.returncode:
        err = p.stderr.decode(errors="replace") if isinstance(p.stderr, bytes) else p.stderr
        die(f"{Path(cmd[0]).name} failed ({p.returncode}): {err.strip()[-1200:]}")
    return p


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    tmp.replace(path)  # atomic: an interrupted run never leaves a half-written cache entry
    return path


def key(*parts) -> str:
    return hashlib.blake2b(json.dumps(parts, sort_keys=True, default=str).encode(), digest_size=8).hexdigest()


def fingerprint(path: Path) -> str:
    """Size + mtime + three sampled 1 MB blocks: cheap on 50 GB files, changes when the file does."""
    st = path.stat()
    h = hashlib.blake2b(f"{st.st_size}:{st.st_mtime_ns}".encode(), digest_size=8)
    mb = 1 << 20
    with path.open("rb") as f:
        for off in sorted({0, max(0, st.st_size // 2 - mb // 2), max(0, st.st_size - mb)}):
            f.seek(off)
            h.update(f.read(mb))
    return h.hexdigest()


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _scalar(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v.strip().strip('"').strip("'")


BRIEF_FIELDS = ("profile", "extends", "language", "languages", "format", "target_length", "glossary", "never_show", "audience",
                "structure", "must_keep", "notes")
LIST_FIELDS = {"extends", "glossary", "language", "languages", "must_keep", "never_show"}


def read_brief(path: Path) -> tuple[dict, str]:
    """Optional brief.md. Front matter between `---` fences: `key: value` lines, `# comments`, dotted keys override
    config (`cut.max_pause: 1.0`). List fields take `a, b, c` or an indented `- item` list on the following lines."""
    if not path.exists():
        return {}, ""
    text = path.read_text()
    m = re.match(r"---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if not m:
        return {}, text
    meta, last = {}, None
    for line in m.group(1).splitlines():
        s = line.split(" #", 1)[0].strip() if not line.lstrip().startswith("#") else ""
        if not s:
            continue
        if s.startswith("- ") and last in LIST_FIELDS:
            meta.setdefault(last, [])
            meta[last] = (meta[last] if isinstance(meta[last], list) else []) + [s[2:].strip().strip('"\'')]
        elif ":" in s:
            k, v = s.split(":", 1)
            k, v = k.strip(), v.strip()
            last = k
            if k in LIST_FIELDS:
                v = v.strip("[]")
                meta[k] = [x.strip().strip('"\'') for x in v.split(",") if x.strip()] if v else []
            else:
                meta[k] = _scalar(v)
    return meta, text[m.end():]


def _toml(path: Path) -> dict:
    return tomllib.loads(path.read_text()) if path.exists() else {}


def profiles() -> list[str]:
    return sorted({f.stem for d in (PKG, HOME) for f in (d / "profiles").glob("*.toml")})


def load_profile(name: str) -> dict:
    """Built-in profile < your profile of the same name (only the keys it sets) < your brand.toml."""
    return merge(merge(_toml(PKG / "profiles" / f"{name}.toml"), _toml(HOME / "profiles" / f"{name}.toml")),
                 _toml(HOME / "brand.toml"))


def style_path(profile: str) -> Path | None:
    """The editorial rules for a profile: yours (<HOME>/styles/) if you wrote them, else the built-in guide."""
    return next((d / "styles" / f"{profile}.md" for d in (HOME, PKG) if (d / "styles" / f"{profile}.md").exists()), None)


def user_glossary() -> list[str]:
    """<HOME>/glossary.txt: one term per line, for every video (names you say often)."""
    f = HOME / "glossary.txt"
    lines = f.read_text().splitlines() if f.exists() else []
    return [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]


def extension(name: str) -> dict:
    """<HOME>/extensions/<name>.toml, pulled in by `extends: <name>` in a brief: settings for a series of videos."""
    f = HOME / "extensions" / f"{name}.toml"
    if not f.exists():
        die(f"extension '{name}' not found: {f}")
    return _toml(f)


def schema_warning() -> str | None:
    s = global_config().get("schema")
    if not GLOBAL_CONFIG.exists() or s == SCHEMA:
        return None
    if s is None:
        return f"{GLOBAL_CONFIG} has no schema line: add `schema = {SCHEMA}`"
    if s > SCHEMA:
        return f"your layer is schema {s} but this jian knows {SCHEMA}: update jian"
    return f"your layer is schema {s}, jian expects {SCHEMA}: run `jian doctor` for what to change"


def validate_brief(meta: dict, cfg: dict, path: Path):
    """Fail early with something you can act on, instead of a KeyError ten minutes into prep."""
    problems = []
    if "profile" in meta and str(meta["profile"]) not in profiles():
        problems.append(f"profile '{meta['profile']}' doesn't exist; valid profiles: {', '.join(profiles())}")
    for k, v in meta.items():
        if "." in k:
            sec, opt = k.split(".", 1)
            if opt not in cfg.get(sec, {}):
                problems.append(f"'{k}' isn't a setting of this profile (sections: {', '.join(sorted(cfg))})")
        elif k not in BRIEF_FIELDS:
            problems.append(f"unknown field '{k}'; valid fields: {', '.join(BRIEF_FIELDS)}, or section.option")
    for code in meta.get("languages") or meta.get("language") or []:
        if not re.fullmatch(r"[a-z]{2}", code):
            problems.append(f"language '{code}' should be a two-letter code like en or es")
    if problems:
        die(f"{path} has problems:\n  " + "\n  ".join(problems))


def global_config() -> dict:
    return tomllib.loads(GLOBAL_CONFIG.read_text()) if GLOBAL_CONFIG.exists() else {}


def resolve_video(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_dir():
        return p.resolve()
    root = os.environ.get("JIANJI_VIDEOS_ROOT") or global_config().get("videos_root")
    if not root:
        die(f"'{arg}' is not a folder and no videos_root is set (JIANJI_VIDEOS_ROOT or {GLOBAL_CONFIG})")
    p = Path(root).expanduser() / arg
    if not p.is_dir():
        die(f"video folder not found: {p}")
    return p.resolve()


class Project:
    """A video folder. jian only ever writes inside <folder>/jianji/ (or vedit/, from before the rename): footage and
    brief.md are yours."""

    def __init__(self, arg: str):
        self.dir = resolve_video(arg)
        self.name = self.dir.name
        self.out = next((self.dir / n for n in (WORK, LEGACY) if (self.dir / n).is_dir()), self.dir / WORK)
        self.footage = self.dir / "footage"
        if not self.footage.is_dir():
            # footage dumped at the folder root: read it where it is, never move it
            from .media import MEDIA_EXT
            if not any(f.suffix.lower() in MEDIA_EXT for f in self.dir.iterdir() if f.is_file()):
                die(f"no footage in {self.dir}: put the raw files in footage/ (or at the folder root)")
            self.footage = self.dir
        self.out.mkdir(exist_ok=True)
        brief = self.dir / "brief.md"
        self.brief_meta, self.brief = read_brief(brief)
        local = self.out / "config.toml"
        own = _toml(local)  # jian's per-video settings, brief untouched
        self.profile = str(own.pop("profile", None) or self.brief_meta.get("profile", "explainer"))
        if self.profile not in profiles():
            die(f"{brief}: profile '{self.profile}' doesn't exist; valid profiles: {', '.join(profiles())}")
        if (w := schema_warning()):
            print(f"jian: {w}", file=sys.stderr)
        ext = [extension(x) for x in self.brief_meta.get("extends", [])]
        cfg = merge(load_profile(self.profile), global_config().get("defaults", {}))
        for e in ext:
            cfg = merge(cfg, {k: v for k, v in e.items() if k not in LIST_FIELDS})
        cfg = merge(cfg, own)
        validate_brief(self.brief_meta, cfg, brief)
        for k, v in self.brief_meta.items():
            if "." in k:
                sec, opt = k.split(".", 1)
                cfg.setdefault(sec, {})[opt] = v
        langs = self.brief_meta.get("languages") or self.brief_meta.get("language")
        if langs:
            cfg["asr"]["languages"] = langs
        # names and terms to spell right: transcription hint (new transcripts) and caption/text post-pass
        for f, mine in (("glossary", user_glossary()), ("never_show", [])):
            cfg[f] = list(dict.fromkeys([*cfg.get(f, []), *mine, *(x for e in ext for x in e.get(f, [])),
                                         *self.brief_meta.get(f, [])]))
        self.cfg = cfg

    def path(self, *parts) -> Path:
        p = self.out.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def edits(self) -> list[Path]:
        return sorted((self.out / "edits").glob("edit.v[0-9][0-9][0-9].json"))

    def latest_edit(self) -> Path:
        e = self.edits()
        if not e:
            die(f"no edit yet in {self.out / 'edits'}")
        return e[-1]

    def ensure_space(self, need_bytes: float, what: str):
        free = shutil.disk_usage(self.out).free
        floor = float(self.cfg.get("disk", {}).get("min_free_gb", 20)) * 1e9
        if free - need_bytes < floor:
            die(f"{what} needs ~{need_bytes / 1e9:.1f} GB but {self.out} has {free / 1e9:.1f} GB free "
                f"and must keep {floor / 1e9:.0f} GB (disk.min_free_gb). Free space or move the video folder.")

    @contextmanager
    def timed(self, stage: str, **info):
        t = time.time()
        yield
        sec = round(time.time() - t, 1)
        path = self.out / "metrics.json"
        m = read_json(path, {"stages": [], "rounds": 0, "review_minutes": []})
        m["stages"].append({"stage": stage, **info, "sec": sec, "at": datetime.now().isoformat(timespec="seconds")})
        write_json(path, m)
        print(f"  {stage} {' '.join(f'{k}={v}' for k, v in info.items())} {sec}s", file=sys.stderr)


def version_of(path: Path) -> int:
    return int(re.search(r"\.v(\d{3})\.", path.name).group(1))
