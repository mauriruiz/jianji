"""jian: the deterministic half of the pipeline. Never calls an LLM.

  jian new <name> [--profile P]     create <videos_root>/<name>/footage/ and a brief.md template
  jian ingest [file] [--name N]     a finished recording -> a dated video folder linked to it (the file is never touched)
  jian prep <video>                 probe, transcribe, pack (cached; resumes where it stopped)
  jian map <video>                  a long stream, minute by minute (speech, screen, files, cues, privacy): stream.md
  jian words <video> <src> <a> <b>  word-level ids for a range
  jian sheet <video> <src> [a] [b]  contact sheet (time-stamped frames) of a source or range
  jian compile <video> [--edit F]   check an edit without rendering
  jian cut <video> [--edit F]       compile + render + QA for the latest edit
  jian qa <video> [--v N]           rerun QA
  jian cuts <video> [--v N]         cut reel: every join with 1.5 s each side, labelled, for a quick audit
  jian texts <video> [--v N]        text reel: every story line and callout in context, time and chars/s
  jian clean <video> [--dry-run]    delete what jian regenerates (audio, render pieces, old render versions)
  jian resolve <video> [--v N]      the cut as a DaVinci Resolve FCPXML on the original footage + IMPORT.md (cut does it too)
  jian pull <video> [file]          Resolve's exported timeline back as the next edit version, your changes locked
  jian diff <video> [--v N]         what you changed in Resolve, from a pulled version
  jian at <video> <tc|tc-tc>...     what's at a review-render timecode
  jian status <video>               what exists, what's next
  jian log <video> <key> <value>    record review_minutes, feedback etc. in metrics.json
  jian report [root]                scoreboard: review minutes, versions, QA, machine time, per video
  jian queue [--json] [--all]       shorts waiting for your yes or no (--announce: tell your on_queue_ready hook)
  jian approve <id> [--force]       a short into your upload folder as a Resolve project (never a final render)
  jian reject <id>                  a short out of the queue (nothing is deleted)
  jian hook <event> [KEY=VALUE]...  run your hook for an event by hand (to try it, or from your own scripts)

Shorts out of a long video: compile, cut, qa, cuts, texts, resolve, at and status take `--short <n>` and work in
<video>/jianji/shorts/<n>/ (its own edits and renders, the video's transcripts and cache; profile `short`).
  jian doctor                       check the install and your layer (~/.config/jianji) against this version

Hooks: `[hooks] on_ingest | on_queue_ready | on_approve | on_reject | on_error = "command"` in your layer's
config.toml; the facts arrive as JIAN_* variables.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from fractions import Fraction
from pathlib import Path

import numpy as np

from .core import Project, die, key, read_json, version_of, write_json


def _tr_path(p: Project, sid: str, e: dict) -> Path:
    from .transcribe import VERSION
    return p.path("cache", "transcripts", f"{sid}.{key(e['fp'], p.cfg['asr'], VERSION)}.json")


def prep(p: Project):
    from .broll import Vision, broll_md, candidates, describe, sheet
    from .media import build_catalog, load_wav, wav16k
    from .pack import catalog_md, transcript_md
    from .transcribe import Engine, transcribe_audio
    with p.timed("probe"):
        cat = build_catalog(p)
    eng, trs = None, {}
    for sid, e in cat["sources"].items():
        path = _tr_path(p, sid, e)
        if path.exists():
            trs[sid] = read_json(path)
            continue
        wav = wav16k(p, sid, e)
        if wav is None:
            continue
        eng = eng or Engine(p.cfg["asr"], p.cfg["glossary"])
        with p.timed("transcribe", src=sid, dur=round(e["dur"])):
            trs[sid] = transcribe_audio(load_wav(wav), eng)
        write_json(path, trs[sid])
    bcfg = p.cfg.get("broll", {})
    vis, descs = None, {}
    for sid in candidates(cat, trs):
        e = cat["sources"][sid]
        if bcfg.get("vlm", True):
            vis = vis or Vision(bcfg["vlm_model"])
            descs[sid] = describe(p, sid, e, vis)
        else:
            img, _ = sheet(p, sid, e, frames=int(bcfg.get("sheet_frames", 16)))
            descs[sid] = {"sheet": str(img.relative_to(p.dir)), "text": "(vision model off: open the sheet)"}
    (p.out / "broll.md").write_text(broll_md(p.name, cat, descs))
    (p.out / "transcript.md").write_text(transcript_md(p.name, cat, trs))
    (p.out / "catalog.md").write_text(catalog_md(p.name, cat, trs))
    print(f"{len(cat['sources'])} sources, {sum(len(t['words']) for t in trs.values())} words, "
          f"{sum(len(t['cues']) for t in trs.values())} cues, {len(descs)} B-roll candidates -> "
          f"{p.out / 'transcript.md'}, {p.out / 'broll.md'}")


def load_sources(p: Project, with_audio: bool = False) -> tuple[dict, dict]:
    from .media import envelope, load_wav, wav16k
    cat = read_json(p.out / "catalog.json") or die("run `jian prep` first")
    sources = {}
    for sid, e in cat["sources"].items():
        tr = read_json(_tr_path(p, sid, e), {"words": [], "voiced": [], "cues": []})
        s = {**{k: e.get(k) for k in ("dur", "fps", "w", "h")}, **tr}
        wav = wav16k(p, sid, e)
        if wav:
            envf = p.path("cache", "env", f"{sid}.{e['fp']}.npy")
            if envf.exists() and not with_audio:
                s["env"] = np.load(envf)
            else:
                audio = load_wav(wav)
                s["env"] = envelope(audio)
                np.save(envf, s["env"])
                if with_audio:
                    s["audio"] = audio
        else:
            s["env"] = np.full(int(e["dur"] * 100) + 1, -90.0)
        notes = sorted(p.out.joinpath("cache", "vlm").glob(f"{sid}.*.json"), key=lambda f: f.stat().st_mtime)
        if notes:  # the vision model's note: a first guess at the shot type
            s["note"] = read_json(notes[-1])["text"]
        sources[sid] = s
    return cat, sources


def _edit(p: Project, arg: str | None) -> tuple[dict, int]:
    path = Path(arg) if arg else p.latest_edit()
    if arg and not re.search(r"\.v\d{3}\.json$", path.name):
        die("edit files are named edit.vNNN.json")
    return json.loads(path.read_text()), version_of(path)


def compile_edit(p: Project, edit_arg: str | None, sources: dict) -> tuple[dict, int]:
    from .compile import EditError, add_outro, compile_timeline
    from .media import motion
    edit, v = _edit(p, edit_arg)
    cat = read_json(p.out / "catalog.json")
    for c in edit.get("clips", []) + edit.get("overlays", []):  # picture shots: where their action settles
        if "in" in c and c.get("src") in sources and "motion" not in sources[c["src"]]:
            sources[c["src"]]["motion"] = motion(p, c["src"], cat["sources"][c["src"]])
    try:
        tl = compile_timeline(edit, sources, p.cfg["cut"], p.cfg.get("timeline"), p.cfg.get("texts"), p.cfg.get("shots"),
                              p.cfg.get("layouts"), p.cfg.get("hook"))
        outro = p.cfg.get("outro") or {}
        if p.profile in outro.get("profiles", ["short"]):  # your closing line, on the profiles you want it on
            tl = add_outro(tl, outro, p.cfg.get("texts"))
        if p.short and p.cfg.get("shorts"):  # the length you want of a short cut out of a long video
            from .compile import judge_length
            off = judge_length(tl["stats"]["seconds"], p.cfg["shorts"])
            if off:
                tl["warnings"].append(f"length: {off[1]}" + (" (QA fails it)" if off[0] == "fail" else ""))
    except EditError as e:
        die(f"edit v{v:03d} rejected: {e}")
    write_json(p.path("renders", f"timeline.v{v:03d}.json"), tl)
    st = tl["stats"]
    print(f"v{v:03d}: {st['seconds']}s, {st['segments']} pieces, removed {st['fillers']} fillers, "
          f"{st['voiced']} voiced gaps, shortened {st['pauses']} pauses")
    for w in tl["warnings"]:
        print(f"  warning: {w}")
    return tl, v


def _previous(p: Project, v: int) -> int | None:
    """The latest earlier version that was rendered."""
    d = p.out / "renders"
    return next((u for u in range(v - 1, 0, -1)
                 if (d / f"timeline.v{u:03d}.json").exists() and (d / f"master.v{u:03d}.mp4").exists()), None)


def cut(p: Project, edit_arg: str | None, qa: bool = True):
    from .render import render, same_picture_and_sound
    cat, sources = load_sources(p, with_audio=qa)
    tl, v = compile_edit(p, edit_arg, sources)
    d = p.out / "renders"
    u = _previous(p, v)
    reuse = u if u and same_picture_and_sound(read_json(d / f"timeline.v{u:03d}.json"), tl) else None
    if reuse:
        print(f"captions/texts only since v{reuse:03d}: reusing its master and its sound checks")
    with p.timed("cut", v=v, **({"reused": f"v{reuse:03d}"} if reuse else {})):
        outs = render(p, tl, cat, sources, v, reuse_master=d / f"master.v{reuse:03d}.mp4" if reuse else None)
    if (d / f"placement.v{v:03d}.json").exists():
        log = outs.get("placement") or []
        stamp_block(p, "placement", f"Text placement v{v:03d} (faces and the subject kept clear; decided by jian)",
                    [f"- {x}" for x in log] or ["- every text in its default slot, captions in their lane"])
    rv = p.out / "review.md"
    if tl.get("justified") or rv.exists() and re.search(r"<!-- (jianji|vedit):abrupt -->", rv.read_text()):
        stamp_block(p, "abrupt", f"Speech cuts kept as they are v{v:03d} (\"abrupt\" in the edit, or yours in Resolve)",
                    [f"- {c}: {why}" for c, why in tl.get("justified", {}).items()] or ["- none"])
    if tl.get("reorders") or rv.exists() and re.search(r"<!-- (jianji|vedit):reorders -->", rv.read_text()):
        hms = lambda t: f"{int(t) // 3600}:{int(t) // 60 % 60:02d}:{int(t) % 60:02d}"
        stamp_block(p, "reorders", f"Told out of order v{v:03d} (the stream's own order differs)",
                    [f"- {r['sec']} (from {hms(r['src_t'])}) comes after {r['after']} (from {hms(r['prev_t'])})"
                     for r in tl.get("reorders", [])] or ["- none"])
    print(f"master  {outs['master']}\nreview  {outs['review']}\ncaptions {outs['srt']}")
    if "final" in outs:
        print(f"final   {outs['final']}")
    if qa:
        run_qa_cmd(p, v, tl, sources, outs, reuse=read_json(d / f"qa.v{reuse:03d}.json") if reuse else None)
    from .resolve import export  # every cut also opens in Resolve: jianji/resolve/IMPORT.md
    export(p, v)
    if p.short:  # a short out of a long video is a candidate: a light copy to judge it on a phone
        from .render import preview
        mb = float(p.cfg["render"].get("preview_mb", 8))
        print(f"preview {preview(Path(outs['final']), p.out / 'preview.mp4', tl['stats']['seconds'], mb)}")


def run_qa_cmd(p: Project, v: int, tl: dict | None = None, sources: dict | None = None, outs: dict | None = None,
               reuse: dict | None = None):
    from .qa import run_qa
    from .transcribe import Engine
    if sources is None:
        _, sources = load_sources(p, with_audio=True)
    d = p.out / "renders"
    tl = tl or read_json(d / f"timeline.v{v:03d}.json") or die(f"no timeline v{v:03d}")
    if outs is None:
        outs = {"master": d / f"master.v{v:03d}.mp4", "srt": d / f"captions.v{v:03d}.srt"}
        if (d / f"final.v{v:03d}.mp4").exists():
            outs.update(final=d / f"final.v{v:03d}.mp4", final_ass=d / f"final.v{v:03d}.ass")
    frames = None
    if outs.get("final_ass"):  # burned text: check it against the faces in the picture
        from .vision import detect
        frames = detect(p, Path(outs["master"]))
    words_on_screen = None
    if p.cfg.get("stream"):  # streams: read the cut for secrets and never_show
        from .vision import ocr
        words_on_screen = ocr(p, Path(outs["master"]), 1 / float(p.cfg["stream"].get("privacy_step", 2)), width=1920)
    with p.timed("qa", v=v):
        res = run_qa(tl, sources, outs, Engine(p.cfg["asr"], p.cfg["glossary"]), p.cfg["captions"], p.cfg["render"],
                     zone=p.cfg.get("safe_zone"), reuse=reuse, cut=p.cfg["cut"], vision=frames,
                     texts=p.cfg.get("texts"), shots=p.cfg.get("shots"), ocr=words_on_screen, hook=p.cfg.get("hook"),
                     length=p.cfg.get("shorts") if p.short else None,
                     never=p.cfg.get("never_show"), allow=(p.cfg.get("stream") or {}).get("allow_emails"))
    loud = (outs or {}).get("loudnorm_input") or (reuse or {}).get("loudnorm")
    if loud:  # the mix's measured loudness: `jian resolve` gives every clip the same gain the master got
        res["loudnorm"] = loud
    write_json(d / f"qa.v{v:03d}.json", res)
    stamp_review(p, f"{res['summary']} · v{v:03d}")
    print(res["summary"])
    for c in res["checks"]:
        print(f"  {c['status']:4}  {c['name']:14} {c['detail']}")
    for i in res["issues"][:40]:
        print(f"  {i['severity']:4}  {i['tc']}  {i['sec'] or '-'} {i['clip']} #{i['piece']}  {i['msg']}")
    if len(res["issues"]) > 40:
        print(f"  ... {len(res['issues']) - 40} more in qa.v{v:03d}.json")


def stamp_review(p: Project, line: str):
    """Keep the QA status line under review.md's title current; review.md itself is the editor's."""
    f = p.out / "review.md"
    if not f.exists():
        return
    lines = f.read_text().splitlines()
    i = next((i for i, x in enumerate(lines) if x.startswith("QA: ")), None)
    if i is None:
        i = next((i for i, x in enumerate(lines) if x.startswith("# ")), -1) + 1
        lines.insert(i, line)
    else:
        lines[i] = line
    f.write_text("\n".join(lines) + "\n")


def stamp_block(p: Project, name: str, title: str, lines: list[str]):
    """A section of review.md that jian owns and rewrites (between markers); the rest stays the editor's."""
    f = p.out / "review.md"
    if not f.exists():
        return
    text = f.read_text()
    block = f"<!-- jianji:{name} -->\n### {title}\n" + "\n".join(lines) + f"\n<!-- /jianji:{name} -->"
    m = re.search(rf"<!-- (jianji|vedit):{name} -->.*?<!-- /(jianji|vedit):{name} -->", text, re.S)  # vedit: before the rename
    if m:
        text = text[:m.start()] + block + text[m.end():]
    else:  # after the QA line (or the title)
        rows = text.splitlines()
        i = next((i for i, x in enumerate(rows) if x.startswith("QA: ")), None)
        i = i if i is not None else next((i for i, x in enumerate(rows) if x.startswith("# ")), -1)
        rows[i + 1:i + 1] = ["", block]
        text = "\n".join(rows) + "\n"
    f.write_text(text)


