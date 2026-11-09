"""B-roll: candidates, contact sheets, and a local vision model's first-pass description.

Claude reads broll.md (text) and opens a sheet only for the shortlist; the vision model never decides anything.
"""
from __future__ import annotations

import math
from pathlib import Path

from .core import FFMPEG, Project, key, read_json, run, write_json
from .media import src_path

VLM_VERSION = 1
PROMPT = """These are {n} frames from one video clip, in time order. Each frame shows its source time (HH:MM:SS) in the top-left corner.
Reply in exactly this format, terse:
SHOWS: <subject and setting, max 15 words>
ACTION: <what changes over time, max 20 words>
BEST: <steady, well-framed time range(s), like 00:00:02-00:00:06>
ISSUES: <shaky, blurry, dark, fumbling, or none>
USE: <what narration it could illustrate, max 12 words>"""


def candidates(cat: dict, trs: dict) -> list[str]:
    """Video sources with little or no speech of their own."""
    out = []
    for sid, e in cat["sources"].items():
        if e["kind"] != "video":
            continue
        words = len((trs.get(sid) or {}).get("words", []))
        if words < 10 or words / max(e["dur"] / 60, 0.1) < 40:
            out.append(sid)
    return out


def sheet(p: Project, sid: str, e: dict, a: float = 0.0, b: float | None = None, frames: int = 16, cols: int = 4) -> tuple[Path, int]:
    """Evenly spaced frames in a grid, each stamped with its source time. Cached per range and file content."""
    b = min(b if b is not None else e["dur"], e["dur"])
    step = max((b - a) / frames, 0.2)
    n = max(1, min(frames, math.floor((b - a) / step)))
    out = p.path("sheets", f"{sid}_{a:.1f}-{b:.1f}_{e['fp'][:6]}.jpg")
    if not out.exists():
        tmp = out.with_suffix(".tmp.jpg")
        vf = (f"fps=1/{step:.4f},scale='if(gt(iw,ih),320,-2)':'if(gt(iw,ih),-2,320)',"
              f"drawtext=font=Menlo:text='%{{pts\\:hms\\:{a:.3f}}}':x=6:y=6:fontsize=16:fontcolor=white:"
              f"box=1:boxcolor=black@0.6:boxborderw=4,tile={cols}x{math.ceil(n / cols)}:padding=4")
        run([FFMPEG, "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src_path(p, e),
             "-vf", vf, "-frames:v", 1, "-update", 1, "-q:v", 3, tmp])
        tmp.replace(out)
    return out, n


class Vision:
    def __init__(self, model_id: str):
        self.model_id = model_id
        self._m = None

    def describe(self, image: Path, n: int, prompt: str | None = None) -> str:
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config
        if self._m is None:
            model, proc = load(self.model_id)
            self._m = (model, proc, load_config(self.model_id))
        model, proc, cfg = self._m
        text = apply_chat_template(proc, cfg, prompt or PROMPT.format(n=n), num_images=1)
        out = generate(model, proc, text, [str(image)], max_tokens=220, temperature=0.0, verbose=False)
        return getattr(out, "text", out).strip()


def describe(p: Project, sid: str, e: dict, vis: Vision) -> dict:
    cfg = p.cfg.get("broll", {})
    path = p.path("cache", "vlm", f"{sid}.{key(e['fp'], vis.model_id, VLM_VERSION, PROMPT, cfg.get('sheet_frames', 16))}.json")
    if path.exists():
        return read_json(path)
    img, n = sheet(p, sid, e, frames=int(cfg.get("sheet_frames", 16)))
    with p.timed("vlm", src=sid):
        res = {"sheet": str(img.relative_to(p.dir)), "text": vis.describe(img, n)}
    write_json(path, res)
    return res


def broll_md(name: str, cat: dict, descs: dict) -> str:
    out = [f"# B-roll: {name}", "",
           "Video sources with little or no speech. Descriptions are a local vision model's first pass: open the sheet "
           "before relying on one. Vertical sources are centre-cropped to fill a horizontal frame.", ""]
    if not descs:
        out.append("No B-roll candidates.")
    for sid, d in descs.items():
        e = cat["sources"][sid]
        fmt = f"{e['w']}x{e['h']}" + (" vertical" if e["h"] > e["w"] else "")
        audio = "has audio" if e.get("audio_streams") else "silent"
        out += [f"## {sid}  {e['file']}  {e['dur']:.1f}s  {fmt}  {audio}", f"sheet: {d['sheet']}", d["text"], ""]
    return "\n".join(out)
