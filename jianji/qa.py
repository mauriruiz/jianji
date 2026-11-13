"""Automatic checks on every render. Each check: pass / warn / fail, with evidence and output timecodes."""
from __future__ import annotations

import difflib
import re
from fractions import Fraction
from pathlib import Path

import numpy as np

from .compile import reading_time
from .core import FFMPEG, run
from .media import SR, decode, envelope, ffprobe
from .transcribe import FILLERS, Engine, norm, transcribe_audio


def tc(t: float, F: Fraction) -> str:
    f = round(t * F)
    fps = round(F)
    return f"{f // (3600 * fps):02d}:{f // (60 * fps) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def _xcorr_lag(ref: np.ndarray, sig: np.ndarray) -> int:
    """Lag (samples) at which ref best matches inside sig."""
    n = len(ref) + len(sig)
    c = np.fft.irfft(np.fft.rfft(sig, n) * np.conj(np.fft.rfft(ref, n)), n)
    return int(np.argmax(c[:len(sig) - len(ref) + 1]))


def parse_srt(path: Path) -> list[tuple[float, float, str]]:
    def secs(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000
    cues = []
    for block in path.read_text().strip().split("\n\n"):
        lines = block.splitlines()
        if len(lines) >= 3:
            a, b = lines[1].split(" --> ")
            cues.append((secs(a), secs(b), " ".join(lines[2:])))
    return cues


HEAVY = ("clipped_words", "asr_diff", "fragments")  # depend only on the master's sound: reusable when only captions changed


def text_boxes(ass: Path, size: list[int]) -> list[tuple[str, float, float, tuple, str]]:
    """Render each burned text event alone (captions, story texts and callouts in separate passes, so libass
    neither merges nor shoves them) and measure its box: (style, start, end, (x0, y0, x1, y1) px, text)."""
    W, H = size
    s = 0.5
    cw, ch = round(W * s / 2) * 2, round(H * s / 2) * 2
    head, events = [], {}
    for line in ass.read_text().splitlines():
        if line.startswith("Dialogue:"):
            events.setdefault(line.split(",")[3], []).append(line)
        else:
            head.append(line)
    out = []
    for style, lines in events.items():
        one = ass.with_name(f"_zone_{style}.ass")
        one.write_text("\n".join(head + lines) + "\n")
        for line in lines:
            f = line.split(",", 9)
            a, b = (sum(float(x) * m for x, m in zip(t.split(":"), (3600, 60, 1))) for t in (f[1], f[2]))
            t = (a + b) / 2
            raw = run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=black:s={cw}x{ch}:r=10:d={t + 0.3:.2f}",
                       "-vf", f"subtitles={one.name}", "-ss", f"{t:.2f}", "-frames:v", "1", "-f", "rawvideo",
                       "-pix_fmt", "gray", "-"], cwd=ass.parent).stdout
            img = np.frombuffer(raw, np.uint8)[:cw * ch].reshape(ch, cw)
            ys, xs = np.nonzero(img > 60)
            if len(xs):
                box = (xs.min() / s, ys.min() / s, (xs.max() + 1) / s, (ys.max() + 1) / s)
                out.append((style, a, b, box, re.sub(r"\{[^}]*\}", "", f[9]).replace("\\N", " ").replace("\\h", " ")))
        one.unlink()
    return out


def text_over_faces(boxes: list[tuple], frames: list[dict], size) -> tuple[list, list]:
    """Burned text boxes that cover a face for more than 10% of their time on screen, and those covering the
    subject of a face-less shot (the dish) for more than 30%. Measured on the real libass boxes."""
    from .place import _hits
    W, H = size
    face, subj = [], []
    for bx in boxes:
        f, s = _hits(frames, bx[3], bx[1], bx[2], W, H)
        if f > 0.10:
            face.append((bx, f))
        elif s > 0.30:
            subj.append((bx, s))
    return face, subj


def overlaps(boxes: list[tuple]) -> list[tuple[float, str, str]]:
    """Text boxes of different styles on screen at the same time and touching: (time, text, other text)."""
    out = []
    for i, (s1, a1, b1, r1, t1) in enumerate(boxes):
        for s2, a2, b2, r2, t2 in boxes[i + 1:]:
            if s1 != s2 and a1 < b2 and a2 < b1 and r1[0] < r2[2] and r2[0] < r1[2] and r1[1] < r2[3] and r2[1] < r1[3]:
                out.append((max(a1, a2), t1, t2))
    return out


def safe_zone(boxes: list[tuple], size: list[int], zone: dict) -> list[tuple[float, str, list[str], int]]:
    """Every burned text box against the keep-clear margins: (time, text, sides crossed, worst overshoot px)."""
    W, H = size
    k = H / zone["size"][1]
    lim = (zone["left"] * k, zone["top"] * k, W - zone["right"] * k, H - zone["bottom"] * k)
    out = []
    for _, a, b, box, text in boxes:
        over = {"left": lim[0] - box[0], "top": lim[1] - box[1], "right": box[2] - lim[2], "bottom": box[3] - lim[3]}
        crossed = [side for side, px in over.items() if px > 0]
        if crossed:
            out.append(((a + b) / 2, text, crossed, round(max(over.values()))))
    return out