def parse_tc(s: str, F: Fraction) -> float:
    parts = [float(x) for x in s.split(":")]
    if len(parts) == 4:  # HH:MM:SS:FF
        return parts[0] * 3600 + parts[1] * 60 + parts[2] + parts[3] / float(F)
    sec = 0.0
    for x in parts:
        sec = sec * 60 + x
    return sec


def at(p: Project, tcs: list[str]):
    v = version_of(p.latest_edit())
    tl = read_json(p.out / "renders" / f"timeline.v{v:03d}.json") or die("no timeline yet")
    _, sources = load_sources(p)
    F = Fraction(tl["fps"])
    for arg in tcs:
        a, _, b = arg.partition("-")
        t0 = parse_tc(a, F)
        t1 = parse_tc(b, F) if b else t0
        print(f"{arg}  (timeline v{v:03d})")
        for s in tl["segments"]:
            s0, s1 = float(s["rec"] / F), float((s["rec"] + s["n"]) / F)
            if s1 < t0 or s0 > t1:
                continue
            W = sources[s["src"]]["words"]
            inside = [W[k] for k in s["kept"] if W[k]["s"] - s["in"] + s0 <= t1 and W[k]["e"] - s["in"] + s0 >= t0]
            ids = f"words {inside[0]['i']}-{inside[-1]['i']}: {' '.join(w['t'] for w in inside)[:120]}" if inside else ""
            print(f"  #{s['id']} {s['sec'] or '-'} {s['clip']}  out {s0:.2f}-{s1:.2f}  src {s['src']} "
                  f"{s['in']:.2f}-{s['in'] + float(s['n'] / F):.2f}  {ids}")


