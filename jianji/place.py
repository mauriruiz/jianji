"""Where burned text goes, decided from faces and the salient subject over each item's whole time on screen.

Captions stay in their lane; a cue moves to the alternate lane only where a face enters the lane, and the switch
holds across neighbouring cues. Badges and callouts take the first free slot of the safe zone (no face, least of
the subject, not over captions or another text); with none free they move up to 1 s. Never shorter or smaller:
a text keeps its reading time. Story lines next to each other share one position.
"""
from __future__ import annotations

from fractions import Fraction

from .vision import _grow, _overlap

LINE = 1.2       # line height / font size
CHAR = 0.48      # average glyph width / font size: libass boxes of Helvetica Neue Bold measure ~0.45
HOLD = 1.5       # s: a lane switch spreads to cues this close, so captions don't jump every second


def box(text: str, cx: float, top: float, size: float) -> tuple[float, float, float, float]:
    """Estimated box of a text block centred on cx with its top edge at `top` (pixels)."""
    lines = text.replace("\\N", "\n").split("\n")
    w = max(len(x) for x in lines) * CHAR * size + size * 0.4
    h = len(lines) * LINE * size + size * 0.3
    return (cx - w / 2, top, cx + w / 2, top + h)


def _hits(frames: list[dict], b: tuple, a: float, z: float, W: int, H: int) -> tuple[float, float]:
    """(share of frames in [a, z] where b covers a face, share where it covers the main subject of a shot with no
    face: the dish, the product). A subject filling most of the frame can't be avoided and is ignored."""
    nb = (b[0] / W, b[1] / H, b[2] / W, b[3] / H)
    inside = [f for f in frames if a <= f["t"] <= z]
    if not inside and frames:
        inside = [min(frames, key=lambda f: abs(f["t"] - (a + z) / 2))]
    if not inside:
        return 0.0, 0.0
    face = sub = 0
    for f in inside:
        faces = [_grow(x) for x in f["faces"]]
        if any(_overlap(nb, x) >= 0.15 * (x[2] - x[0]) * (x[3] - x[1]) for x in faces):
            face += 1
        if not faces:
            s = max(f.get("salient", []), key=lambda s: s[4] * (s[2] - s[0]) * (s[3] - s[1]), default=None)
            if s and (s[2] - s[0]) * (s[3] - s[1]) < 0.6 and _overlap(nb, s) >= 0.25 * (s[2] - s[0]) * (s[3] - s[1]):
                sub += 1
    return face / len(inside), sub / len(inside)