def run_qa(tl: dict, sources: dict, outs: dict, eng: Engine, cap_cfg: dict, render_cfg: dict,
           zone: dict | None = None, reuse: dict | None = None, cut: dict | None = None,
           vision: list[dict] | None = None, texts: dict | None = None, shots: dict | None = None,
           ocr: list[dict] | None = None, never: list[str] | None = None, allow: list[str] | None = None,
           hook: dict | None = None, length: dict | None = None) -> dict:
    """reuse: a previous QA result for a master with the same sound; its HEAVY checks are carried over.
    cut: the profile's [cut], for the breathing check. vision: faces/subject from vision.detect on the master.
    texts: the profile's [texts], for reading time. shots: its [shots], for least holds. hook: its [hook], for how
    the cut opens. length: your [shorts] (a short cut out of a long video), for how long it may run."""
    F = Fraction(tl["fps"])
    frame = float(1 / F)
    segs = tl["segments"]
    checks, issues = [], []
    cur = [None]  # the check whose issues are being reported

    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})

    def issue(t, severity, msg):
        seg = next((s for s in segs if s["rec"] <= t * F < s["rec"] + s["n"]), segs[-1])
        issues.append({"tc": tc(t, F), "t": round(t, 2), "piece": seg["id"], "clip": seg["clip"], "sec": seg["sec"],
                       "severity": severity, "msg": msg, "check": cur[0]})

    master = Path(outs["master"])
    info = ffprobe(master)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    vdur, adur = float(v["duration"]), float(a["duration"])
    frames = int(v.get("nb_frames") or round(vdur * F))
    ok = abs(frames - tl["frames"]) <= 1 and abs(adur - vdur) <= frame
    check("duration", "pass" if ok else "fail",
          f"video {frames} frames (plan {tl['frames']}), audio {adur:.3f}s vs video {vdur:.3f}s")
    outs_v = [("master", v)]
    if "final" in outs:
        outs_v.append(("final", next(x for x in ffprobe(Path(outs["final"]))["streams"] if x["codec_type"] == "video")))
    bad_size = [f"{n} {x['width']}x{x['height']}" for n, x in outs_v if [x["width"], x["height"]] != list(tl["size"])]
    check("size", "fail" if bad_size else "pass",
          f"{', '.join(bad_size)} vs plan {tl['size'][0]}x{tl['size'][1]}" if bad_size else
          " + ".join(n for n, _ in outs_v) + f" {tl['size'][0]}x{tl['size'][1]}")
    off = float(a.get("start_time", 0)) - float(v.get("start_time", 0))
    check("av_offset", "pass" if abs(off) <= frame / 2 else "fail", f"audio starts {off * 1000:+.0f} ms vs video")

    # content sync: find each long piece's source audio inside the master where the plan says it is
    heard = decode(master)[:, 0]
    lags = []
    for s in sorted(segs, key=lambda x: -x["n"])[:3]:
        src = sources[s["src"]]
        if "audio" not in src or s["n"] / F < 1.2:
            continue
        ln = min(3.0, float(s["n"] / F) - 0.6)
        ref = src["audio"][int((s["in"] + 0.3) * SR):int((s["in"] + 0.3 + ln) * SR)]
        pos = float(s["rec"] / F) + 0.3
        lo = max(0, int((pos - 0.25) * SR))
        win = heard[lo:int((pos + ln + 0.25) * SR)]
        if len(win) > len(ref) > SR // 2 and np.std(ref) > 1e-4:
            lags.append(((lo + _xcorr_lag(ref, win)) / SR - pos, s))
    worst = max(lags, key=lambda x: abs(x[0]), default=None)
    if worst:
        st = "pass" if abs(worst[0]) <= 0.01 else "warn" if abs(worst[0]) <= frame else "fail"
        check("av_sync", st, f"max drift {worst[0] * 1000:+.0f} ms over {len(lags)} pieces")
        if st == "fail":
            issue(float(worst[1]["rec"] / F), "fail", f"audio {worst[0] * 1000:+.0f} ms off its picture")

    # loudness
    out = run([FFMPEG, "-hide_banner", "-nostats", "-i", master, "-af", "ebur128=peak=true", "-f", "null", "-"]).stderr.decode()
    summ = out[out.rfind("Summary:"):]
    i_lufs = float(re.search(r"I:\s+(-?[\d.]+) LUFS", summ).group(1))
    peak = float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summ).group(1))
    ok = abs(i_lufs - render_cfg["loudness"]) <= 1.0 and peak <= render_cfg["true_peak"] + 0.5
    check("loudness", "pass" if ok else "warn", f"{i_lufs} LUFS integrated, true peak {peak} dBFS "
          f"(target {render_cfg['loudness']}, {render_cfg['true_peak']})")

    # black / frozen picture
    out = run([FFMPEG, "-hide_banner", "-nostats", "-i", master, "-an", "-vf",
               "scale=320:-2,blackdetect=d=0.5:pix_th=0.10,freezedetect=n=-60dB:d=3", "-f", "null", "-"]).stderr.decode()
    blacks = [float(x) for x in re.findall(r"black_start:([\d.]+)", out)]
    freezes = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", out)]
    for t in blacks:
        issue(t, "warn", "black picture >= 0.5 s")
    for t in freezes:
        issue(t, "warn", "frozen picture >= 3 s")
    check("picture", "pass" if not blacks and not freezes else "warn", f"{len(blacks)} black, {len(freezes)} frozen")

    # captions: overlaps and overruns are bugs; "hard to read" is a property of fast speech, reported in aggregate
    cues = parse_srt(Path(outs["srt"]))
    bad, hard_cues = 0, []
    for (a0, b0, t), nxt in zip(cues, cues[1:] + [None]):
        if nxt and b0 > nxt[0] + 1e-3:
            issue(a0, "fail", f"caption overlaps the next: {t!r}")
            bad += 1
        if b0 > vdur + 0.05:
            issue(a0, "fail", f"caption runs past the end: {t!r}")
            bad += 1
        cps = len(t) / max(b0 - a0, 0.1)
        if b0 - a0 < 0.7 or len(t) > cap_cfg["max_chars"] + 8 or cps > 22:
            hard_cues.append((cps, a0, b0, t))
    hard_cues.sort(reverse=True)
    for cps, a0, b0, t in hard_cues[:5]:
        issue(a0, "warn", f"caption hard to read ({b0 - a0:.1f}s, {len(t)} chars): {t!r}")
    many = len(hard_cues) > max(3, 0.1 * len(cues))
    check("captions", "fail" if bad else "warn" if many else "pass",
          f"{len(cues)} cues, {len(hard_cues)} fast to read" + (" (fast speech; worst 5 listed)" if hard_cues else ""))
    cur[0] = "edges"
    _edges(tl, sources, issue, check)

    if cut and "gap_sentence" in cut:  # every speech cut on a clause end, with breathing room measured on the render
        cur[0] = "speech_cuts"
        js = breathing(tl, sources, heard, cut)
        mids = mid_sentence(tl, sources, cut)
        tight = [j for j in js if j["tight"] and j["class"] != "intra"]
        for j in tight:
            if not j["why"]:
                issue(j["cut"], "fail", f"cut without breathing room: {j['class']} gap {j['quiet']:.2f}s < "
                      f"{j['min']:.2f}s ({j['last']!r} | {j['first']!r})")
        for m in mids:
            if not m["why"]:
                issue(m["t"], "fail", f"speech cut mid-sentence after {m['word']!r} ({m['clip']}): end it on a clause "
                      "or sentence end, or mark the clip \"abrupt\" with why")
        bad = [x for x in tight + mids if not x["why"]]
        med = {c: float(np.median([j["quiet"] for j in js if j["class"] == c])) for c in ("sentence", "section")
               if any(j["class"] == c for j in js)}
        check("speech_cuts", "fail" if bad else "pass",
              f"{len(js)} cuts between speech: {sum(not m['why'] for m in mids)} end mid-sentence, "

              f"{sum(not j['why'] for j in tight)} without breathing room, "
              f"{len({x['why'] for x in tight + mids if x['why']})} kept tight on purpose (abrupt); median quiet "
              + ", ".join(f"{c} {v:.2f}s" for c, v in med.items()))
    if length:  # a short out of a long video: as long as you want them, never past your limits
        cur[0] = "length"
        from .compile import judge_length
        secs = float(tl["frames"] / F)
        off = judge_length(secs, length)
        if off:
            issue(0.0, off[0], off[1])
        check("length", off[0] if off else "pass", f"{secs:.1f} s" + (f" (you aim for {length['length'][0]:g}-{length['length'][1]:g} s)"
                                                                   if length.get("length") else ""))
    if hook:  # the first seconds: on the line itself, said in one go
        cur[0] = "hook"
        from .compile import opening
        found, how = opening(tl, sources, hook)
        for sev, t, msg in found:
            issue(t, sev, msg)
        check("hook", "fail" if any(x[0] == "fail" for x in found) else "warn" if found else "pass", how)
    if shots and shots.get("min_visible"):  # nothing on screen for a jarring fraction of a second
        cur[0] = "short_segments"
        from .compile import picture_runs
        mv = round(float(shots["min_visible"]) * F)
        flashes = [r for r in picture_runs(segs, tl.get("overlays", []), F) if r["end"] - r["start"] < mv - 1
                   and r["id"] not in tl.get("locked", [])]
        for r in flashes:
            issue(float(r["start"] / F), "fail", f"{'B-roll ' if r['kind'] == 'overlay' else ''}{r['id']} on screen "
                  f"{float((r['end'] - r['start']) / F):.2f}s, under {shots['min_visible']}s")
        check("short_segments", "fail" if flashes else "pass",
              f"{len(flashes)} pictures on screen under {shots['min_visible']}s (talking head included)")

    # burned text inside the platforms' safe zone (vertical profiles)
    boxes = text_boxes(Path(outs["final_ass"]), tl["size"]) if outs.get("final_ass") else []
    if zone and outs.get("final_ass"):
        cur[0] = "safe_zone"
        bad = safe_zone(boxes, tl["size"], zone)
        for t, text, sides, px in bad:
            issue(t, "fail" if px > 8 else "warn", f"text crosses the safe zone ({', '.join(sides)}, {px} px): {text!r}")
        worst = max((px for *_, px in bad), default=0)
        check("safe_zone", "fail" if worst > 8 else "warn" if bad else "pass",
              f"{len(bad)} text boxes outside the zone (keep clear: top {zone['top']}, bottom {zone['bottom']}, "
              f"left {zone['left']}, right {zone['right']} px at {zone['size'][0]}x{zone['size'][1]})")
        cur[0] = "text_overlap"
        clash = overlaps(boxes)
        for t, t1, t2 in clash:
            issue(t, "warn", f"on-screen text overlaps another: {t1!r} / {t2!r}")
        check("text_overlap", "warn" if clash else "pass", f"{len(clash)} overlapping text boxes")
    if tl.get("texts"):  # every story line and callout on screen long enough to read, as burned
        cur[0] = "text_readable"
        tx = texts or {}
        hand = {" ".join(t["text"].split()) for t in tl["texts"] if t.get("locked")}  # your timing: listed, not judged
        shown = [(a, b, t) for st, a, b, _, t in boxes if st in ("Txt", "Hi")] or \
                [(float(t["rec"] / F), float((t["rec"] + t["n"]) / F), t["text"]) for t in tl["texts"]]
        shown = [x for x in shown if " ".join(x[2].split()) not in hand]
        short = fast = 0
        for a, b, t in shown:
            need, chars = reading_time(t, tx), len(" ".join(t.split()))
            if b - a < need - frame - 0.01:
                short += 1
                issue(a, "fail", f"text up {b - a:.1f}s, needs {need:.1f}s to be read ({chars} chars): {t!r}")
            elif chars / (b - a) > float(tx.get("read_cps", 13)):
                fast += 1
                issue(a, "warn", f"text reads at {chars / (b - a):.0f} chars/s in {b - a:.1f}s: shorten the words: {t!r}")
        check("text_readable", "fail" if short else "warn" if fast else "pass",
              f"{len(shown)} texts, {short} below their reading time, {fast} faster than "
              f"{tx.get('read_cps', 13)} chars/s")
    if tl.get("shots"):  # every picture shot held long enough to register
        cur[0] = "shot_readable"
        brief = [x for x in tl["shots"] if 0 < x["visible"] < round(x["need"] * F) - 1]
        for x in brief:
            issue(float(x["rec"] / F), "warn", f"{x['type']} shot {x['id']} on screen {float(x['visible'] / F):.1f}s, "
                  f"wants {x['need']:.1f}s")
        check("shot_readable", "warn" if brief else "pass",
              f"{len(brief)} of {sum(x['visible'] > 0 for x in tl['shots'])} picture shots shorter than their type's hold")
    if ocr is not None:  # what the cut shows and says that shouldn't be public (streams: secrets, never_show)
        cur[0] = "privacy"
        from .stream import private
        hits = [(f["t"], *x) for f in ocr for x in private(f["text"], never or [], allow or [])]
        for t, kind, text, box in hits:
            issue(t, "fail", f"{kind} on screen: {text!r}: blur it, \"blur\": [[{', '.join(f'{v:.2f}' for v in box[:4])}]] "
                  "on the clip or B-roll under it (frame coordinates)")
        said = []
        for s in segs:
            text = " ".join(sources[s["src"]]["words"][k]["t"] for k in s["kept"])
            said += [(float(s["rec"] / F), n) for n in never or [] if n and n.lower() in text.lower()]
        for t, n in said:
            issue(t, "fail", f"never_show '{n}' said in piece at {t:.1f}s: cut it")
        check("privacy", "fail" if hits or said else "pass",
              f"{len(hits)} things on screen and {len(said)} said that may be private (OCR every "
              f"{ocr[1]['t'] - ocr[0]['t'] if len(ocr) > 1 else 0:.0f} s; never_show: {len(never or [])})")
    if vision is not None and outs.get("final_ass"):
        cur[0] = "text_faces"
        on_face, on_subject = text_over_faces(boxes, vision, tl["size"])
        for (style, a, b, box, text), share in on_face:
            issue(a, "fail", f"{'caption' if style.startswith('Cap') else 'text'} over a face {share:.0%} of its "
                  f"{b - a:.1f}s: {text!r}")
        for (style, a, b, box, text), share in on_subject[:5]:
            issue(a, "warn", f"text over the dish/subject {share:.0%} of its time: {text!r}")
        check("text_faces", "fail" if on_face else "warn" if on_subject else "pass",
              f"{len(on_face)} of {len(boxes)} burned texts over a face >10% of their time, "
              f"{len(on_subject)} over the subject")

    if reuse and all(any(c["name"] == h for c in reuse.get("checks", [])) for h in HEAVY) \
            and all("check" in i for i in reuse.get("issues", [])):
        checks += [c for c in reuse["checks"] if c["name"] in HEAVY]
        issues += [i for i in reuse["issues"] if i["check"] in HEAVY]
        return _finish(checks, issues, tl)

    # clipped words, primary signal: force-align the words we meant to keep against the render's own audio.
    # Only words near a cut edge are judged; a fail needs a low score that is also low against the same word's
    # score in the source (crosstalk words align badly everywhere) AND speech-level sound right at the edge.
    cur[0] = "clipped_words"
    words = clipped_words(tl, sources, heard, eng)
    fails = [w for w in words if w["verdict"] == "fail"]
    lows = [w for w in words if w["verdict"] == "warn"]
    for w in fails:
        issue(w["at"], "fail", f"{w['t']!r} clipped at the {w['edge']} of piece #{w['piece']}: alignment "
              f"{w['score']:.2f} in the render vs {w['ref']:.2f} in the source, cut-away side {w['edge_db']:+.0f} dB vs the word")
    for w in lows[:10]:
        issue(w["at"], "warn", f"{w['t']!r} aligns worse in the render ({w['score']:.2f} vs {w['ref']:.2f}) near a cut, "
              f"but what was cut away is quieter than speech ({w['edge_db']:+.0f} dB) or the word is unclear anyway")
    judged = sum(w["judged"] for w in words)
    check("clipped_words", "fail" if fails else "warn" if lows else "pass",
          f"{len(words)} words aligned in context, {judged} near cuts judged, {len(fails)} clipped, {len(lows)} unsure")

    # secondary signal: re-transcribe and diff. Whisper mishears short words in noise and "hears" half-clipped
    # words from context, so this can only ever warn
    cur[0] = "asr_diff"
    diffs, extra = asr_diffs(tl, sources, heard, eng)
    for t, want, hear in diffs[:10]:
        issue(t, "warn", f"re-transcription near a cut: wanted {want!r}, heard {hear!r}")
    check("asr_diff", "warn" if diffs else "pass", f"{len(diffs)} differences near cuts (Whisper on the render)")

    # speech nobody kept, cut into: voice-level sound past the words AND a word Whisper hears there that the edit
    # doesn't have (a half-word Whisper never wrote). Either alone is a lid, a laugh or a mishearing.
    cur[0] = "fragments"
    frs = fragments(tl, sources, cut or {"tail_db": 6}, extra)
    for x in frs:
        issue(x["t"], "fail", f"piece {x['side']} through speech nobody kept, {'after' if x['side'] == 'ends' else 'before'}"
              f" {x['word']!r} ({x['clip']}): Whisper hears {x['heard']!r} there, {x['ms']} ms at voice level")
    check("fragments", "fail" if frs else "pass", f"{len(frs)} cuts through speech the edit didn't keep")
    return _finish(checks, issues, tl)