def status(p: Project):
    cat = read_json(p.out / "catalog.json")
    print(f"{p.dir}  profile={p.profile}  outline_checkpoint={p.cfg['edit']['outline_checkpoint']}")
    from .core import style_path
    print(f"work folder: {p.out}  style guide: {style_path(p.profile) or 'none for this profile'}")
    print(f"brief: {'yes' if (p.dir / 'brief.md').exists() else 'no (infer and state assumptions)'}")
    if not cat:
        print("next: jian prep")
        return
    missing = [sid for sid, e in cat["sources"].items() if e.get("audio_streams") and not _tr_path(p, sid, e).exists()]
    print(f"sources: {len(cat['sources'])}  transcripts missing/stale: {missing or 'none'}")
    edits = p.edits()
    print(f"edits: {[e.name for e in edits] or 'none'}  next: edits/edit.v{len(edits) + 1:03d}.json")
    for e in edits:
        v = version_of(e)
        qa = read_json(p.out / "renders" / f"qa.v{v:03d}.json")
        print(f"  v{v:03d}: rendered={'yes' if (p.out / 'renders' / f'master.v{v:03d}.mp4').exists() else 'no'}"
              f"  qa={qa['status'] if qa else '-'}")
    if p.short:  # what the editor of a short needs: the two-panel layouts you defined, and their regions
        print("layouts: " + ("; ".join(f"{k} (top {v.get('top')}, bottom {v.get('bottom')}"
                                       f"{', ' + str(v['note']) if v.get('note') else ''})"
                                       for k, v in (p.cfg.get("layouts") or {}).items()) or "none defined"))
    rule = p.cfg.get("shorts") or {}
    if rule.get("length") or rule.get("min_length") or rule.get("max_length"):  # yours, for shorts out of this video
        lo, hi = rule.get("length") or (0, 0)
        print("short length: " + "; ".join(x for x in (
            f"aim for {lo:g}-{hi:g} s" if hi else "", f"never under {rule['min_length']:g} s" if rule.get("min_length") else "",
            f"never over {rule['max_length']:g} s" if rule.get("max_length") else "") if x))
    from .queue import look
    shorts = [f"{x.name} {(look(x) or {'state': 'empty'})['state']}" for x in sorted((p.out / "shorts").glob("*")) if x.is_dir()]
    if shorts:  # where each one stands: pending (waiting for your yes or no), failed (QA), approved, rejected...
        print(f"shorts: {', '.join(shorts)}  (jian status {p.dir.name} --short <n>)")
    free = shutil.disk_usage(p.out).free / 1e9
    print(f"disk: {free:.0f} GB free (minimum {p.cfg['disk']['min_free_gb']})")


