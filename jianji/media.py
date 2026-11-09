"""Probe footage into a catalog with stable source ids, and read audio as numpy."""
from __future__ import annotations

import json
import re
import string
from fractions import Fraction
from pathlib import Path

import numpy as np

from .core import FFMPEG, FFPROBE, Project, die, fingerprint, read_json, run, write_json

MEDIA_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".mts", ".avi", ".webm",
             ".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff"}
SR = 16000  # analysis rate


def ffprobe(path: Path) -> dict:
    p = run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path])
    return json.loads(p.stdout)


def probe(path: Path) -> dict | None:
    info = ffprobe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s["codec_type"] == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    a = [s for s in streams if s["codec_type"] == "audio"]
    dur = float(info.get("format", {}).get("duration") or 0)
    if dur < 0.5 or (not v and not a):
        return None
    e = {"dur": round(dur, 3), "kind": "video" if v else "audio", "audio_streams": len(a)}
    if v:
        rot = 0
        for sd in v.get("side_data_list", []):
            rot = int(sd.get("rotation", rot))
        w, h = v["width"], v["height"]
        if abs(rot) in (90, 270):
            w, h = h, w
        r, avg = Fraction(v["r_frame_rate"]), Fraction(v.get("avg_frame_rate") or "0/1")
        e.update(w=w, h=h, fps=str(r), rot=rot, codec=v["codec_name"], pix=v.get("pix_fmt"),
                 transfer=v.get("color_transfer"),
                 # phones and OBS report a nominal r_frame_rate but a drifting average
                 vfr=bool(avg) and abs(float(avg) - float(r)) / float(r) > 0.01)
        tc = v.get("tags", {}).get("timecode") or info["format"].get("tags", {}).get("timecode")
        if tc:
            e["tc"] = tc
    if a:
        # OBS can record the mic on its own track (STREAMING.md): a track titled mic/voz/voice is the dialogue
        mic = next((k for k, s in enumerate(a) if re.search(r"mic|voz|voice|micro", (s.get("tags") or {}).get("title", ""), re.I)), 0)
        e["channels"] = a[mic].get("channels")
        if mic:
            e["mic"] = mic
    return e


def _next_id(used: set[str]) -> str:
    n = 0
    while True:
        s, k = "", n
        while True:
            s = string.ascii_uppercase[k % 26] + s
            k = k // 26 - 1
            if k < 0:
                break
        if s not in used:
            return s
        n += 1


def build_catalog(p: Project) -> dict:
    """Ids are persistent: adding a file later never renames existing sources (edits reference ids)."""
    path = p.path("catalog.json")
    old = read_json(path, {"sources": {}, "skipped": []})
    by_file = {e["file"]: (sid, e) for sid, e in old["sources"].items()}
    sources, skipped = {}, []
    files = (x for x in p.footage.rglob("*") if x.is_file() and not x.name.startswith("."))
    for f in sorted(x for x in files if p.out not in x.parents and x.name != "brief.md"):  # footage may be the root
        rel = str(f.relative_to(p.footage))
        if f.suffix.lower() not in MEDIA_EXT:
            skipped.append(rel)
            continue
        fp = fingerprint(f)
        sid, prev = by_file.get(rel, (None, None))
        if prev and prev["fp"] == fp:
            entry = prev
        else:
            entry = probe(f)
            if entry is None:
                skipped.append(rel)
                continue
            entry.update(file=rel, fp=fp)
        sid = sid or _next_id(set(sources) | {s for s, _ in by_file.values()})
        sources[sid] = entry
    if not sources:
        die(f"no media found in {p.footage}")
    cat = {"sources": sources, "skipped": skipped}
    write_json(path, cat)
    return cat


def src_path(p: Project, entry: dict) -> Path:
    return p.footage / entry["file"]


def wav16k(p: Project, sid: str, entry: dict) -> Path | None:
    """Mono 16 kHz analysis audio, cached by source fingerprint."""
    if not entry.get("audio_streams"):
        return None
    out = p.path("cache", "audio", f"{sid}.{entry['fp']}.wav")
    if not out.exists():
        p.ensure_space(entry["dur"] * SR * 2, f"audio for {sid}")
        tmp = out.with_suffix(".tmp.wav")
        run([FFMPEG, "-v", "error", "-y", "-i", src_path(p, entry), "-map", f"0:a:{entry.get('mic', 0)}", "-ac", "1", "-ar", SR,
             "-c:a", "pcm_s16le", tmp])
        tmp.replace(out)
    return out