def _edges(tl: dict, sources: dict, issue, check):
    """Plan-level: a piece edge well inside a word's span is a clip by construction; a few ms is a fade leak."""
    F = Fraction(tl["fps"])
    in_word = leak = loud = 0
    for s in tl["segments"]:
        src = sources[s["src"]]
        if not s["kept"] or s["clip"] in tl.get("locked", []):  # your cut in Resolve: your call
            continue
        env = src.get("env")
        thr = float(np.percentile(env, 10) + 0.7 * (np.median(env) - np.percentile(env, 10))) if env is not None else 0
        for at, t_src in (("starts", s["in"]), ("ends", s["in"] + float(s["n"] / F))):
            t_out = float(s["rec"] / F) + (0 if at == "starts" else float(s["n"] / F))
            w = next((w for w in src["words"] if w["s"] < t_src < w["e"]), None)
            depth = min(t_src - w["s"], w["e"] - t_src) if w else 0
            k = int((t_src - (0 if at == "starts" else 0.03)) * 100)
            if w and depth >= 0.06:
                in_word += 1
                issue(t_out, "fail", f"piece #{s['id']} {at} {depth * 1000:.0f} ms inside the word {w['t']!r} "
                      f"(src {s['src']} {w['i']})")
            elif w and depth > 0.02:
                leak += 1
                issue(t_out, "warn", f"piece #{s['id']} {at} {depth * 1000:.0f} ms into the word {w['t']!r}")
            elif env is not None and 0 <= k < len(env) - 3 and env[k:k + 3].mean() > thr:
                loud += 1  # background or crosstalk under the edge: counted, not listed
    check("edges", "fail" if in_word else "warn" if leak else "pass",
          f"{in_word} edges inside words, {leak} touching a word, {loud} in background sound")