BRIEF_TEMPLATE = """---
# Every field is optional: delete what you don't need. Lines starting with # are ignored.
profile: {profile}            # {profiles}
# language: es, en            # languages spoken in the footage (two-letter codes)
# format: horizontal 16:9     # or: vertical 9:16, 1080x1920
# target_length: 8-10 min
# glossary: Kubernetes, Ana García, Café Central
#                             # names and terms to spell right in captions and on-screen text
# extends: my-series         # settings shared by a series: ~/.config/jianji/extensions/<name>.toml
# cut.max_pause: 1.0          # any profile setting as section.option (see `jian doctor` for where profiles live)
---

# Brief: {name}

## What it's about
<!-- one or two sentences: the story and the point -->

## Audience
<!-- who it's for and what they already know -->

## Structure
<!-- optional outline: hook, sections, close -->

## Must keep
<!-- moments, lines or numbers that have to be in the cut -->

## Notes
<!-- anything else: what to avoid, music, B-roll, where it will be published -->
"""


def new(arg: str, profile: str):
    from .core import HOME, global_config, profiles
    from .media import MEDIA_EXT
    if profile not in profiles():
        die(f"unknown profile '{profile}'; valid profiles: {', '.join(profiles())}")
    root = os.environ.get("JIANJI_VIDEOS_ROOT") or global_config().get("videos_root")
    d = Path(arg).expanduser() if "/" in arg or not root else Path(root).expanduser() / arg
    d.mkdir(parents=True, exist_ok=True)
    at_root = any(f.suffix.lower() in MEDIA_EXT for f in d.iterdir() if f.is_file())
    if not at_root:  # footage already dumped at the root stays there: an empty footage/ would hide it
        (d / "footage").mkdir(exist_ok=True)
    brief = d / "brief.md"
    if brief.exists():
        print(f"{brief} exists, left as is")
    else:
        mine = next((f for f in (HOME / "briefs" / f"{profile}.md", HOME / "briefs" / "default.md") if f.exists()), None)
        tpl = mine.read_text() if mine else BRIEF_TEMPLATE  # yours may use {name}, {profile}, {profiles}
        for k, v in (("name", d.name), ("profile", profile), ("profiles", " | ".join(profiles()))):
            tpl = tpl.replace("{" + k + "}", v)
        brief.write_text(tpl)
    print(f"{d}\n  footage: {d if at_root else d / 'footage'}\n  brief:   {brief}")


