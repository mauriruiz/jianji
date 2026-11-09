"""Faces and the salient subject (Apple Vision) over time, for placing on-screen text and checking it.

vision.swift is compiled once into ~/.cache/jianji and run on frames sampled from the master (the picture without
any text), 4 per second. Results are cached per master, so a caption-only revision reuses them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from .core import FFMPEG, Project, fingerprint, read_json, run, write_json

FPS = 4
# Vision's face box runs from the eyebrows to the chin; the head is bigger. Grow it 35% upwards (forehead and hair:
# a badge once landed on a hairline), 15% to each side, 10% down.
PAD = (0.15, 0.35, 0.15, 0.10)  # left, top, right, bottom, as fractions of the box


def binary() -> Path:
    src = Path(__file__).with_name("vision.swift")
    out = Path("~/.cache/jianji").expanduser() / f"jian-vision-{hashlib.sha1(src.read_bytes()).hexdigest()[:10]}"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        run(["xcrun", "swiftc", "-O", src, "-o", tmp])
        tmp.replace(out)
    return out


def detect(p: Project, video: Path) -> list[dict]:
    """[{"t": seconds, "faces": [[x0, y0, x1, y1, conf]], "salient": [...]}] with boxes normalised to the frame."""
    cache = p.path("cache", "vision", f"{fingerprint(video)}.json")
    if cache.exists():
        return read_json(cache)
    tmp = Path(tempfile.mkdtemp(prefix="jian-vision-"))
    try:
        with p.timed("vision", file=video.name):
            run([FFMPEG, "-v", "error", "-i", video, "-vf", f"fps={FPS},scale='if(gt(iw,ih),640,-2)':'if(gt(iw,ih),-2,640)'",
                 "-q:v", 4, tmp / "f%06d.jpg"])
            lines = run([binary(), tmp]).stdout.decode().splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    frames = [{"t": round((k + 0.5) / FPS, 3), **json.loads(x)} for k, x in enumerate(lines) if x.strip()]
    write_json(cache, frames)
    return frames


def ocr(p: Project, video: Path, fps: float, width: int = 1600, fast: bool = False, start: float = 0.0,
        dur: float | None = None) -> list[dict]:
    """[{"t": seconds, "text": [[line, x0, y0, x1, y1, conf]]}]: the words on screen, `fps` frames a second (a stream
    map samples one every 10 s; the privacy check reads the master every 2 s). Cached per file and sampling."""
    key = f"{fingerprint(video)}.{fps:g}.{width}.{'fast' if fast else 'acc'}.{start:g}.{dur or 0:g}"
    cache = p.path("cache", "ocr", f"{hashlib.sha1(key.encode()).hexdigest()[:16]}.json")
    if cache.exists():
        return read_json(cache)
    tmp = Path(tempfile.mkdtemp(prefix="jian-ocr-"))
    try:
        with p.timed("ocr", file=video.name):
            sparse = ["-skip_frame", "nokey"] if fps < 0.5 else []  # one frame every few seconds: keyframes are enough
            run([FFMPEG, "-v", "error", *sparse, "-ss", f"{start:.3f}", *(["-t", f"{dur:.3f}"] if dur else []), "-i", video,
                 "-vf", f"fps={fps},scale={width}:-2", "-q:v", 3, tmp / "f%06d.jpg"])
            lines = run([binary(), tmp, "ocr-fast" if fast else "ocr"]).stdout.decode().splitlines()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    frames = [{"t": round(start + (k + 0.5) / fps, 3), **json.loads(x)} for k, x in enumerate(lines) if x.strip()]
    write_json(cache, frames)
    return frames


def _grow(b, pad=PAD):
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - w * pad[0], b[1] - h * pad[1], b[2] + w * pad[2], b[3] + h * pad[3])


def _overlap(a, b) -> float:
    """Area of a ∩ b."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