def _finish(checks: list[dict], issues: list[dict], tl: dict) -> dict:
    status = "fail" if any(c["status"] == "fail" for c in checks) else \
        "warn" if any(c["status"] == "warn" for c in checks) else "pass"
    return {"status": status, "summary": summary(status, checks), "checks": checks,
            "issues": sorted(issues, key=lambda x: x["t"])}


def joins(tl: dict, sources: dict) -> list[dict]:
    """Every cut between two speech pieces: its class (from compile, or worked out for timelines made before the
    pause hierarchy), and where the last word before it ends and the next word starts in the output."""
    from .compile import ends_sentence
    F = Fraction(tl["fps"])
    out, prev, between = [], None, False
    for s in tl["segments"]:
        if not s["kept"]:
            between = between or "tone" not in s  # a picture-only piece in between: not a speech gap
            continue
        if prev and not between:
            wp = sources[prev["src"]]["words"][prev["kept"][-1]]
            wn = sources[s["src"]]["words"][s["kept"][0]]
            cls = (s.get("join") or {}).get("class") or ("section" if s["sec"] != prev["sec"] else
                                                         "sentence" if ends_sentence(wp["t"]) else "intra")
            if cls in ("intra", "sentence", "section"):
                just = tl.get("justified", {})
                out.append({"piece": s["id"], "class": cls, "end": float(prev["rec"] / F) + wp["e"] - prev["in"],
                            "start": float(s["rec"] / F) + wn["s"] - s["in"], "cut": float(s["rec"] / F),
                            "last": wp["t"], "first": wn["t"],
                            "why": next((just[c] for c in (prev["clip"], *prev.get("also", [])) if c in just), None)})
        prev, between = s, False
    return out