def ingest(file_arg: str | None, profile: str | None = None, name: str | None = None) -> Path:
    """A finished recording becomes a video folder: <videos_root>/<date>-<slug>/ with your brief template and, in
    footage/, a link to the recording. The recording is never moved, renamed or written to. With no file, the newest
    recording in `[ingest] watch` (config.toml in your layer). Again on the same recording: the same folder."""
    from datetime import datetime
    from .core import global_config
    from .media import MEDIA_EXT
    cfg = global_config().get("ingest", {})
    if file_arg:
        src = Path(file_arg).expanduser()
    else:
        watch = Path(cfg.get("watch") or die("no recording given and no [ingest] watch folder in your config.toml")).expanduser()
        found = [f for f in watch.glob("*") if f.is_file() and f.suffix.lower() in MEDIA_EXT and not f.name.startswith(".")]
        src = max(found, key=lambda f: f.stat().st_mtime, default=None) or die(f"no recordings in {watch}")
    src = src.resolve()
    if not src.is_file() or src.suffix.lower() not in MEDIA_EXT:
        die(f"not a recording: {src}")
    m = re.match(r"(\d{4}-\d{2}-\d{2})(.*)", src.stem)  # OBS names a file by when it started; else, when it was written
    date = m.group(1) if m else datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", (m.group(2) if m else src.stem).lower()).strip("-")
    name = name or str(cfg.get("name", "{date}-{slug}")).format(date=date, slug=slug).strip("-")
    root = os.environ.get("JIANJI_VIDEOS_ROOT") or global_config().get("videos_root") or \
        die("no videos_root set (JIANJI_VIDEOS_ROOT or config.toml in your layer)")
    d = Path(root).expanduser().resolve() / name
    link = d / "footage" / src.name
    if d.exists() and not (link.is_symlink() and link.resolve() == src):
        die(f"{d} already exists with other footage: pass --name")
    new(str(d), profile or cfg.get("profile", "stream"))
    if not link.is_symlink():
        link.symlink_to(src)
    from .core import hook
    hook("ingest", video=d.name, dir=d, source=src)
    print(d)  # last line: the video folder, for scripts
    return d


def _reel(p: Project, video: Path, parts: list[tuple[float, float, str]], out: Path, size: list[int]):
    """Excerpts of `video` ((start, duration, label) each), labelled and joined into one review reel."""
    from .core import FFMPEG, run
    w, h = size
    tmp = p.path("cache", "reel", "x").parent
    files = []
    for k, (a, dur, label) in enumerate(parts, 1):
        part = tmp / f"part{k:03d}.mp4"
        label = label.replace("\\", "").replace("'", "’").replace(":", "\\:").replace("%", "\\%")
        run([FFMPEG, "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", video, "-vf",
             f"{'scale=-2:960' if h > w else 'scale=-2:540'},drawtext=font=Menlo:text='{label}':"
             f"fontsize={18 if h > w else 22}:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:x=(w-tw)/2:y=h-th-24",
             "-af", f"afade=t=in:d=0.05,afade=t=out:st={dur - 0.05:.3f}:d=0.05",
             "-c:v", "h264_videotoolbox", "-b:v", "3M", "-c:a", "aac_at", "-b:a", "128k", "-ar", 48000, part])
        files.append(part)
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{x}'\n" for x in files))
    run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", "-movflags", "+faststart", out])
    for x in files:
        x.unlink()


