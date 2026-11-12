"""Edit (word ids, written by the model) -> timeline (frame-exact source ranges, written by code).

The model never writes timestamps for speech: it picks word ids, and every edge is placed here
(padding, quiet-spot snapping, pause shortening, filler removal, frame quantization) and checked.
"""
from __future__ import annotations

import math
import re
from fractions import Fraction

import numpy as np


class EditError(Exception):
    pass


BREATHING = {"tight": 0.75, "natural": 1.0, "relaxed": 1.3}
STYLES = ("txt", "hi", "chat", "chapter")  # story line, callout, a chat question card, a chapter card
CLASSES = ("intra", "sentence", "section")


def gaps(cut: dict) -> dict[str, tuple[float, float, float]]:
    """[min, target, max] kept gap per join class, scaled by cut.breathing."""
    f = BREATHING.get(str(cut.get("breathing", "natural")), None)
    if f is None:
        raise EditError(f"cut.breathing must be one of {', '.join(BREATHING)}, got {cut.get('breathing')!r}")
    return {c: tuple(round(x * f, 3) for x in cut[f"gap_{c}"]) for c in CLASSES}


def ends_sentence(text: str) -> bool:
    return text.rstrip(" \"'»)”’")[-1:] in (".", "?", "!", "…")


def voice_db(src: dict) -> float:
    """The speaker's usual level in this take: the median at the middle of every word."""
    if "voice" not in src:
        env, W = src["env"], src["words"]
        mids = [env[min(int((w["s"] + w["e"]) * 50), len(env) - 1)] for w in W] or [float(np.max(env))]
        src["voice"] = float(np.median(mids))
    return src["voice"]


def floor_db(src: dict) -> float:
    if "floor" not in src:
        src["floor"] = float(np.percentile(src["env"], 10))
    return src["floor"]


def _quiet(env, k: int, floor: float, db: float) -> bool:
    return 0 <= k < len(env) and env[k] <= floor + db


def decay_end(src: dict, t: float, limit: float, db: float) -> float:
    """First moment after t where the sound has decayed to the noise floor + db for 30 ms (the word's real tail,
    breath and reverb included); limit if it never does."""
    env, floor = src["env"], floor_db(src)
    run, k = 0, int(t * 100)
    while k < min(int(limit * 100), len(env)):
        run = run + 1 if _quiet(env, k, floor, db) else 0
        if run == 3:
            return (k - 2) / 100
        k += 1
    return limit


def onset_start(src: dict, t: float, limit: float, db: float) -> float:
    """Last moment before t where the sound was still at the noise floor + db (the word's real onset)."""
    env, floor = src["env"], floor_db(src)
    run, k = 0, int(t * 100) - 1
    while k >= max(int(limit * 100), 0):
        run = run + 1 if _quiet(env, k, floor, db) else 0
        if run == 3:
            return (k + 3) / 100
        k -= 1
    return limit


MAX_TAIL = 0.35  # s: a word's audible tail (decay, breath, reverb) past its aligned end, at most; aligned edges sit
                 # 0.1-0.15 s inside the word. Sound going on longer is more speech, maybe a word Whisper didn't write


def _quietest(src: dict, a: float, b: float) -> float:
    env = src["env"][max(int(a * 100), 0):max(int(b * 100), int(a * 100) + 1)]
    return (max(int(a * 100), 0) + int(np.argmin(env))) / 100 if len(env) else a


RISE = 8.0  # dB back up from the lowest point after a word: something new starts (a word, a laugh, a lid)


def _edge(src: dict, t: float, lim: float, db: float, step: int) -> float:
    """Walk from a word's aligned edge t towards lim (step +1: its end, -1: its start). The word is over where the
    sound reaches the noise floor + db for 30 ms, or, where the speaker runs straight on (a
    half-word Whisper never wrote), at the lowest point before the level climbs back RISE dB for 30 ms."""
    env, floor = src["env"], floor_db(src)
    k0, k1 = int(t * 100) - (step < 0), int(lim * 100) - (step < 0)
    low, quiet, rise = k0, 0, 0
    for k in range(k0, k1, step):
        if not 0 <= k < len(env):
            break
        quiet = quiet + 1 if _quiet(env, k, floor, db) else 0
        if quiet == 3:
            return (k - 2 * step + (step < 0)) / 100
        if abs(k - k0) < 12:
            low = k  # the first 0.12 s is still the word (aligned edges sit inside it): its syllables dip and rise
            continue
        if env[k] < env[low]:
            low = k
        rise = rise + 1 if env[k] >= env[low] + RISE else 0
        if rise == 3:
            return (low + (step < 0)) / 100
    return _quietest(src, *sorted((t, lim)))


def word_end(src: dict, e: float, limit: float, db: float) -> float:
    """Where a word audibly ends: its decay, at most MAX_TAIL past its aligned end e, never into what follows."""
    return _edge(src, e, min(limit, e + MAX_TAIL), db, 1)


def word_start(src: dict, s: float, limit: float, db: float) -> float:
    """Where a word audibly starts: its onset, at most MAX_TAIL before its aligned start (word_end's mirror)."""
    return _edge(src, s, max(limit, s - MAX_TAIL), db, -1)


def next_sound(src: dict, t: float, limit: float, db: float) -> float:
    """First moment at or after t where sound rises over the noise floor + db for 30 ms; limit if it doesn't."""
    env, floor = src["env"], floor_db(src)
    run, k = 0, int(t * 100)
    while k < min(int(limit * 100), len(env)):
        run = 0 if _quiet(env, k, floor, db) else run + 1
        if run == 3:
            return (k - 2) / 100
        k += 1
    return limit


def last_sound(src: dict, t: float, limit: float, db: float) -> float:
    """Last moment before t where sound was over the noise floor + db for 30 ms; limit if there's none."""
    env, floor = src["env"], floor_db(src)
    run, k = 0, int(t * 100) - 1
    while k >= max(int(limit * 100), 0):
        run = 0 if _quiet(env, k, floor, db) else run + 1
        if run == 3:
            return (k + 3) / 100
        k -= 1
    return limit


def breath_start(src: dict, onset: float, limit: float, look: float, word: dict) -> float | None:
    """An inhale right before a sentence: above the room, well below the voice, ending at the onset."""
    env, floor = src["env"], floor_db(src)
    voice = float(np.median(env[int(word["s"] * 100):max(int(word["e"] * 100), int(word["s"] * 100) + 1)]))
    start, k = None, int(onset * 100) - 1
    while k >= max(int(limit * 100), int((onset - look) * 100), 0):
        if floor + 8 < env[k] < voice - 6:  # +8: room noise wanders a few dB over its 10th percentile
            start = k
        elif env[k] >= voice - 6 or start is not None:
            break  # a voiced sound (a removed word), or the silence before the breath
        k -= 1
    return start / 100 if start is not None else None


def tone_spot(src: dict, near: float, need: float) -> float | None:
    """Start of a stretch of the source with nobody speaking, at least `need` long, closest to `near`:
    room tone to fill a gap where the speaker leaves none. Never digital silence."""
    spans = sorted([s[0], s[1]] for s in src.get("spans", []))
    words = src["words"]
    edges = [0.0] + [x for s in spans for x in s] + [src["dur"]]
    best = None
    for a, b in zip(edges[::2], edges[1::2]):  # gaps between VAD speech spans
        a, b = a + 0.1, b - 0.1
        if b - a < need or any(w["s"] < b and w["e"] > a for w in words):
            continue
        s = min(max(near - need / 2, a), b - need)
        if best is None or abs(s - near) < abs(best - near):
            best = s
    return round(best, 3) if best is not None else None