def mid_sentence(tl: dict, sources: dict, cut: dict) -> list[dict]:
    """Speech that stops mid-sentence: a piece whose last word isn't a clause or sentence end with an audible
    pause after it (compile.is_boundary), where the next speech doesn't carry on from it."""
    from .compile import carries_on, is_boundary
    F = Fraction(tl["fps"])
    just = tl.get("justified", {})
    segs = tl["segments"]
    sp = [s for s in segs if s["kept"]]
    out = []
    for s, n in zip(sp, sp[1:] + [None]):
        src = sources[s["src"]]
        W, k = src["words"], s["kept"][-1]
        after = segs[segs.index(s) + 1] if s is not segs[-1] else None  # the same take going on as picture
        if (n and n["src"] == s["src"] and carries_on(W, k, n["kept"][0]) or is_boundary(src, k, cut)
                or after and not after["kept"] and after["src"] == s["src"] and -0.05 <= after["in"] - W[k]["e"] <= 1.0
                or s.get("picture_tail")):  # the take goes on as picture: through_speech judges that end
            continue
        clips = (s["clip"], *s.get("also", []))
        out.append({"t": round(float(s["rec"] / F) + W[k]["e"] - s["in"], 2), "word": W[k]["t"], "clip": s["clip"],
                    "why": next((just[c] for c in clips if c in just), None)})
    return out