def cut_reel(p: Project, v: int | None):
    """Every cut with 1.5 s of context each side, labelled with its number, class and the gap measured on the
    render: audit every join in a couple of minutes."""
    from .media import decode
    from .qa import breathing
    v = v or version_of(p.latest_edit())
    d = p.out / "renders"
    master = d / f"master.v{v:03d}.mp4"
    tl = read_json(d / f"timeline.v{v:03d}.json") or die(f"no timeline v{v:03d}: run `jian cut` first")
    if not master.exists():
        die(f"no {master.name}: run `jian cut` first")
    _, sources = load_sources(p)
    js = breathing(tl, sources, decode(master)[:, 0], p.cfg["cut"])
    if not js:
        die("no cuts between speech pieces in this version")
    parts = []
    for k, j in enumerate(js, 1):
        a = max(0.0, j["end"] - 1.5)
        parts.append((a, j["start"] + 1.5 - a, f"{k}/{len(js)}  {j['class']}  {j['quiet']:.2f}s (min {j['min']:.2f})"
                      + ("  TOO TIGHT" if j["tight"] else "")))
    out = d / f"cuts.v{v:03d}.mp4"
    _reel(p, master, parts, out, tl["size"])
    tight = [j for j in js if j["tight"]]
    print(f"{out}\n{len(js)} cuts, {len(tight)} too tight")
    for k, j in enumerate(js, 1):
        print(f"  {k:3d}  {j['class']:8} gap {j['quiet']:.2f}s{'  TOO TIGHT' if j['tight'] else ''}  "
              f"{j['last']!r} | {j['first']!r}")


def text_reel(p: Project, v: int | None):
    """Every story line and callout as burned, with 1 s before and after, labelled with its time on screen and
    reading speed: check each one can be read in a minute."""
    from .compile import reading_time
    from .qa import text_boxes
    v = v or version_of(p.latest_edit())
    d = p.out / "renders"
    tl = read_json(d / f"timeline.v{v:03d}.json") or die(f"no timeline v{v:03d}: run `jian cut` first")
    video, ass = d / f"final.v{v:03d}.mp4", d / f"final.v{v:03d}.ass"
    if not video.exists():
        video, ass = d / f"review.v{v:03d}.mp4", d / f"review.v{v:03d}.ass"
    if not video.exists():
        die(f"no final or review render v{v:03d}: run `jian cut` first")
    size = tl["size"] if ass.name.startswith("final") else None
    tx = p.cfg.get("texts") or {}
    if size:
        shown = sorted((a, b, t) for st, a, b, _, t in text_boxes(ass, size) if st in ("Txt", "Hi"))
    else:
        F = Fraction(tl["fps"])
        shown = [(float(t["rec"] / F), float((t["rec"] + t["n"]) / F), t["text"]) for t in tl.get("texts", [])]
    if not shown:
        die("no on-screen texts in this version")
    end = float(tl["frames"] / Fraction(tl["fps"]))
    parts, rows = [], []
    for k, (a, b, t) in enumerate(shown, 1):
        need, chars = reading_time(t, tx), len(" ".join(t.split()))
        flag = "  TOO SHORT" if b - a < need - 0.05 else ""
        rows.append(f"  {k:3d}  {b - a:.1f}s  {chars / (b - a):4.1f} chars/s  (needs {need:.1f}s){flag}  {t!r}")
        s = max(0.0, a - 1)
        parts.append((s, min(end, b + 1) - s, f"{k}/{len(shown)}  {b - a:.1f}s  {chars / (b - a):.1f} c/s "
                      f"(min {need:.1f}s){flag}"))
    out = d / f"texts.v{v:03d}.mp4"
    _reel(p, video, parts, out, tl["size"])
    print(f"{out}\n{len(shown)} texts, {sum('TOO SHORT' in r for r in rows)} too short to read")
    print("\n".join(rows))