def reading_time(text: str, tx: dict) -> float:
    """Least time on screen for a burned text: a moment to find it plus its characters at reading speed (subtitles
    use ~17 chars/s; ours compete with the picture and the captions), never under min_dur, capped at max_dur: a
    text that needs longer is too long, shorten its words."""
    chars = len(" ".join(text.split()))
    return min(float(tx.get("max_dur", 4.5)),
               max(float(tx.get("min_dur", 1.5)), float(tx.get("read_lead", 0.4)) + chars / float(tx.get("read_cps", 13))))


SHOT_TYPES = ("place", "detail", "action", "reaction")
# the vision model's SHOWS line -> shot type, first match wins. ponytail: a keyword list; the edit's "shot" label
# overrides it, so a wrong guess costs one label
_SHOT_WORDS = (("reaction", r"smil|laugh|react|tast|chew|bite|biting|grin"),
               ("action", r"pour|mix|stir|scoop|prepar|serv|cook|fry|frying|slic|cutting|pull|dip|flip|sprinkl|open"),
               ("place", r"fa[cç]ade|storefront|exterior|outside|street|sign|logo|stall|shop|restaurant|counter|"
                         r"display|menu|market|entrance|interior|queue|crowd|building"))
MOTION_FPS = 10


def shot_type(item: dict, src: dict, default: str | None = "detail") -> str | None:
    """A picture shot's type: the edit's "shot", else a guess from the vision model's note on its source, else
    `default` (None: not a shot with a hold rule, e.g. the talking head)."""
    if item.get("shot"):
        if item["shot"] not in SHOT_TYPES:
            raise EditError(f"{item.get('id')}: shot must be one of {', '.join(SHOT_TYPES)}, got {item['shot']!r}")
        return item["shot"]
    m = re.search(r"SHOWS:(.*)", src.get("note") or "")
    if m:
        return next((t for t, words in _SHOT_WORDS if re.search(rf"\b({words})", m.group(1), re.I)), "detail")
    return default


def settle(src: dict, t: float, limit: float, since: float | None = None) -> float | None:
    """Where the picture's motion dies down at or after t (an action or a reaction completing): the first moment
    whose next 0.3 s are calm, i.e. at the source's resting level or under half the shot's own peak (from `since`;
    a handheld selfie never rests). t without motion data; None if still moving at limit."""
    m = src.get("motion")
    if m is None or not len(m):
        return t
    # ponytail: global frame difference, so camera shake counts as motion; subtract the camera's own motion
    # (optical flow) if handheld shots never settle
    calm = 2 * float(np.percentile(m, 20)) + 1.0
    if since is not None:
        calm = max(calm, 0.5 * float(m[int(since * MOTION_FPS):int(t * MOTION_FPS) + 1].max(initial=0)))
    for k in range(int(t * MOTION_FPS), min(int(limit * MOTION_FPS), len(m) - 1) + 1):
        if m[k:k + 3].mean() <= calm:
            return k / MOTION_FPS
    return None


def _hold(cid: str, typ: str, i: float, o: float, lo: float, hi: float, src: dict, shots: dict,
          warnings: list[str]) -> tuple[float, float]:
    """Stretch a shot's [i, o] in the source, within [lo, hi], to its type's least hold; an action or a reaction
    also runs on until its motion settles. Later first (the shot keeps its moment), then earlier."""
    need, o2 = float(shots[typ]), o
    if typ in ("action", "reaction"):
        mx = float(shots.get("settle_max", 1.5))
        s = settle(src, o, min(hi, o + mx), i)
        if s is None:
            where = (f"{mx}s past its out-point" if hi >= o + mx else "where its source ends"
                     if hi >= float(src["dur"]) - 1e-3 else "before the next word")
            warnings.append(f"{cid}: the {typ} is still moving {where}: let it finish or end it on a calmer frame")
        o2 = max(o2, s or o)
    o2 = max(o2, min(hi, i + need))
    i2 = min(i, max(lo, o2 - need))
    if o2 - i2 < need - 1e-3:
        warnings.append(f"{cid}: a {typ} shot wants {need:.1f}s on screen, its source has {o2 - i2:.2f}s here")
    if (round(i2, 3), round(o2, 3)) != (round(i, 3), round(o, 3)):
        warnings.append(f"{cid}: {typ} shot held {o2 - i2:.1f}s (was {o - i:.1f}s), extended in the source")
    return round(i2, 3), round(o2, 3)


def ends_clause(text: str) -> bool:
    return text.rstrip(" \"'»)”’")[-1:] in (".", "?", "!", "…", ",", ";", ":")


def is_boundary(src: dict, k: int, cut: dict) -> bool:
    """Word k closes a clause or a sentence: punctuated, and followed by an audible pause (or nothing), so a cut
    after it sounds finished. A period with the next word already sounding is a speaker running on: Whisper's
    punctuation isn't prosody."""
    W = src["words"]
    if not ends_clause(W[k]["t"]):
        return False
    if k + 1 >= len(W) or W[k + 1]["s"] - W[k]["e"] >= 1.5:
        return True  # 1.5 s with no word: a pause, even under keyboard and music (streams on one audio track)
    db = cut.get("tail_db", 6)
    dec = word_end(src, W[k]["e"], W[k + 1]["s"], db)
    # a pause: no voice (a decaying word or room noise is still a pause). Under the floor + 2x tail_db, or within 10 dB
    # of the speaker's level where the room sits higher than that: music and keys on a stream, a mic behind a noise gate
    quiet = max(2 * db, voice_db(src) - 10 - floor_db(src))
    return next_sound(src, dec, W[k + 1]["s"], quiet) - dec >= float(cut.get("boundary_gap", 0.12))


def carries_on(W: list[dict], last: int, first: int) -> bool:
    """Speech that stops after word `last` and resumes at `first` of the same take is one sentence going on: a
    filler or cue word was taken out between. A cut with nothing taken out (a pause shortened) is not: inside a
    clause it chops the sentence ("está | bien")."""
    return first > last + 1 and all(W[x].get("filler") or W[x].get("cue") for x in range(last + 1, first))


def _trailing(W: list[dict], k: int, a: int, b: int) -> bool:
    """A filler that closes its sentence ("pica mucho, eh."): said right after the word before it and ending the
    sentence or the clip. It stays: without it the sentence sounds chopped."""
    return (k > a and not W[k - 1].get("filler") and W[k]["s"] - W[k - 1]["e"] < 0.3
            and (ends_sentence(W[k]["t"]) or k == b))