def through_speech(tl: dict, sources: dict, cut: dict) -> list[dict]:
    """Speech pieces that may carry a fragment of speech nobody kept: 30 ms or more at the speaker's level (within 6 dB of
    the piece's words) between the last kept word's real end and the cut, or between the cut and the first word's
    real start. The transcript can't see it: words Whisper never wrote (a half-word after a cut).
    Where the take goes on as picture past the words (a laugh, a smile), the cut itself must not land in voice.
    Sound alone can't tell a word from a lid or a laugh: these are candidates, `fragments` confirms them."""
    from .compile import word_end, word_start
    F = Fraction(tl["fps"])
    out = []
    mine = set(tl.get("locked", []))  # your own cuts from Resolve: your call, not a defect
    for s in tl["segments"]:
        if not s["kept"] or "tone" in s or s["clip"] in mine:
            continue
        src = sources[s["src"]]
        W, env = src["words"], src["env"]
        voice = float(np.median(np.concatenate([env[int(W[k]["s"] * 100):int(W[k]["e"] * 100) + 1] for k in s["kept"]])))
        a, b = s["in"], s["in"] + float(s["n"] / F)
        first, last = W[s["kept"][0]], W[s["kept"][-1]]
        pt, ph = s.get("picture_tail"), s.get("picture_head")  # there the cut itself must not land in voice
        sides = (("ends", b - 0.05 if pt else word_end(src, last["e"], b, cut["tail_db"]), b + 0.03 if pt else b,
                  last["t"]),
                 ("starts", a - 0.03 if ph else a, a + 0.05 if ph else word_start(src, first["s"], a, cut["tail_db"]),
                  first["t"]))
        for side, lo, hi, word in sides:
            run = best = 0
            for v in env[max(int(lo * 100), 0):max(int(hi * 100), 0)]:
                run = run + 1 if v > voice - 6 else 0
                best = max(best, run)
            if best >= 3:
                t = float(s["rec"] / F) + (b - a if side == "ends" else 0)
                out.append({"t": round(t, 2), "clip": s["clip"], "side": side, "word": word, "ms": best * 10})
    return out


def breathing(tl: dict, sources: dict, heard: np.ndarray, cut: dict) -> list[dict]:
    """Measure, on the render's own audio, the quiet at every cut: from where the last word audibly decays to
    where the next audibly starts. Independent of the plan, so it also catches what a render does to it.
    Quiet is the voice being gone, as compile plans it: under the speaker's level by 10 dB (or the noise floor +
    tail_db, in a room quieter than that). On a stream the room never gets near the floor: music, keys, a noise gate
    that leaves the floor far under the room; measured against the floor alone, a real pause there counted as none."""
    from .compile import decay_end, gaps, onset_start
    env = envelope(heard)
    src = {"env": env, "floor": float(np.percentile(env, 10))}
    mids = [env[min(int((a + b) * 50), len(env) - 1)] for _, _, a, b in _pieces_words(tl, sources)]
    db = max(cut["tail_db"], float(np.median(mids)) - 10 - src["floor"]) if mids else cut["tail_db"]
    G = gaps(cut)
    out = []
    for j in joins(tl, sources):
        dec = decay_end(src, j["end"], j["start"], db)
        ons = onset_start(src, j["start"], dec, db)
        j["quiet"] = round(max(0.0, ons - dec), 3)
        j["min"] = G[j["class"]][0]
        j["tight"] = j["quiet"] < j["min"] - 0.03
        out.append(j)
    return out


