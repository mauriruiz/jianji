"""Streams: a long livestream isn't one story but several threads interleaved (a feature started, chat, a diagram,
the feature again an hour later). `jian map <name>` turns the whole stream into a compact per-minute view for the
editor: speech density, what's on screen (OCR every map_step s: editor, terminal, browser, diagram; file and window
names), where the activity is (for zooming on code), cues said aloud (STREAMING.md), the vision model's note at each
change of activity, and privacy hits. The editor reads it and writes jianji/stream_map.md: spans, threads, chapters.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from .core import FFMPEG, Project, die, read_json, run, write_json

APPS = (  # first match wins, on the words OCR reads in one frame
    ("terminal", r"cargo (build|run|test|check)|\berror\[E\d+|warning: |Compiling |Finished |\$ \w|~/|zsh|bash"),
    ("diagram", r"excalidraw|tldraw|draw\.io|miro|figma|whimsical"),
    ("editor", r"\bfn \w|\blet (mut )?\w|\bpub (fn|struct|enum)|\bimpl\b|\bstruct \w|\buse \w+::|=>|\.rs\b|\bdef \w|\bfunction\b"),
    ("browser", r"https?://|www\.|\.com/|github\.com|localhost:\d|wikipedia"),
)
FILES = re.compile(r"\b[\w./-]+\.(?:rs|toml|md|ts|tsx|js|py|go|json|ya?ml|lua|gd|cs|cpp|h)\b")
SECRETS = (
    # a lowercase domain and nothing code-like around it: OCR of code ("cfg.validate_x@Q.ie.ehcl:") isn't an address
    ("email", r"(?<![\w.@])[\w.+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,12}(?![\w(@])"),
    ("api key", r"\b(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_\w{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[\w-]{10,}|"
                r"AIza[\w-]{30,})"),
    ("token", r"\beyJ[\w-]{10,}\.[\w-]{10,}"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY"),
    (".env", r"(^|\s)\.env(\.\w+)?\b|\b[A-Z][A-Z0-9_]*(KEY|SECRET|TOKEN|PASSWORD)\s*=\s*\S+"),
)
NOTIFY = re.compile(r"\b(Messages|Mensajes|WhatsApp|Telegram|Mail|Correo|Slack|Discord|iMessage|Signal)\b")
CUES = (("topic", r"nuevo tema|new topic|cambio de tema"), ("mark", r"marca eso|mark (that|this)"),
        ("goal", r"objetivo|el plan (de hoy|es)|today'?s goal|goal (for|of) today"),
        ("chat", r"\bchat\b|pregunta|me preguntan|(he|she|they) asks?|question from"),
        ("works", r"funciona|it works|ya está|perfecto|lo tenemos"), ("recap", r"resumen|recap|en resumen"))
PROMPT = ("This is one frame of a programming livestream. In one short line: which app is in front (code editor, "
          "terminal, browser, diagram, the running program, chat) and what is being worked on (file, feature, "
          "diagram topic). No preamble.")


def classify(lines: list[str]) -> str:
    """The app with the most matches (a status bar or an overlay always on screen mustn't decide alone); the editor
    needs three: one keyword shows up anywhere."""
    text = "\n".join(lines)
    score = {name: len(re.findall(rx, text, re.I)) for name, rx in APPS}
    score["editor"] = score["editor"] if score["editor"] >= 3 else 0
    best = max(score, key=score.get)
    return best if score[best] else "other"


def public_email(addr: str, allow: list[str]) -> bool:
    """GitHub's noreply addresses (git log on screen) and the ones you list in `[stream] allow_emails`."""
    a = addr.lower()
    return a == "noreply@github.com" or a.endswith("@users.noreply.github.com") or a in {x.lower() for x in allow}


def private(lines: list[list], never: list[str], allow: list[str] = ()) -> list[tuple[str, str, list[float]]]:
    """(kind, text, box) of every OCR line that may be private: secrets, an e-mail (not an allowed one), a
    notification banner (top right), or anything on the brief's never_show list."""
    out = []
    for ln in lines:
        text, box = ln[0], ln[1:5]
        for kind, rx in SECRETS:
            hits = [m.group(0) for m in re.finditer(rx, text)]
            if kind == "email":
                hits = [h for h in hits if not public_email(h, allow)]
            if hits:
                out.append((kind, text, box))
        if box[0] > 0.65 and box[3] < 0.25 and NOTIFY.search(text):
            out.append(("notification", text, box))
        for n in never:
            if n and n.lower() in text.lower():
                out.append((f"never_show '{n}'", text, box))
    return out


def _activity(p: Project, video: Path, step: float) -> list[tuple[float, list[float] | None]]:
    """(change, box) per sample: how much of the picture changed since the last sample and where (normalised): the
    typing or the cursor, a window switch is everything."""
    raw = run([FFMPEG, "-v", "error", "-skip_frame", "nokey", "-i", video, "-vf", f"fps=1/{step},scale=160:90,format=gray",
               "-f", "rawvideo", "-"]).stdout
    fr = np.frombuffer(raw, np.uint8)[:len(raw) // 14400 * 14400].reshape(-1, 90, 160).astype(np.int16)
    out = [(0.0, None)]
    for a, b in zip(fr, fr[1:]):
        m = np.abs(b - a) > 24
        ys, xs = np.nonzero(m)
        out.append((round(float(m.mean()), 3), [round(xs.min() / 160, 2), round(ys.min() / 90, 2), round((xs.max() + 1) / 160, 2),
                                                round((ys.max() + 1) / 90, 2)] if len(xs) > 8 else None))
    return out


def _mmss(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def stream_map(p: Project) -> Path:
    from .cli import _tr_path
    from .media import src_path
    from .vision import ocr
    cat = read_json(p.out / "catalog.json") or die("run `jian prep` first")
    sid, e = max(((k, x) for k, x in cat["sources"].items() if x["kind"] == "video"), key=lambda kv: kv[1]["dur"])
    video = src_path(p, e)
    cfg = p.cfg.get("stream", {})
    step = float(cfg.get("map_step", 10))
    words = (read_json(_tr_path(p, sid, e)) or {}).get("words", [])
    shots = ocr(p, video, 1 / step, width=1600, fast=True)
    act = _activity(p, video, step)
    never, allow = p.cfg.get("never_show", []), p.cfg["stream"].get("allow_emails", [])
    bins = []
    for m in range(int(e["dur"] // 60) + 1):
        a, z = m * 60, (m + 1) * 60
        fs = [f for f in shots if a <= f["t"] < z]
        ks = [k for k, f in enumerate(shots) if a <= f["t"] < z]
        lines = [ln[0] for f in fs for ln in f["text"]]
        apps = [classify([ln[0] for ln in f["text"]]) for f in fs]
        said = [w["t"] for w in words if a <= w["s"] < z]
        text = " ".join(said)
        boxes = [act[k][1] for k in ks if k < len(act) and act[k][1] and act[k][0] < 0.15]  # typing, not a switch
        bins.append({"t": a, "words": len(said), "screen": max(set(apps), key=apps.count) if apps else "-",
                     "files": sorted(set(FILES.findall(" ".join(lines))))[:5],
                     "title": [ln[0] for f in fs[:1] for ln in f["text"] if ln[4] < 0.06][:3],
                     "said": " ".join(said[:16]), "cues": [c for c, rx in CUES if re.search(rx, text, re.I)],
                     "change": round(max((act[k][0] for k in ks if k < len(act)), default=0), 2),
                     "box": [round(float(np.median([b[i] for b in boxes])), 2) for i in range(4)] if boxes else None,
                     "private": [(f["t"], *x) for f in fs for x in private(f["text"], never, allow)]})
    # the vision model at each change of what's on screen (capped): a first label for the span that starts there
    changes = [b for k, b in enumerate(bins) if k and b["screen"] != bins[k - 1]["screen"]][: int(cfg.get("vlm_keyframes", 24))]
    if p.cfg.get("broll", {}).get("vlm", True) and changes:
        from .broll import Vision
        vis = Vision(p.cfg["broll"]["vlm_model"])
        frames = p.path("cache", "stream", "x").parent
        for b in changes:
            f = frames / f"{sid}.{int(b['t'])}.jpg"
            if not f.exists():
                run([FFMPEG, "-v", "error", "-y", "-ss", min(b["t"] + 30, e["dur"] - 2), "-i", video, "-frames:v", 1,
                     "-vf", "scale=1280:-2", "-pix_fmt", "yuvj420p", f])
            with p.timed("vlm", src=sid):
                b["note"] = vis.describe(f, 1, PROMPT).splitlines()[0][:160]
    write_json(p.path("cache", "stream", "map.json"), {"src": sid, "step": step, "bins": bins})
    rows = ["| min | words | screen | files / window | said (first words) | cues |", "|---|---|---|---|---|---|"]
    for b in bins:
        cell = ", ".join(b["files"]) or " / ".join(b["title"])[:60]
        rows.append(f"| {_mmss(b['t'])} | {b['words']} | {b['screen']}{' ↺' if b['change'] > 0.5 else ''} | "
                    f"{cell.replace('|', '/')} | {b['said'].replace('|', '/')} | {' '.join(b['cues'])} |")
    notes = [f"- {_mmss(b['t'])} → {b['screen']}: {b['note']}" for b in bins if b.get("note")]
    priv = [f"- {_mmss(t)} {kind}: {text!r} (box {', '.join(f'{v:.2f}' for v in box[:4])})"
            for b in bins for t, kind, text, box in b["private"]]
    code = [f"- {_mmss(b['t'])}: x {b['box'][0]:.2f}-{b['box'][2]:.2f}, y {b['box'][1]:.2f}-{b['box'][3]:.2f}"
            for b in bins if b["box"] and b["screen"] in ("editor", "terminal")]
    out = p.out / "stream.md"
    out.write_text(
        f"# Stream: {p.name} ({_mmss(e['dur'])}, source {sid})\n\nOne row per minute: words spoken, what's on screen "
        f"(OCR every {step:g} s; ↺ a big change of picture), files or window titles seen, the first words said, cues "
        "said aloud (topic, mark, goal, chat, works, recap). Read it to write jianji/stream_map.md: spans, threads, "
        "chapters (see the jian-edit skill, Streams).\n\n" + "\n".join(rows)
        + "\n\n## At each change of screen (vision model)\n" + ("\n".join(notes) or "- (vision model off)")
        + "\n\n## Privacy (OCR every map step; QA re-reads the cut every 2 s)\n" + ("\n".join(priv) or "- nothing found")
        + "\n\n## Where the activity is, on code (for zoom: `crop`)\n" + ("\n".join(code) or "- (none)") + "\n")
    print(out)
    return out