def _finish(clips: list[dict], sources: dict, cut: dict, warnings: list[str]) -> list[dict]:
    """A clip that stops mid-sentence runs on to the sentence's (or clause's) end, at most finish_max seconds more
    and never into words the edit uses elsewhere: half a second more of natural speech beats a sentence chopped
    off. A clip with "abrupt": "<why>" (the speaker jumps topic with no pause) keeps its end."""
    used = {(c["src"], k) for c in clips if "from" in c for k in range(c["from"], c["to"] + 1)}
    out = []
    for n, c in enumerate(clips):
        c = dict(c)
        out.append(c)
        if "from" not in c or c.get("abrupt") or c.get("src") not in sources:
            continue
        src, nxt = sources[c["src"]], clips[n + 1] if n + 1 < len(clips) else None
        W, k = src["words"], c["to"]
        if not 0 <= k < len(W) or is_boundary(src, k, cut):
            continue
        if nxt and nxt.get("src") == c["src"] and (carries_on(W, k, nxt["from"]) if "from" in nxt else
                                                    -0.05 <= float(nxt["in"]) - W[k]["e"] <= 1.0):
            continue  # the next clip picks the sentence up
        j, lim = k, W[k]["e"] + float(cut["finish_max"])
        while j + 1 < len(W) and W[j + 1]["e"] <= lim and (c["src"], j + 1) not in used and not W[j + 1].get("cue"):
            j += 1
            if is_boundary(src, j, cut):
                c["to"] = j
                used |= {(c["src"], x) for x in range(k + 1, j + 1)}
                warnings.append(f"{c['id']}: stopped mid-sentence after {W[k]['t']!r}; runs on to {W[j]['t']!r} "
                                f"(+{W[j]['e'] - W[k]['e']:.1f}s) to finish it")
                break
    return out


def picture_runs(segs: list[dict], overlays: list[dict], F: Fraction) -> list[dict]:
    """What's on screen, in order: {"start", "end" (frames), "kind": "piece" | "overlay", "id"}. Pieces that go on
    from each other in their source (room tone under the picture, a raw tail of the same take) are one run."""
    cover = sorted(overlays, key=lambda o: o["rec"])
    runs = []
    for s in segs:
        cur, end = s["rec"], s["rec"] + s["n"]
        for o in cover:
            x, y = o["rec"], o["rec"] + o["n"]
            if y <= cur or x >= end:
                continue
            if x > cur:
                runs.append({"start": cur, "end": x, "kind": "piece", "seg": s})
            cur = max(cur, y)
        if cur < end:
            runs.append({"start": cur, "end": end, "kind": "piece", "seg": s})
    runs += [{"start": o["rec"], "end": o["rec"] + o["n"], "kind": "overlay", "ov": o} for o in cover]
    runs.sort(key=lambda r: r["start"])
    at = lambda r, f: r["seg"]["in"] + float((f - r["seg"]["rec"]) / F)  # source time of timeline frame f
    out = []
    for r in runs:
        p = out[-1] if out else None
        if (p and p["kind"] == r["kind"] == "piece" and p["end"] == r["start"] and p["seg"]["src"] == r["seg"]["src"]
                and abs(at(p, p["end"]) - at(r, r["start"])) <= 1.5 / F):
            p["end"], p["seg"] = r["end"], r["seg"]
        else:
            out.append(r)
    for r in out:
        r["id"] = r["ov"]["id"] if r["kind"] == "overlay" else r["seg"]["clip"]
    return out


def _fill(segs: list[dict], overlays: list[dict], sources: dict, F: Fraction, total: int, mv: int,
          warnings: list[str]):
    """Nothing on screen for less than mv frames (a jarring flash): a talking-head stretch that short next to
    B-roll is covered by stretching the B-roll over it, later into its source or earlier. What's left is warned
    (QA fails it): the editor merges it into a neighbour, extends or drops it."""
    order = sorted(overlays, key=lambda o: o["rec"])
    for _ in range(4 * len(segs) + 4):
        runs = picture_runs(segs, overlays, F)
        fixed = False
        for k in sorted((k for k, r in enumerate(runs) if r["kind"] == "piece" and r["end"] - r["start"] < mv),
                        key=lambda k: runs[k]["end"] - runs[k]["start"]):
            d = runs[k]["end"] - runs[k]["start"]
            P, N = runs[k - 1] if k else None, runs[k + 1] if k + 1 < len(runs) else None
            if P and P["kind"] == "overlay" and P["end"] == runs[k]["start"] and not (P["ov"].get("locked") or P["ov"].get("pinned")):
                o = P["ov"]
                i = order.index(o)
                limit = order[i + 1]["rec"] if i + 1 < len(order) else total
                if math.floor((float(sources[o["src"]]["dur"]) - o["in"]) * F + 1e-6) - o["n"] >= d and \
                        o["rec"] + o["n"] + d <= limit:
                    o["n"] += d
                    warnings.append(f"{o['id']} runs {float(d / F):.2f}s longer to cover a "
                                    f"{float(d / F):.2f}s flash of {runs[k]['id']}")
                    fixed = True
                    break
            if N and N["kind"] == "overlay" and N["start"] == runs[k]["end"] and not (N["ov"].get("locked") or N["ov"].get("pinned")):
                o = N["ov"]
                i = order.index(o)
                limit = order[i - 1]["rec"] + order[i - 1]["n"] if i else 0
                if math.floor(o["in"] * F + 1e-6) >= d and o["rec"] - d >= limit:
                    o["rec"], o["in"], o["n"] = o["rec"] - d, round(o["in"] - d / F, 3), o["n"] + d
                    warnings.append(f"{o['id']} starts {float(d / F):.2f}s earlier to cover a "
                                    f"{float(d / F):.2f}s flash of {runs[k]['id']}")
                    fixed = True
                    break
        if not fixed:
            break
    for r in picture_runs(segs, overlays, F):
        if r["end"] - r["start"] < mv:
            warnings.append(f"{r['id']} on screen {float((r['end'] - r['start']) / F):.2f}s at "
                            f"{float(r['start'] / F):.1f}s (min {float(mv / F):.1f}s): extend it, merge it into a "
                            "neighbour or drop it")


def _runs(clip: dict, src: dict, cut: dict) -> tuple[list[dict], set, dict]:
    W = src["words"]
    a, b = clip["from"], clip["to"]
    if not (0 <= a <= b < len(W)):
        raise EditError(f"{clip['id']}: word range {a}-{b} outside source {clip['src']} (0-{len(W) - 1})")
    user_cut = set()
    for x, y in clip.get("cut", []):
        user_cut |= set(range(x, y + 1))
    keep = set(clip.get("keep", []))
    auto = set(cut.get("auto_cut", [])) - keep
    fillers = {k for k in range(a, b + 1) if "filler" in auto and W[k].get("filler") and k not in user_cut
               and not _trailing(W, k, a, b)}
    cues = {k for k in range(a, b + 1) if W[k].get("cue")}
    voiced = [v for v in src.get("voiced", []) if "voiced" in auto and W[a]["s"] < v[0] and v[1] < W[b]["e"]]
    stats = {"fillers": 0, "voiced": 0, "pauses": 0}
    joined = set()  # words whose pause before them stays: cutting it would strand a sliver

    def split(excluded):
        kept = [k for k in range(a, b + 1) if k not in excluded]
        runs, cur = [], None
        for k in kept:
            if cur is None:
                cur = {"first": k, "last": k, "in": "clip"}
                continue
            prev = cur["last"]
            gap0, gap1 = W[prev]["e"], W[k]["s"]
            why = None
            if k != prev + 1:
                why = "cut"
            elif (gap1 - gap0 > cut["max_pause"] and "pauses" not in keep and k not in joined
                  and ends_clause(W[prev]["t"])):  # mid-clause hesitations stay; some pauses are the visual payoff
                why = "pause"
            elif any(gap0 <= v[0] and v[1] <= gap1 for v in voiced):
                why = "voiced"
            if why:
                cur["out"] = why
                runs.append(cur)
                cur = {"first": k, "last": k, "in": why}
            else:
                cur["last"] = k
        if cur:
            cur["out"] = "clip"
            runs.append(cur)
        return runs

    excluded = user_cut | fillers | cues
    runs = split(excluded)
    # never leave a sliver: a filler cut or a pause cut that strands a piece shorter than min_segment is undone
    changed = True
    while changed:
        changed = False
        for r in runs:
            if W[r["last"]]["e"] - W[r["first"]]["s"] >= cut["min_segment"] or r["in"] == r["out"] == "clip":
                continue
            for k in (r["first"] - 1, r["last"] + 1):
                if k in fillers and k in excluded:
                    excluded.discard(k)
                    changed = True
            sides = [k for k, why in ((r["first"], r["in"]), (r["last"] + 1, r["out"])) if why == "pause"
                     and W[k]["s"] - W[k - 1]["e"] <= 1.0]  # a longer silence stays cut: _fill or the editor handle it
            if sides:  # join it to the neighbour across the shorter pause; the longer one is still cut
                k = min(sides, key=lambda k: W[k]["s"] - W[k - 1]["e"])
                joined.add(k)
                changed = True
        if changed:
            runs = split(excluded)
    stats["fillers"] = len(fillers & excluded)
    stats["voiced"] = sum(r["in"] == "voiced" for r in runs)
    stats["pauses"] = sum(r["in"] == "pause" for r in runs)
    return runs, excluded, stats