def clean(p: Project, dry_run: bool):
    """Delete what jian regenerates on demand: extracted audio, render pieces, reel scratch, and every render
    version but the latest two (their small .json records stay: timeline, QA, placement). Footage, brief, edits,
    transcripts, vision notes, review.md and publish.md are never touched."""
    d = p.out / "renders"
    vs = sorted({version_of(f) for f in d.glob("*.v[0-9][0-9][0-9].*")})
    old = set(vs[:-2])
    files = [f for sub in ("audio", "seg", "reel") for f in (p.out / "cache" / sub).glob("*") if f.is_file()]
    files += [f for f in d.glob("*.v[0-9][0-9][0-9].*") if version_of(f) in old and f.suffix != ".json"]
    inodes: dict[int, list] = {}
    for f in files:
        st = f.stat()
        inodes.setdefault(st.st_ino, [st.st_size, st.st_nlink, 0])[2] += 1
    freed = sum(size for size, links, n in inodes.values() if n >= links)  # a master another version links stays
    print(f"{p.out}: {'would free' if dry_run else 'freed'} {freed / 1e9:.2f} GB in {len(files)} files "
          f"(kept renders: {', '.join(f'v{x:03d}' for x in vs[-2:]) or 'none'})")
    for f in files:
        if dry_run:
            print(f"  {f.relative_to(p.out)}")
        else:
            f.unlink()