def _cross(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def caption_boxes(cues, lanes, cap: dict, W: int, H: int, alt_mv: float) -> list[tuple]:
    size = cap["size"] * H
    cx = cap["margin_l"] * H + (W - cap["margin_l"] * H - cap["margin_r"] * H) / 2
    out = []
    for (a, z, t), lane in zip(cues, lanes):
        n = t.count("\n") + 1
        h = n * LINE * size + size * 0.3
        top = alt_mv * H if lane == "alt" else cap["centre"] * H - h / 2 if cap.get("centre") else H - cap["margin_v"] * H - h
        out.append((a, z, box(t, cx, top, size)))
    return out


def caption_lanes(cues, frames, cap: dict, W: int, H: int, alt_mv: float) -> list[str]:
    """main for every cue, alt where a face enters the main lane (and the alt lane is clearer), spread to cues
    within HOLD seconds so the captions don't jump."""
    main = caption_boxes(cues, ["main"] * len(cues), cap, W, H, alt_mv)
    alt = caption_boxes(cues, ["alt"] * len(cues), cap, W, H, alt_mv)
    want = []
    for (a, z, bm), (_, _, ba) in zip(main, alt):
        fm, fa = _hits(frames, bm, a, z, W, H)[0], _hits(frames, ba, a, z, W, H)[0]
        want.append(fm > 0.1 and fa < fm)
    return ["alt" if any(w and abs((c[0] + c[1]) / 2 - (o[0] + o[1]) / 2) <= HOLD for w, o in zip(want, cues)) else "main"
            for c in cues]


def slots(W: int, H: int, zone: dict | None, w: float, h: float) -> list[tuple[str, float, float]]:
    """(name, centre x, top y) candidates inside the safe zone, top to bottom, centre then left then right."""
    if zone:
        k = H / zone["size"][1]
        x0, y0, x1, y1 = zone["left"] * k + 20, zone["top"] * k + 20, W - zone["right"] * k - 20, H - zone["bottom"] * k
    else:
        x0, y0, x1, y1 = 0.05 * W, 0.05 * H, 0.95 * W, 0.95 * H
    out = []
    for fy in (0.0, 0.14, 0.28, 0.42, 0.56):  # of the usable height
        top = y0 + fy * (y1 - y0)
        if top + h > y1:
            continue
        for name, cx in (("centre", (x0 + x1) / 2), ("left", x0 + w / 2), ("right", x1 - w / 2)):
            if name == "centre" or (x0 <= cx - w / 2 and cx + w / 2 <= x1 and w < 0.7 * (x1 - x0)):
                out.append((f"{name}@{fy:.2f}", cx, top))
    return out


SHIFTS = (0.0, 0.25, -0.25, 0.5, -0.5, 0.75, -0.75, 1.0, -1.0)  # s: a text belongs to its moment


def place_texts(texts: list[dict], F: Fraction, frames: list[dict], tx: dict, W: int, H: int, zone: dict | None,
                busy: list[tuple], total: float) -> tuple[dict, list[str]]:
    """{text id: {"cx", "top", "a", "z", "slot", "face", "subject"}} and a log of every decision that isn't the
    default. A text keeps its whole time on screen (its reading time): to get off a face it takes another slot or
    moves up to 1 s, never gets shorter or smaller. Story lines in a run (each within HOLD of the last) share one
    position, so they don't jump around; callouts each take their own slot."""
    from .render import text_style
    placed, log, taken = {}, [], list(busy)
    runs = []
    for t in sorted(texts, key=lambda t: t["rec"]):
        prev = next((r for r in reversed(runs) if r[0]["style"] == "txt"), None) if t["style"] == "txt" else None
        if prev and float((t["rec"] - prev[-1]["rec"] - prev[-1]["n"]) / F) <= HOLD:
            prev.append(t)
        else:
            runs.append([t])
    for run in runs:
        style = run[0]["style"]
        size, pref = (text_style(tx, style)[k] * H for k in ("size", "margin_v"))
        cands = None  # slots every text of the run fits in: story lines stay centred
        for t in run:
            b = box(t.get("shown", t["text"]), 0, 0, size)
            ok = {n: (cx, top) for n, cx, top in slots(W, H, zone, b[2] - b[0], b[3] - b[1])
                  if style == "hi" or n.startswith("centre")}
            cands = ok if cands is None else {n: v for n, v in cands.items() if n in ok}
        best = None
        for name, (cx, top) in sorted(cands.items(), key=lambda kv: (abs(kv[1][1] - pref), kv[0][0] != "c")):
            plan = []
            for t in run:
                text = t.get("shown", t["text"])
                a0, z0 = float(t["rec"] / F), float((t["rec"] + t["n"]) / F)
                b = box(text, cx, top, size)
                opt = None
                for sh in SHIFTS:
                    a, z = a0 + sh, z0 + sh
                    if a < 0 or z > total + 1e-6 or any(o[0] < z and a < o[1] and (o[3] == style or _cross(b, o[2]))
                                                         for o in taken + [p[1] for p in plan]):
                        continue
                    face, sub = _hits(frames, b, a, z, W, H)
                    c = (face > 0.1, sub > 0.3, abs(sh))
                    if opt is None or c < opt[0]:
                        opt = (c, (a, z, b, style), face, sub)
                    if not c[0] and not c[1]:
                        break  # the smallest move that clears it
                if opt:
                    plan.append((t, opt[1], opt[2], opt[3], opt[0]))
            cost = (len(run) - len(plan), sum(p[4][0] for p in plan), sum(p[4][1] for p in plan),
                    sum(p[4][2] for p in plan), abs(top - pref), name[0] != "c")
            if best is None or cost < best[0]:
                best = (cost, name, cx, top, plan)
            if cost[:4] == (0, 0, 0, 0):
                break
        if best is None:
            continue
        _, name, cx, top, plan = best
        default = abs(top - pref) <= H * 0.02 and name.startswith("centre")
        if not default and len(run) > 1:
            log.append(f"story lines {', '.join(t['id'] for t in run)} share slot {name}")
        for t, (a, z, b, _), face, sub, _ in plan:
            placed[t["id"]] = {"cx": round(cx), "top": round(top), "a": round(a, 3), "z": round(z, 3), "slot": name,
                               "face": round(face, 2), "subject": round(sub, 2)}
            taken.append((a, z, b, style))
            a0 = float(t["rec"] / F)
            what = [f"slot {name}"] if not default and len(run) == 1 else []
            if abs(a - a0) >= 0.05:
                what.append(f"moved {a - a0:+.2f}s")
            if face > 0.1:
                what.append(f"kept readable but over a face {face:.0%} of its {z - a:.1f}s: no slot or moment within "
                            "1 s is clear of faces and captions; re-anchor it or cut to a shot with room")
            if what:
                plain = " ".join(t.get("shown", t["text"]).replace(" ", " ").split())
                log.append(f"{t['id']} \"{plain}\": " + ", ".join(what))
        for t in run:
            if t["id"] not in placed:
                log.append(f"{t['id']}: no slot free of captions and other texts within 1 s; left in its default")
    return placed, log