def _bounds(it: dict, src: dict) -> tuple[float, float]:
    """Hard limits for a run's edges: never reach into a word, or a removed vocal sound, outside the run."""
    W, r = src["words"], it["run"]
    f, l = r["first"], r["last"]
    lo = W[f - 1]["e"] + 0.01 if f > 0 else 0.0
    hi = W[l + 1]["s"] - 0.01 if l + 1 < len(W) else src["dur"]
    voiced = src.get("voiced", [])
    if r["in"] == "voiced":
        lo = max(v[1] for v in voiced if v[1] <= W[f]["s"] + 1e-6 and v[0] >= W[f - 1]["e"] - 1e-6)
    if r["out"] == "voiced":
        hi = min(v[0] for v in voiced if v[0] >= W[l]["e"] - 1e-6 and v[1] <= W[l + 1]["s"] + 1e-6)
    return lo, hi


def _after(it: dict, src: dict, cut: dict) -> tuple[float, float]:
    """(where the run's last word audibly ends, quiet time after that before the next sound starts)."""
    W, l = src["words"], it["run"]["last"]
    dec = word_end(src, W[l]["e"], it["hi"], cut["tail_db"])  # tail, breath and reverb are still the word
    # the next sound is a voice: within 10 dB of the speaker's usual level, not the room (keys and music on a
    # stream sit right at floor + tail_db, and must not cut every tail to nothing)
    db = max(cut["tail_db"], voice_db(src) - 10 - floor_db(src))
    nxt = next_sound(src, dec, min(it["hi"], W[l + 1]["s"]) if l + 1 < len(W) else it["hi"], db)
    it["next_sound"] = nxt  # frame rounding mustn't reach it either
    return dec, max(0.0, nxt - dec - 0.03)  # stay 30 ms clear of the next voice


def _before(it: dict, src: dict, cut: dict, breath: bool) -> tuple[float, float, float]:
    """(where the run's first word audibly starts, quiet time before it since the previous sound ended,
    the inhale kept in front of it)."""
    W, f = src["words"], it["run"]["first"]
    ons = word_start(src, W[f]["s"], it["lo"], cut["tail_db"])
    prv = last_sound(src, ons, max(it["lo"], W[f - 1]["e"]) if f > 0 else it["lo"], cut["tail_db"])
    quiet = max(0.0, ons - prv)
    b = breath_start(src, ons, ons - quiet, cut["breath"], W[f]) if breath else None
    return ons, quiet, (ons - b if b is not None else 0.0)


def _join(P: dict, N: dict, sources: dict, cut: dict, G: dict) -> dict:
    """What a cut separates, and the silence it keeps: the quiet after P's last word audibly ends plus the quiet
    before N's first word audibly starts. Taken from the source's own pauses; where the speaker runs straight
    on, `room` seconds of room tone are created."""
    if "run" not in P or "run" not in N:  # B-roll / natural sound on one side: no speech gap to keep
        return {"class": "picture"}
    sp, sn = sources[P["src"]], sources[N["src"]]
    wp, wn = sp["words"][P["run"]["last"]], sn["words"][N["run"]["first"]]
    same_gap = P["src"] == N["src"] and N["run"]["first"] == P["run"]["last"] + 1
    section = N["clip"].get("sec") != P["clip"].get("sec")
    if same_gap and not section and N["run"]["in"] == "clip" and wn["s"] - wp["e"] <= cut["max_pause"]:
        mid = (wp["e"] + wn["s"]) / 2  # back-to-back clips, nothing removed: no cut at all
        return {"class": "none", "tail": mid - wp["e"], "head": wn["s"] - mid}
    cls = "section" if section else "sentence" if ends_clause(wp["t"]) else "intra"  # intra: a filler out
    mn, tg, mx = G[cls]
    dec, q_after = _after(P, sp, cut)
    ons, q_before, breath = _before(N, sn, cut, cls != "intra")
    span = max(0.0, ons - dec)
    if same_gap:  # both edges come out of one source pause: a sound inside it (a blip, a breath) splits it
        q_after, q_before = min(q_after, span), min(q_before, span)
    # never less than this after the decay; a sentence's last word rings out 0.1 s (air on both sides of a cut,
    # and a quiet word decaying into a noisy room isn't clipped)
    t_min = min(q_after, cut["tail_margin"] if cls == "intra" else max(cut["tail_margin"], 0.1))
    h_min = min(q_before, breath + cut["onset_margin"])  # an inhale stays with its sentence
    g_head = max(h_min, min(q_before, tg / 2))
    g_tail = max(t_min, min(q_after, tg - g_head))
    g_head = max(h_min, min(q_before, tg - g_tail))  # one side short of silence: the other gives more
    if same_gap and g_tail + g_head > span:  # one pause can't give more than itself
        g_head = max(min(h_min, span - t_min), span - g_tail)
        g_tail = span - g_head
    kept = g_tail + g_head
    room = min(tg - kept, float(cut["max_room"])) if kept < mn else 0.0  # no pause to keep: create one
    return {"class": cls, "tail": round(dec - wp["e"] + g_tail, 3), "head": round(wn["s"] - ons + g_head, 3),
            "kept": round(kept, 3), "room": round(room, 3), "min": mn, "target": tg, "quiet": round(q_after, 3)}


def _ends(it: dict, src: dict, cut: dict, first: bool, last: bool) -> tuple[float | None, float | None]:
    """Head and tail for the start and end of the whole cut (or next to a picture-only clip): the word's onset
    and decay, a little lead at the very start and some air at the very end."""
    W, r = src["words"], it["run"]
    ons, q_before, breath = _before(it, src, cut, True)
    dec, q_after = _after(it, src, cut)
    head = W[r["first"]]["s"] - ons + min(q_before, max(breath + cut["onset_margin"], 0.1 if first else cut["onset_margin"]))
    tail = dec - W[r["last"]]["e"] + min(q_after, 0.4 if last else cut["tail_margin"])
    return head, tail