def report(root_arg: str | None):
    """Scoreboard across every video under videos_root (one or two levels down): your review minutes are the main
    metric."""
    from .core import LEGACY, WORK, global_config
    root = Path(root_arg or os.environ.get("JIANJI_VIDEOS_ROOT") or global_config().get("videos_root") or
                die("no videos_root set (JIANJI_VIDEOS_ROOT or ~/.config/jianji/config.toml)")).expanduser()
    mmss = lambda s: f"{int(s) // 60}:{int(s) % 60:02d}" if s else "-"
    prep_stages, cut_stages = ("probe", "transcribe", "vlm"), ("render_video", "render_audio", "render_review", "render_final")
    rows = []
    found = {c.parent for n in (WORK, LEGACY) for pat in (f"*/{n}/catalog.json", f"*/*/{n}/catalog.json")
             for c in root.glob(pat)}
    for out in sorted(found, key=lambda o: o.parent):
        d = out.parent
        m = read_json(out / "metrics.json", {"stages": []})
        edits = sorted((out / "edits").glob("edit.v[0-9][0-9][0-9].json"))
        qas = sorted((out / "renders").glob("qa.v[0-9][0-9][0-9].json"))
        qa = read_json(qas[-1]) if qas else None
        stage = lambda names: sum(s["sec"] for s in m["stages"] if s["stage"] in names)
        from .core import read_brief
        profile = read_brief(d / "brief.md")[0].get("profile", "explainer")
        minutes = m.get("review_minutes") or []
        rows.append([str(d.relative_to(root)), str(profile), f"v{version_of(edits[-1]):03d}" if edits else "-",
                     (qa.get("summary") or f"QA: {qa['status']}").removeprefix("QA: ") if qa else "-",
                     f"{sum(minutes):g}" if minutes else "not logged", str(len(m.get("feedback") or [])),
                     mmss(stage(prep_stages)), mmss(stage(cut_stages)), mmss(stage(("qa",))),
                     mmss(sum(s["sec"] for s in m["stages"] if s["stage"] != "cut"))])  # "cut" wraps the render stages
    head = ["video", "profile", "latest", "QA", "review min", "feedback rounds", "prep", "render", "qa", "machine total"]
    print(f"jian report: {root}\n")
    print("| " + " | ".join(head) + " |\n|" + "---|" * len(head))
    for r in rows:
        print("| " + " | ".join(r) + " |")
    logged = [r for r in rows if r[4] != "not logged"]
    print(f"\n{len(rows)} videos, review minutes logged for {len(logged)}. Log with `jian log <name> review_minutes <n>`.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="jian", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("report").add_argument("root", nargs="?")
    sub.add_parser("doctor")
    nw = sub.add_parser("new")
    nw.add_argument("video")
    nw.add_argument("--profile", default="explainer")
    ig = sub.add_parser("ingest")
    ig.add_argument("file", nargs="?")
    ig.add_argument("--profile")
    ig.add_argument("--name")
    for name in ("prep", "status", "map"):
        sub.add_parser(name).add_argument("video")
    short = lambda x: x.add_argument("--short", metavar="N", help="work on short N of this video")
    short(sub.choices["status"])
    w = sub.add_parser("words")
    for a in ("video", "src"):
        w.add_argument(a)
    w.add_argument("a", type=int)
    w.add_argument("b", type=int)
    sh = sub.add_parser("sheet")
    for a in ("video", "src"):
        sh.add_argument(a)
    sh.add_argument("a", type=float, nargs="?", default=0.0)
    sh.add_argument("b", type=float, nargs="?")
    sh.add_argument("--frames", type=int, default=16)
    for name in ("compile", "cut"):
        c = sub.add_parser(name)
        c.add_argument("video")
        c.add_argument("--edit")
        if name == "cut":
            c.add_argument("--no-qa", action="store_true")
        short(c)
    for name in ("cuts", "texts"):
        cr = sub.add_parser(name)
        cr.add_argument("video")
        cr.add_argument("--v", type=int)
        short(cr)
    rs = sub.add_parser("resolve")
    rs.add_argument("video")
    rs.add_argument("--v", type=int)
    short(rs)
    pu = sub.add_parser("pull")
    pu.add_argument("video")
    pu.add_argument("file", nargs="?")
    df = sub.add_parser("diff")
    df.add_argument("video")
    df.add_argument("--v", type=int)
    cl = sub.add_parser("clean")
    cl.add_argument("video")
    cl.add_argument("--dry-run", action="store_true")
    q = sub.add_parser("qa")
    q.add_argument("video")
    q.add_argument("--v", type=int)
    short(q)
    t = sub.add_parser("at")
    t.add_argument("video")
    t.add_argument("tc", nargs="+")
    short(t)
    lg = sub.add_parser("log")
    lg.add_argument("video")
    lg.add_argument("key")
    lg.add_argument("value")
    qu = sub.add_parser("queue")
    qu.add_argument("--json", action="store_true")
    qu.add_argument("--all", action="store_true", help="also approved, rejected, failed QA and unrendered")
    qu.add_argument("--announce", action="store_true", help="fire on_queue_ready when something is waiting")
    qu.add_argument("--protected", action="store_true", help="list the recordings a waiting short still needs")
    for name in ("approve", "reject"):
        j = sub.add_parser(name)
        j.add_argument("id", help="<video>:<n>, as `jian queue` prints it")
        if name == "approve":
            j.add_argument("--force", action="store_true", help="even if its QA failed or didn't run")
    hk = sub.add_parser("hook")
    hk.add_argument("event")
    hk.add_argument("facts", nargs="*", metavar="KEY=VALUE")
    args = ap.parse_args(argv)
    try:
        return _run(args)
    except BaseException as e:  # a failed run tells your on_error hook (a night run nobody watches), then fails as before
        code = e.code if isinstance(e, SystemExit) else repr(e)
        if code not in (None, 0) and args.cmd not in ("doctor", "hook") and not isinstance(e, KeyboardInterrupt):
            from .core import hook
            try:
                hook("error", command=args.cmd, video=getattr(args, "video", getattr(args, "id", None)), error=code)
            except Exception:  # a broken config.toml: the original failure is the one to show
                pass
        raise


def _run(args):
    if args.cmd == "hook":
        from .core import hook
        if any("=" not in x for x in args.facts):
            die("facts are KEY=VALUE")
        return hook(args.event, **{k.lower(): v for k, v in (x.split("=", 1) for x in args.facts)})
    if args.cmd in ("queue", "approve", "reject"):
        from . import queue as q
        if args.cmd == "queue":
            return print("\n".join(q.protected())) if args.protected else q.queue(args.json, args.all, args.announce)
        q.approve(args.id, args.force) if args.cmd == "approve" else q.reject(args.id)
        return
    if args.cmd == "new":
        return new(args.video, args.profile)
    if args.cmd == "ingest":
        ingest(args.file, args.profile, args.name)
        return
    if args.cmd == "report":
        return report(args.root)
    if args.cmd == "doctor":
        from .doctor import doctor
        return doctor()
    p = Project(args.video, getattr(args, "short", None))
    if args.cmd == "prep":
        prep(p)
    elif args.cmd == "map":
        from .stream import stream_map
        stream_map(p)
    elif args.cmd == "words":
        from .pack import words_view
        cat = read_json(p.out / "catalog.json") or die("run `jian prep` first")
        e = cat["sources"].get(args.src) or die(f"unknown source {args.src}")
        print(words_view(read_json(_tr_path(p, args.src, e)), args.a, args.b))
    elif args.cmd == "sheet":
        from .broll import sheet
        cat = read_json(p.out / "catalog.json") or die("run `jian prep` first")
        e = cat["sources"].get(args.src) or die(f"unknown source {args.src}")
        if e["kind"] != "video":
            die(f"{args.src} has no picture")
        print(sheet(p, args.src, e, args.a, args.b, frames=args.frames)[0])
    elif args.cmd == "compile":
        compile_edit(p, args.edit, load_sources(p)[1])
    elif args.cmd == "cut":
        cut(p, args.edit, qa=not args.no_qa)
    elif args.cmd == "cuts":
        cut_reel(p, args.v)
    elif args.cmd == "texts":
        text_reel(p, args.v)
    elif args.cmd == "clean":
        clean(p, args.dry_run)
    elif args.cmd == "resolve":
        from .resolve import export
        export(p, args.v)
    elif args.cmd == "pull":
        from .resolve import pull
        pull(p, args.file)
    elif args.cmd == "diff":
        from .resolve import show_diff
        show_diff(p, args.v)
    elif args.cmd == "qa":
        run_qa_cmd(p, args.v or version_of(p.latest_edit()))
    elif args.cmd == "at":
        at(p, args.tc)
    elif args.cmd == "status":
        status(p)
    elif args.cmd == "log":
        path = p.out / "metrics.json"
        m = read_json(path, {"stages": [], "rounds": 0, "review_minutes": []})
        val = float(args.value) if re.fullmatch(r"-?[\d.]+", args.value) else args.value
        if args.key in ("review_minutes", "feedback") or isinstance(m.get(args.key), list):  # these accumulate
            m[args.key] = [*(m.get(args.key) or []), val]
        else:
            m[args.key] = val
        write_json(path, m)


if __name__ == "__main__":
    sys.exit(main())