def summary(status: str, checks: list[dict]) -> str:
    """One line for the top of review.md: "QA: pass (2 warnings: captions, asr_diff)"."""
    parts = []
    for level, word in (("fail", "failure"), ("warn", "warning")):
        names = [c["name"] for c in checks if c["status"] == level]
        if names:
            parts.append(f"{len(names)} {word}{'s' if len(names) > 1 else ''}: {', '.join(names)}")
    return f"QA: {status}" + (f" ({'; '.join(parts)})" if parts else "")


EDGE = 0.3  # only words this close to a cut edge are judged


def _pieces_words(tl: dict, sources: dict):
    """(piece, kept word, planned output start/end) for every spoken word in the cut."""
    F = Fraction(tl["fps"])
    for s in tl["segments"]:
        W = sources[s["src"]]["words"]
        off = float(s["rec"] / F) - s["in"]
        for k in s["kept"]:
            if not W[k].get("filler") and norm(W[k]["t"]):
                yield s, W[k], W[k]["s"] + off, W[k]["e"] + off


MARGIN = 0.15  # alignment window around each piece, same in source and render


def _align(eng: Engine, audio: np.ndarray, lang: str, windows: list[tuple[float, float, list[dict]]],
           model: str | None = None) -> list:
    """wav2vec2 score per word for each (start, end, words) window, in input order (None if unplaced)."""
    segs = [{"start": max(0.0, a), "end": b, "text": " ".join(w["t"] for w in ws)} for a, b, ws in windows]
    got = [x for seg in eng.align(segs, audio, lang, model) for x in seg.get("words", [])]
    want = [w for _, _, ws in windows for w in ws]
    sm = difflib.SequenceMatcher(a=[norm(w["t"]) for w in want], b=[norm(x["word"]) for x in got], autojunk=False)
    match = {i: j for blk in sm.get_matching_blocks() for i, j in zip(range(blk.a, blk.a + blk.size),
                                                                     range(blk.b, blk.b + blk.size))}
    return [got[match[i]].get("score") if i in match else None for i in range(len(want))]