def _stacked(clip: dict, layouts: dict | None) -> dict:
    """A clip with "layout": "<name>" shows two regions of its source one over the other (a vertical short out of a
    stream: the camera on top, the screen below). The layout (yours, `[layouts.<name>]`) names the regions as
    [x0, y0, x1, y1] of the source and where the frame splits; the clip's own "crop" replaces the bottom region (a
    zoom on the code that matters)."""
    name = clip.get("layout")
    if not name:
        return clip
    lay = (layouts or {}).get(name)
    if not isinstance(lay, dict):
        raise EditError(f"{clip['id']}: layout {name!r} isn't defined ([layouts.{name}] in your config.toml); "
                        f"defined: {', '.join(layouts or {}) or 'none'}")
    st = {"top": lay.get("top"), "bottom": clip.get("crop") or lay.get("bottom"), "split": lay.get("split", 0.36)}
    for k in ("top", "bottom"):
        r = st[k]
        if not (isinstance(r, list) and len(r) == 4 and all(isinstance(x, (int, float)) for x in r)
                and 0 <= r[0] < r[2] <= 1 and 0 <= r[1] < r[3] <= 1):
            raise EditError(f"{clip['id']}: layout {name!r}: {k} must be [x0, y0, x1, y1] within 0-1, got {r!r}")
    if not (isinstance(st["split"], (int, float)) and 0.1 <= st["split"] <= 0.9):
        raise EditError(f"{clip['id']}: layout {name!r}: split is the top panel's share of the height (0.1-0.9)")
    if clip.get("speed"):
        raise EditError(f"{clip['id']}: a clip with a layout can't be sped up")
    return {**{k: v for k, v in clip.items() if k != "crop"}, "stack": st}


def add_outro(tl: dict, outro: dict | None, tx: dict | None) -> dict:
    """Your closing line (`[outro] text`, e.g. when you're live) over the last seconds of the cut: at least `seconds`
    and its reading time. A text of the same style still up by then ends where the outro starts."""
    text = str((outro or {}).get("text") or "").strip()
    if not text or not tl["frames"]:
        return tl
    F = Fraction(tl["fps"])
    style = outro.get("style", "hi")
    if style not in STYLES:
        raise EditError(f"[outro] style must be one of {', '.join(STYLES)}, got {style!r}")
    n = min(tl["frames"], round(max(float(outro.get("seconds", 3)), reading_time(text, tx or {})) * F))
    rec = tl["frames"] - n
    texts = []
    for t in tl["texts"]:
        if t["style"] == style and t["rec"] + t["n"] > rec:
            if t["rec"] >= rec - round(F):  # under a second left of it: the outro takes its place
                tl["warnings"].append(f"{t['id']} dropped: the outro is on screen then")
                continue
            t = {**t, "n": rec - t["rec"]}
            tl["warnings"].append(f"{t['id']} ends early, where the outro starts")
        texts.append(t)
    tl["texts"] = texts + [{"id": "outro", "text": text, "style": style, "rec": rec, "n": n}]
    return tl