def wav48k(p: Project, sid: str, entry: dict) -> Path | None:
    """Stereo 48 kHz PCM for rendering, cached. Seeking compressed audio (-ss on AAC) lands up to one
    codec frame (21 ms) off; slicing PCM is sample-exact."""
    if not entry.get("audio_streams"):
        return None
    out = p.path("cache", "audio", f"{sid}.{entry['fp']}.48k.wav")
    if not out.exists():
        p.ensure_space(entry["dur"] * 48000 * 4, f"render audio for {sid}")
        tmp = out.with_suffix(".tmp.wav")
        run([FFMPEG, "-v", "error", "-y", "-i", src_path(p, entry), "-map", f"0:a:{entry.get('mic', 0)}", "-ac", 2, "-ar", 48000,
             "-c:a", "pcm_s16le", tmp])
        tmp.replace(out)
    return out


def motion(p: Project, sid: str, entry: dict, fps: int = 10) -> np.ndarray | None:
    """How much the picture changes, `fps` times a second: mean absolute difference between consecutive frames of a
    64x64 grey thumbnail. Cached by source fingerprint; finds where an action or a reaction settles."""
    if entry.get("kind") != "video":
        return None
    out = p.path("cache", "motion", f"{sid}.{entry['fp']}.npy")
    if not out.exists():
        raw = run([FFMPEG, "-v", "error", "-i", src_path(p, entry), "-an", "-vf", f"fps={fps},scale=64:64,format=gray",
                   "-f", "rawvideo", "-"]).stdout
        fr = np.frombuffer(raw, np.uint8)[:len(raw) // 4096 * 4096].reshape(-1, 64, 64).astype(np.int16)
        np.save(out, np.abs(np.diff(fr, axis=0)).mean(axis=(1, 2)).astype(np.float32))
    return np.load(out)


def _pcm(path: Path, ch: int) -> np.ndarray:
    with path.open("rb") as f:
        head = f.read(4096)
    off = head.index(b"data") + 8
    return np.memmap(path, np.int16, "r", offset=off).reshape(-1, ch)


def load_wav(path: Path) -> np.ndarray:
    return _pcm(path, 1)[:, 0].astype(np.float32) / 32768


def read_pcm(path: Path, start: float, dur: float, rate: int = 48000, ch: int = 2) -> np.ndarray:
    """Exact sample range from a cached PCM wav; zero-padded outside the file."""
    data = _pcm(path, ch)
    a, n = round(start * rate), round(dur * rate)
    out = np.zeros((n, ch), np.float32)
    lo, hi = max(0, a), min(len(data), a + n)
    if hi > lo:
        out[lo - a:hi - a] = data[lo:hi].astype(np.float32) / 32768
    return out


def decode(path: Path, start: float = 0.0, dur: float | None = None, rate: int = SR, ch: int = 1) -> np.ndarray:
    """Decode an audio range as float32 (frames, ch); zero-pads before t=0 and past the end so length is exact."""
    pad = max(0.0, -start)
    cmd = [FFMPEG, "-v", "error", "-ss", f"{max(0.0, start):.6f}"]
    if dur is not None:
        cmd += ["-t", f"{dur - pad:.6f}"]
    cmd += ["-i", path, "-map", "0:a:0", "-ac", ch, "-ar", rate, "-f", "f32le", "-"]
    a = np.frombuffer(run(cmd).stdout, np.float32).reshape(-1, ch).copy()
    if pad:
        a = np.concatenate([np.zeros((round(pad * rate), ch), np.float32), a])
    if dur is not None:
        n = round(dur * rate)
        a = a[:n] if len(a) >= n else np.concatenate([a, np.zeros((n - len(a), ch), np.float32)])
    return a


def envelope(audio: np.ndarray, hop: float = 0.01) -> np.ndarray:
    """RMS in dBFS per 10 ms frame."""
    n = int(SR * hop)
    frames = audio[: len(audio) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