def clipped_words(tl: dict, sources: dict, heard: np.ndarray, eng: Engine) -> list[dict]:
    """Force-align the intended words, in output order, against the render audio. Segments break at natural
    pauses, never at cuts, so every word is aligned in context, like at transcription time; its score then compares
    fairly with the source score `p`. Windows that ended at the cuts made edge words score low whether clipped or
    not (the same audio scored 0.04 in one window and 0.56 in another). A fail needs all of: near a cut edge,
    clearly lower than in the source, clear in the source, and speech on the side the cut removed."""
    F = Fraction(tl["fps"])
    items = list(_pieces_words(tl, sources))
    if not items:
        return []
    # scores only compare with the source's `p` from the same aligner: the one its transcript recorded (none recorded:
    # whispermlx's default, what transcripts were aligned with before)
    by = lambda it: (it[1].get("lang") or eng.langs[0], (sources[it[0]["src"]].get("align") or {}).get(it[1].get("lang")))
    runs, cur = [], []  # split where the output has a pause, or the language or the aligner changes
    for it in items:
        if cur and (it[2] - cur[-1][3] > 0.5 or by(it) != by(cur[-1])):
            runs.append(cur)
            cur = []
        cur.append(it)
    runs.append(cur)
    # a cut can only clip the word right next to it: the first kept word of a piece, or the last
    first = {s["id"]: min(s["kept"], key=lambda k: sources[s["src"]]["words"][k]["s"]) for s, *_ in items}
    last = {s["id"]: max(s["kept"], key=lambda k: sources[s["src"]]["words"][k]["e"]) for s, *_ in items}
    out = []
    pad = 0.5  # silence around the render, so the first and last words get lead-in frames like any other
    padded = np.concatenate([np.zeros(int(pad * SR), np.float32), heard, np.zeros(int(pad * SR), np.float32)])
    for lang, model in dict.fromkeys(by(r[0]) for r in runs):
        mine = [r for r in runs if by(r[0]) == (lang, model)]
        scores = _align(eng, padded, lang, [(r[0][2] - 0.1 + pad, r[-1][3] + 0.1 + pad, [w for _, w, _, _ in r])
                                            for r in mine], model)
        for (s, w, a, b), sc in zip([x for r in mine for x in r], scores):
            p0, p1 = float(s["rec"] / F), float((s["rec"] + s["n"]) / F)
            near_start = a - p0 <= EDGE and w["i"] == first[s["id"]]
            near_end = p1 - b <= EDGE and w["i"] == last[s["id"]]
            ref = w.get("p")
            rec = {"piece": s["id"], "k": w["i"], "t": w["t"], "at": round(a, 2),
                   "score": None if sc is None else round(float(sc), 2), "ref": ref,
                   "judged": bool((near_start or near_end) and sc is not None and ref is not None),
                   "verdict": "pass", "edge": "start" if near_start else "end", "edge_db": 0.0}
            if rec["judged"] and sc < 0.6 * ref and ref - sc > 0.2:
                src = sources[s["src"]]
                env = src["env"]
                lvl = float(np.median(env[int(w["s"] * 100):max(int(w["e"] * 100), int(w["s"] * 100) + 1)]))
                # the 150 ms the cut removed next to the word; 75th percentile so a plosive's silent closure
                # ("dis|t|ributed") doesn't hide the vowel before it
                t_src = s["in"] - 0.15 if near_start else s["in"] + float(s["n"] / F)
                removed = float(np.percentile(env[max(0, int(t_src * 100)):int(t_src * 100) + 15], 75))
                rec["edge_db"] = round(removed - lvl, 1)
                # the cut went through the word itself: the cut-away side is voice (VAD) at about the word's level.
                # Much louder is a different sound (market noise, a louder speaker); much quieter is room tone
                voiced = any(a0 < t_src + 0.15 and b0 > t_src for a0, b0, *_ in src.get("spans", []))
                # ...and not another word: removing "Bueno," right before "estas" is a cut between words
                other = any(x["i"] != w["i"] and x["s"] < t_src + 0.15 and x["e"] > t_src for x in src["words"])
                # ...and the cut isn't sitting in a dip between two sounds (a word, a 30 ms dip at the cut,
                # then crosstalk Whisper never transcribed)
                t_edge = s["in"] if near_start else s["in"] + float(s["n"] / F)
                dip = float(env[max(0, int((t_edge - 0.02) * 100)):int((t_edge + 0.02) * 100) + 1].min())
                clean = dip <= min(lvl, removed) - 6
                end = s["in"] + float(s["n"] / F)
                crosses = (near_start and w["s"] < s["in"] - 0.01) or (near_end and w["e"] > end + 0.01)
                if crosses:  # the word's own span runs past the cut: nothing else to weigh
                    rec["verdict"] = "fail"
                elif clean or other or removed <= lvl - 10:  # clean boundary, a removed neighbour, or room tone
                    rec["verdict"] = "pass"
                else:
                    rec["verdict"] = "fail" if voiced and abs(removed - lvl) <= 6 and ref >= 0.4 else "warn"
            out.append(rec)
    return sorted(out, key=lambda r: r["at"])


def fragments(tl: dict, sources: dict, cut: dict, extra: list[tuple[float, str]]) -> list[dict]:
    """through_speech candidates where Whisper, re-hearing the render, hears a word the edit doesn't have (extra:
    (time, word) from asr_diffs) within 0.6 s inside the piece of that edge."""
    out = []
    for c in through_speech(tl, sources, cut):
        lo, hi = (c["t"] - 0.6, c["t"] + 0.15) if c["side"] == "ends" else (c["t"] - 0.15, c["t"] + 0.6)
        heard = [w for t, w in extra if lo <= t <= hi]
        if heard:
            out.append({**c, "heard": " ".join(heard)})
    return out


def asr_diffs(tl: dict, sources: dict, heard: np.ndarray, eng: Engine) -> tuple[list[tuple[float, str, str]], list]:
    """(differences near cuts: (time, wanted, heard); words heard that the edit doesn't have: (time, word))."""
    intended = []
    for s, w, a, _ in _pieces_words(tl, sources):
        text = s.get("fix", {}).get(str(w["i"]), w["t"])  # the editor's correction is what should be heard
        if norm(text):
            intended.append((a, norm(text)))
    if not intended:  # picture-led cut on natural sound: ASR on market noise only adds noise
        return [], []
    got = [(w["s"], norm(w["t"])) for w in transcribe_audio(heard, eng)["words"] if norm(w["t"]) not in FILLERS]
    # near a cut = near the first or last word of a piece (with breathing room the cut frame itself sits up to a
    # second away from the words on either side of it)
    ends = [x for j in joins(tl, sources) for x in (j["end"], j["start"])]
    near = lambda t: min((abs(t - c) for c in ends), default=99) <= 0.6
    sm = difflib.SequenceMatcher(a=[x[1] for x in intended], b=[x[1] for x in got], autojunk=False)
    out, extra = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op in ("insert", "replace"):
            extra += got[j1:j2]
        if op in ("equal", "insert"):
            continue
        want, hear = " ".join(x[1] for x in intended[i1:i2]), " ".join(x[1] for x in got[j1:j2])
        if op == "replace" and difflib.SequenceMatcher(a=want.replace(" ", ""), b=hear.replace(" ", "")).ratio() >= 0.6:
            continue  # spelling / segmentation difference
        if near(intended[i1][0]):
            out.append((intended[i1][0], want, hear))
    return out, extra