def compile_timeline(edit: dict, sources: dict, cut: dict, timeline: dict | None = None, texts: dict | None = None,
                     shots: dict | None = None, layouts: dict | None = None) -> dict:
    """timeline: the profile's [timeline] (size, fps); the edit's own "timeline" wins. texts: the profile's [texts]
    (reading speed: every text stays up long enough to read). shots: its [shots] (least hold per shot type).
    layouts: your named two-panel layouts, for clips with "layout"."""
    clips = edit.get("clips", [])
    if not clips:
        raise EditError("edit has no clips")
    ids = [c.get("id") for c in clips]
    if len(set(ids)) != len(ids) or None in ids:
        raise EditError("every clip needs a unique id")
    clips = [_stacked(c, layouts) for c in clips]
    first_video = next((sources[c["src"]] for c in clips if c["src"] in sources and sources[c["src"]].get("fps")), None)
    tl_cfg = {**(timeline or {}), **edit.get("timeline", {})}
    F = Fraction(tl_cfg.get("fps") or (first_video or {}).get("fps") or "25/1")
    size = tl_cfg.get("size") or ([first_video["w"], first_video["h"]] if first_video else [1920, 1080])
    segs, warnings = [], []
    stats = {"fillers": 0, "voiced": 0, "pauses": 0}
    if "finish_max" in cut:
        clips = _finish(clips, sources, cut, warnings)
    items = []  # every run of kept words (or raw non-speech clip), in output order
    for clip in clips:
        if clip["src"] not in sources:
            raise EditError(f"{clip['id']}: unknown source {clip['src']}")
        src = sources[clip["src"]]
        if "from" in clip:
            fix = clip.get("fix", {})  # caption text for words Whisper got wrong: {"27": "Kubernetes", "28": ""}
            bad = [k for k in fix if not k.isdigit() or not clip["from"] <= int(k) <= clip["to"]]
            if bad:
                raise EditError(f"{clip['id']}: fix names words outside the clip: {bad}")
            runs, excluded, st = _runs(clip, src, cut)
            for k in st:
                stats[k] += st[k]
            for r in runs:
                it = {"clip": clip, "src": clip["src"], "run": r, "excluded": excluded, "fix": fix}
                it["lo"], it["hi"] = _bounds(it, src)
                items.append(it)
            for c in src.get("cues", []):
                if c["type"] == "retake" and "drop" in c and clip["from"] <= c["drop"][1] and clip["to"] >= c["drop"][0]:
                    warnings.append(f"{clip['id']} keeps words {c['drop'][0]}-{c['drop'][1]}, an attempt a RETAKE cue dropped")
        else:
            i, o = float(clip["in"]), float(clip["out"])
            if not (0 <= i < o <= src["dur"] + 1e-3):
                raise EditError(f"{clip['id']}: {i}-{o} outside source {clip['src']} (0-{src['dur']})")
            exact = clip.get("locked") or clip.get("pinned")  # pulled from Resolve: those frames, exactly
            typ = shot_type(clip, src, None if src.get("words") else "detail") if shots and not exact else None
            it = {"clip": clip, "src": clip["src"], "raw": (i, o), "shot": typ}
            if exact and clip.get("speech"):  # captions and QA from the words inside them
                it["kept"] = [w["i"] for w in src.get("words", []) if w["s"] >= i - 0.005 and w["e"] <= o + 0.005]
            items.append(it)
    for k, it in enumerate(items):  # picture shots: on screen long enough to read, never stretched into a word
        if not it.get("shot"):
            continue
        src, (i, o) = sources[it["src"]], it["raw"]
        start = lambda x: x["raw"][0] if "raw" in x else sources[x["src"]]["words"][x["run"]["first"]]["s"]
        end = lambda x: x["raw"][1] if "raw" in x else sources[x["src"]]["words"][x["run"]["last"]]["e"]
        nxt, prv = items[k + 1] if k + 1 < len(items) else None, items[k - 1] if k else None
        if (nxt and nxt["src"] == it["src"] and -0.05 <= start(nxt) - o <= 1.0) or \
                (prv and prv["src"] == it["src"] and -0.05 <= i - end(prv) <= 1.0) or \
                any(ov.get("over") == (nxt or {}).get("clip", {}).get("id") and ov.get("src") == it["src"]
                    and -0.05 <= float(ov.get("in", -9)) - o <= 1.0 for ov in edit.get("overlays", [])):
            continue  # the same take goes on (or came before) on screen: one longer shot, its action included
        W = src.get("words", [])
        lo = max([w["e"] + 0.05 for w in W if w["e"] <= i + 1e-6], default=0.0)
        hi = min([w["s"] - 0.05 for w in W if w["s"] >= o - 1e-6], default=float(src["dur"]))
        it["raw"] = _hold(it["clip"]["id"], it["shot"], i, o, lo, hi, src, shots, warnings)
    # every cut gets the breathing room of what it separates: within a sentence, between sentences, between sections
    G = gaps(cut)
    for P, N in zip(items, items[1:]):
        N["join"] = P["join_out"] = _join(P, N, sources, cut, G)
    for P, N in zip(items, items[1:] + [None]):  # speech stops on a clause or sentence end, never mid-sentence
        if "run" not in P or P["clip"].get("abrupt"):
            continue
        W, last = sources[P["src"]]["words"], P["run"]["last"]
        if N and N["src"] == P["src"] and ("run" in N and carries_on(W, last, N["run"]["first"]) or
                                           "raw" in N and -0.05 <= N["raw"][0] - W[last]["e"] <= 1.0):
            continue
        if not is_boundary(sources[P["src"]], last, cut):
            warnings.append(f"{P['clip']['id']}: speech cut after {W[last]['t']!r} lands mid-sentence (QA fails it): "
                            "end on a clause or sentence end, or give the clip \"abrupt\": \"<why>\" if the speaker "
                            "jumps to another topic")
    for k, it in enumerate(items):
        clip, src = it["clip"], sources[it["src"]]
        if "raw" in it:
            i, o = it["raw"]
            speed = float(clip.get("speed", 1))
            if speed != 1 and "tone" not in clip:  # a sped-up bridge: the picture runs fast over the take's room tone
                spot = tone_spot(src, i, (o - i) / speed + 0.1)
                if spot is None:
                    warnings.append(f"{clip['id']}: no room tone in {it['src']} for its sped-up picture: it will be silent")
                clip = {**clip, **({"tone": spot} if spot is not None else {})}
            segs.append({"clip": clip["id"], "sec": clip.get("sec"), "src": it["src"], "in": round(i, 4),
                         "n": max(1, round((o - i) / speed * F)), "kept": it.get("kept", []), "shot": it["shot"],
                         **({"speed": speed} if speed != 1 else {}), **{k: clip[k] for k in ("crop", "blur", "stack") if clip.get(k)},
                         **({"exact": True} if clip.get("locked") or clip.get("pinned") else {}),
                         **({"gain_db": clip["gain_db"]} if "gain_db" in clip else {}),
                         **({"tone": clip["tone"]} if "tone" in clip else {}),
                         **({"fix": {k: v for k, v in clip["fix"].items() if int(k) in it.get("kept", [])}}
                            if clip.get("fix") and it.get("kept") else {})})
            continue
        W, r = src["words"], it["run"]
        jin, jout = it.get("join"), it.get("join_out")
        head0, tail0 = _ends(it, src, cut, jin is None, jout is None)
        head = jin["head"] if jin and "head" in jin else head0
        tail = jout["tail"] if jout and "tail" in jout else tail0
        i = round(max(W[r["first"]]["s"] - head, it["lo"]), 3)
        o = round(min(W[r["last"]]["e"] + tail, it["hi"]), 3)
        n = max(1, math.ceil((o - i) * F - 1e-9))  # ceil: frame rounding never clips a kept word...
        if i + n / F > it.get("next_sound", 1e9) - 0.005 and i + (n - 1) / F >= W[r["last"]]["e"] + 0.02:
            n -= 1  # ...but a next sound right there (a word nobody wrote) isn't let in by rounding up
        # room = silence available past each edge; the renderer shrinks the crossfade to fit it,
        # so a fade handle never leaks the start of a removed word
        room = [round(max(0.0, i - it["lo"]), 3), round(max(0.0, it["hi"] - (i + n / F)), 3)]
        segs.append({"clip": clip["id"], "sec": clip.get("sec"), "src": it["src"], "in": i, "n": n, "room": room, **{k: clip[k] for k in ("crop", "blur", "stack") if clip.get(k)},
                     "words": [r["first"], r["last"]],
                     "kept": [k for k in range(r["first"], r["last"] + 1) if k not in it["excluded"]],
                     **({"gain_db": clip["gain_db"]} if "gain_db" in clip else {}),
                     **({"join": {k: jin[k] for k in ("class", "kept", "room", "min", "target") if k in jin}}
                        if jin and jin["class"] != "none" else {})})
        fx = {k: v for k, v in it["fix"].items() if int(k) in segs[-1]["kept"]}
        if fx:
            segs[-1]["fix"] = fx
        if jout and jout.get("room"):  # the speaker ran straight on: room tone, under the picture going on
            need = jout["room"]
            spot = tone_spot(src, o, need + 0.1)
            if spot is None:
                warnings.append(f"{clip['id']}: no pause to keep and no room tone in {it['src']} to fill one")
                continue
            segs.append({"clip": clip["id"], "sec": clip.get("sec"), "src": it["src"], "in": round(i + n / F, 3),
                         "n": max(1, round(need * F)), "kept": [], "tone": spot, "room": [0.05, 0.05],
                         **({"stack": clip["stack"]} if clip.get("stack") else {})})
            stats["room"] = round(stats.get("room", 0) + need, 3)
            if jout["class"] == "section" and jout["room"] + jout["tail"] + jout["head"] < jout["min"]:
                warnings.append(f"{clip['id']} -> next section: the speaker runs straight on; "
                                f"{need:.2f}s of room tone created, the section wants {jout['target']:.2f}s: "
                                "give it a B-roll beat")
    shot_list, pre = [], 0  # picture shots with a hold rule, at their place in the cut (before merging)
    for s in segs:
        typ = s.pop("shot", None)
        if typ:
            shot_list.append({"id": s["clip"], "type": typ, "need": float(shots[typ]), "rec": pre, "n": s["n"]})
        pre += s["n"]
    joins = [it["join"]["class"] for it in items if it.get("join")]
    stats["joins"] = {c: joins.count(c) for c in CLASSES if joins.count(c)}
    # join pieces that continue each other in the source (back-to-back clips, nothing removed between)
    locked = {c["id"] for c in clips if c.get("locked")}
    exact = locked | {c["id"] for c in clips if c.get("pinned")}
    merged = []
    for s in segs:
        p = merged[-1] if merged else None
        if (p and p["src"] == s["src"] and p.get("gain_db") == s.get("gain_db") and "tone" not in p
                and "tone" not in s and p["clip"] not in exact and s["clip"] not in exact
                and p.get("stack") == s.get("stack")
                and p["in"] <= s["in"] <= p["in"] + (p["n"] + 1) / F):
            end = max(p["in"] + p["n"] / F, s["in"] + s["n"] / F)
            p["n"] = round((end - p["in"]) * F)
            if p["kept"] and not s["kept"]:
                p["picture_tail"] = True  # the take goes on past the words: QA judges its end by sound, not words
            if s["kept"] and not p["kept"]:
                p["picture_head"] = True  # and before them
            p["kept"] += s["kept"]
            if "fix" in s:
                p["fix"] = {**p.get("fix", {}), **s["fix"]}
            if "room" in p and "room" in s:
                p["room"][1] = s["room"][1]
            p.setdefault("also", []).append(s["clip"])
            if "words" in s:
                p["words"] = [p.get("words", s["words"])[0], s["words"][1]]
        else:
            merged.append(dict(s))
    rec = 0
    for k, s in enumerate(merged):
        s["id"], s["rec"] = k + 1, rec
        rec += s["n"]
    warnings += _validate(merged, sources, F)
    overlays = _overlays(edit, merged, sources, F, rec, warnings, shots)
    if shots and shots.get("min_visible"):
        _fill(merged, overlays, sources, F, rec, round(float(shots["min_visible"]) * F), warnings)
    texts = _texts(edit, merged, sources, F, rec, texts, warnings)
    cover = sorted((o["rec"], o["rec"] + o["n"]) for o in overlays)
    for s in shot_list:
        s["visible"] = _visible(s["rec"], s["rec"] + s["n"], cover)
    shot_list += [{"id": o["id"], "type": o["shot"], "need": float(shots[o["shot"]]), "rec": o["rec"], "n": o["n"],
                   "visible": o["n"]} for o in overlays if "shot" in o]
    sections = []
    for s in merged:
        if s["sec"] and (not sections or sections[-1]["id"] != s["sec"]):
            title = next((x.get("title", "") for x in edit.get("sections", []) if x["id"] == s["sec"]), "")
            sections.append({"id": s["sec"], "title": title, "rec": s["rec"], "n": 0})
        if sections and sections[-1]["id"] == s["sec"]:
            sections[-1]["n"] += s["n"]
    # told out of order: a section that starts earlier in the take than the one before it (said in review.md)
    first = {}
    for s in merged:
        first.setdefault(s["sec"], s)
    reorders = [{"sec": b["id"], "after": a["id"], "src_t": round(first[b["id"]]["in"], 1), "prev_t": round(first[a["id"]]["in"], 1)}
                for a, b in zip(sections, sections[1:]) if first[b["id"]]["src"] == first[a["id"]]["src"]
                and first[b["id"]]["in"] < first[a["id"]]["in"] - 30]
    return {"fps": str(F), "size": size, "frames": rec, "segments": merged, "overlays": overlays, "texts": texts,
            "shots": sorted(shot_list, key=lambda s: s["rec"]), "sections": sections, "reorders": reorders,
            "justified": {**{c["id"]: c["abrupt"] for c in clips if c.get("abrupt")},
                          **{c["id"]: "your cut in Resolve" for c in clips if c.get("locked")}},
            "locked": sorted(locked | {x["id"] for x in overlays + texts if x.get("locked")}),
            **({"captions": edit["captions"]} if edit.get("captions") else {}),
            "markers": _markers(edit, merged, sources, F),
            "stats": {**stats, "segments": len(merged), "seconds": round(float(rec / F), 2)}, "warnings": warnings}


def _validate(segs: list[dict], sources: dict, F: Fraction) -> list[str]:
    """Invariants: every kept word fully inside its piece; a removed word (filler, cue, cut) overlaps a piece
    by at most one frame of rounding (warned above 30 ms)."""
    warnings = []
    for s in segs:
        if not s["kept"] or s.get("exact"):  # frames from Resolve are your call, word edges or not
            continue
        W = sources[s["src"]]["words"]
        a, e = s["in"], s["in"] + s["n"] / F
        kept = set(s["kept"])
        for w in W:
            overlap = min(e, w["e"]) - max(a, w["s"])
            if w["i"] in kept:
                if w["s"] < a - 1e-6 or w["e"] > e + 1e-6:
                    raise EditError(f"piece {s['id']} ({s['clip']}) clips kept word {w['i']} '{w['t']}'")
            elif overlap > 1 / F + 1e-6:
                raise EditError(f"piece {s['id']} ({s['clip']}) includes removed word {w['i']} '{w['t']}'")
            elif overlap > 0.03:
                warnings.append(f"piece {s['id']} ({s['clip']}) touches removed word {w['i']} '{w['t']}' "
                                f"by {overlap * 1000:.0f} ms (frame rounding)")
    return warnings


def _anchor(oid: str, ov: dict, segs: list[dict], sources: dict, F: Fraction) -> int:
    """Timeline frame where an overlay or text starts: a kept word of a clip, or "at" seconds from its start."""
    pieces = [s for s in segs if ov.get("over") in [s["clip"], *s.get("also", [])]]
    if not pieces:
        raise EditError(f"{oid}: 'over' must name a clip in the cut, got {ov.get('over')!r}")
    if "word" in ov:
        s = next((s for s in pieces if ov["word"] in s["kept"]), None)
        if s is None:
            raise EditError(f"{oid}: word {ov['word']} is not kept in clip {ov['over']}")
        w = sources[s["src"]]["words"][ov["word"]]
        return s["rec"] + round((w["s"] - s["in"]) * F)
    return pieces[0]["rec"] + round(float(ov.get("at", 0)) * F)


def _texts(edit: dict, segs: list[dict], sources: dict, F: Fraction, total: int, tx: dict | None = None,
           warnings: list[str] | None = None) -> list[dict]:
    """On-screen text: a story line ("txt") or a place/price callout ("hi"); one of each style at a time.
    tx (the profile's [texts]): every text stays up its reading time (see reading_time), crossing shot changes if
    it must; a neighbour of the same style waits for it, and the last ones pull back to end with the cut."""
    warnings = warnings if warnings is not None else []
    out = []
    for t in edit.get("texts", []):
        tid = t.get("id") or "text"
        if not str(t.get("text", "")).strip():
            raise EditError(f"{tid}: empty text")
        if t.get("style", "txt") not in STYLES:
            raise EditError(f"{tid}: style must be one of {', '.join(STYLES)}, got {t['style']!r}")
        rec = _anchor(tid, t, segs, sources, F)
        need = round(reading_time(t["text"], tx) * F) if tx is not None else 0
        n0 = round(float(t["dur"]) * F) if "dur" in t else None  # no dur: its reading time decides
        if n0 is not None and (n0 < 1 or rec + n0 > total):
            raise EditError(f"{tid}: runs {float((rec + n0 - total) / F):.2f}s past the end of the cut")
        if rec >= total:
            raise EditError(f"{tid}: starts after the end of the cut")
        base = n0 if n0 is not None else max(need, round(1.5 * F))
        n = min(max(base, need), round(float(tx.get("max_dur", 4.5)) * F)) if tx is not None and not (t.get("locked") or t.get("pinned")) else base
        if n0 is not None and n > n0:
            warnings.append(f"{tid}: on screen {float(n / F):.1f}s (was {float(n0 / F):.1f}s), its reading time")
        out.append({"id": tid, "text": t["text"], "style": t.get("style", "txt"), "rec": rec, "n": n, "n0": n0 or 1,
                    **{k: t[k] for k in ("locked", "pinned", "place", "look") if k in t}})
    out.sort(key=lambda x: x["rec"])
    for st in STYLES:
        same = [x for x in out if x["style"] == st]
        for a, b in zip(same, same[1:]):
            if b["rec"] < a["rec"] + a["n0"]:
                raise EditError(f"{a['id']} and {b['id']} overlap; one '{st}' text at a time")
            if b["rec"] < a["rec"] + a["n"] and not (b.get("locked") or b.get("pinned")):
                warnings.append(f"{b['id']} starts {float((a['rec'] + a['n'] - b['rec']) / F):.1f}s later so "
                                f"{a['id']} can be read")
                b["rec"] = a["rec"] + a["n"]
        end = total
        for x in reversed(same):
            if x["rec"] + x["n"] > end and not (x.get("locked") or x.get("pinned")):
                warnings.append(f"{x['id']} starts {float((x['rec'] + x['n'] - end) / F):.1f}s earlier to be read "
                                "before what follows ends")
                x["rec"] = end - x["n"]
            end = x["rec"]
        if same and same[0]["rec"] < 0:
            raise EditError(f"the '{st}' texts don't fit in the cut at reading speed: shorten their words")
    for x in out:
        del x["n0"]
    return out


def _visible(a: int, b: int, cover: list[tuple[int, int]]) -> int:
    """Longest stretch of [a, b) not under B-roll (cover: sorted, non-overlapping)."""
    best, cur = 0, a
    for x, y in cover:
        if y <= cur:
            continue
        if x >= b:
            break
        best, cur = max(best, x - cur), y
    return max(best, b - cur)


def _overlays(edit: dict, segs: list[dict], sources: dict, F: Fraction, total: int,
              warnings: list[str] | None = None, shots: dict | None = None) -> list[dict]:
    """B-roll over narration: picture from another source, dialogue keeps playing underneath.
    Anchored to a kept word of a clip ("starts as I say 'ledger'"), or to an offset from the clip's start.
    shots (the profile's [shots]): each one held its type's least time, later into its source first, then earlier."""
    warnings = warnings if warnings is not None else []
    out = []
    for ov in edit.get("overlays", []):
        oid = ov.get("id") or "overlay"
        if ov.get("src") not in sources:
            raise EditError(f"{oid}: unknown source {ov.get('src')}")
        src = sources[ov["src"]]
        i, o = float(ov["in"]), float(ov["out"])
        if not (0 <= i < o <= src["dur"] + 1e-3):
            raise EditError(f"{oid}: {i}-{o} outside source {ov['src']} (0-{src['dur']})")
        rec = _anchor(oid, ov, segs, sources, F)
        n = round((o - i) * F)
        if rec + n > total:
            raise EditError(f"{oid}: runs {float((rec + n - total) / F):.2f}s past the end of the cut")
        out.append({"id": oid, "src": ov["src"], "in": round(i, 3), "n": n, "rec": rec,
                    "gain_db": ov.get("gain_db"), "over": ov["over"], **{k: ov[k] for k in ("crop", "blur") if ov.get(k)},
                    **({"shot": shot_type(ov, src)} if shots else {}),
                    **{k: ov[k] for k in ("locked", "pinned") if ov.get(k)}})
    out.sort(key=lambda x: x["rec"])
    for a, b in zip(out, out[1:]):
        over = a["rec"] + a["n"] - b["rec"]
        if 0 < over <= round(0.3 * F) and a["n"] - over >= 1:
            # chained shots pulled together by breathing room changing the words in between: end the first one
            # where the next begins instead of rejecting the edit
            a["n"] -= over
            warnings.append(f"{a['id']} ends {float(over / F):.2f}s early: it ran into {b['id']}")
        elif over > 0:
            raise EditError(f"{a['id']} and {b['id']} overlap; one B-roll layer at a time")
    flash = round(0.3 * F)
    for k, a in enumerate(out if shots else []):
        if a.get("locked") or a.get("pinned"):
            continue
        src, typ, n0 = sources[a["src"]], a["shot"], a["n"]
        nxt = out[k + 1]["rec"] if k + 1 < len(out) else total
        prv = out[k - 1]["rec"] + out[k - 1]["n"] if k else 0
        left = lambda: math.floor((float(src["dur"]) - a["in"]) * F + 1e-6) - a["n"]  # source frames after it
        want = max(n0, round(float(shots[typ]) * F))
        if typ in ("action", "reaction"):
            end = a["in"] + n0 / F
            s = settle(src, end, min(float(src["dur"]), end + float(shots.get("settle_max", 1.5))), a["in"])
            if s is None:
                warnings.append(f"{a['id']}: the {typ} is still moving past its out-point: let it finish")
            want = max(want, round(((s or end) - a["in"]) * F))
        later = max(0, min(want - a["n"], nxt - a["rec"] - a["n"], left()))
        a["n"] += later
        earlier = max(0, min(want - a["n"], a["rec"] - prv, math.floor(a["in"] * F + 1e-6)))
        a["rec"], a["in"], a["n"] = a["rec"] - earlier, round(a["in"] - earlier / F, 3), a["n"] + earlier
        gap = nxt - a["rec"] - a["n"]
        if a["n"] > n0 and k + 1 < len(out) and 0 < gap <= flash and gap <= left():
            a["n"] += gap  # held into the next shot rather than flashing the talking head between them
        if a["n"] > n0:
            warnings.append(f"{a['id']}: {typ} shot held {float(a['n'] / F):.1f}s (was {float(n0 / F):.1f}s)")
        if a["n"] < round(float(shots[typ]) * F):
            why = f"{out[k + 1]['id']} follows" if k + 1 < len(out) and a["rec"] + a["n"] >= nxt else "the source ends"
            warnings.append(f"{a['id']}: a {typ} shot wants {shots[typ]}s on screen, it has {float(a['n'] / F):.1f}s "
                            f"({why}): give it room or pick a longer stretch")
    for a, b in zip(out, out[1:] if not (shots and shots.get("min_visible")) else []):  # else _fill covers it
        over = a["rec"] + a["n"] - b["rec"]
        if -flash <= over < 0:
            warnings.append(f"{float(-over / F):.2f}s of A-roll flashes between {a['id']} and {b['id']}: "
                            "chain them or leave a real beat")
    return out


def _markers(edit: dict, segs: list[dict], sources: dict, F: Fraction) -> list[dict]:
    out = []
    clip_rec = {}
    for s in segs:
        for cid in [s["clip"], *s.get("also", [])]:
            clip_rec.setdefault(cid, s["rec"])
    for m in edit.get("markers", []):
        if m.get("at") in clip_rec:
            out.append({"rec": clip_rec[m["at"]], "kind": m.get("kind", "note"), "note": m.get("note", "")})
    for sid, src in sources.items():
        for c in src.get("cues", []):
            t = src["words"][c["cue"][0]]["s"]
            if c["type"] == "note":
                before = [s for s in segs if s["src"] == sid and s["in"] <= t]
                if before:
                    s = max(before, key=lambda x: x["in"])
                    out.append({"rec": s["rec"] + s["n"], "kind": "note", "note": c.get("text", "")})
            elif c["type"] == "mark" and "keep" in c:
                k = c["keep"][0]
                hit = next((s for s in segs if s["src"] == sid and k in s["kept"]), None)
                if hit:
                    w = src["words"][k]
                    out.append({"rec": hit["rec"] + round((w["s"] - hit["in"]) * F), "kind": "highlight",
                                "note": " ".join(x["t"] for x in src["words"][c["keep"][0]:c["keep"][1] + 1])})
    return sorted(out, key=lambda m: m["rec"])
