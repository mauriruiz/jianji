"""Timeline -> master (source quality), review render (timecode + section labels + captions) and .srt.

Video: one encode per piece, cached by (source range + settings), joined with concat copy, so a revision
re-encodes only the pieces that changed. Audio: one continuous mix with a centered equal-power crossfade at
every cut (no dip, no AAC-join drift), then two-pass loudnorm.
"""
from __future__ import annotations

import difflib
import json
import re
from fractions import Fraction
from pathlib import Path

import numpy as np

from .core import FFMPEG, Project, key, run, write_json
from .media import read_pcm, src_path, wav48k

R = 48000


def vcodec_args(codec: str, q) -> list[str]:
    if "videotoolbox" in codec:
        return ["-c:v", codec, "-q:v", str(q)] + (["-tag:v", "hvc1"] if "hevc" in codec else [])
    return ["-c:v", codec, "-crf", str(q), "-preset", "medium"] + (["-tag:v", "hvc1"] if "265" in codec else [])


def video_spans(tl: dict) -> list[dict]:
    """Picture track: A-roll pieces with B-roll overlays cut into them, contiguous on the timeline."""
    F = Fraction(tl["fps"])
    ovs = tl.get("overlays", [])
    look = lambda x: {k: x[k] for k in ("crop", "blur", "speed") if x.get(k)}
    spans = [{"src": o["src"], "in": o["in"], "n": o["n"], "rec": o["rec"], **look(o)} for o in ovs]
    for s in tl["segments"]:
        cur, end = s["rec"], s["rec"] + s["n"]
        sp = float(s.get("speed", 1))
        for o in ovs:
            a, b = max(cur, o["rec"]), min(end, o["rec"] + o["n"])
            if a >= b:
                continue
            if a > cur:
                spans.append({"src": s["src"], "in": round(s["in"] + float((cur - s["rec"]) / F) * sp, 3), "n": a - cur,
                              "rec": cur, **look(s)})
            cur = b
        if cur < end:
            spans.append({"src": s["src"], "in": round(s["in"] + float((cur - s["rec"]) / F) * sp, 3), "n": end - cur,
                          "rec": cur, **look(s)})
    return sorted(spans, key=lambda x: x["rec"])


def picture_filter(s: dict, F, w: int, h: int, pix: str) -> str:
    """Source picture -> one frame of the cut: speed (a sped-up bridge), crop (zoom on the code that matters, as
    [x0, y0, x1, y1] of the source), cover-scale to the frame, then blur boxes ([x0, y0, x1, y1] of the frame:
    something private on screen)."""
    vf = []
    if s.get("speed"):
        vf.append(f"setpts=PTS/{float(s['speed']):g}")
    vf.append(f"fps={F}")
    if s.get("crop"):
        x0, y0, x1, y1 = s["crop"]
        vf.append(f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}")
    vf.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1")
    chain = ",".join(vf)
    for k, (x0, y0, x1, y1) in enumerate(s.get("blur") or []):
        bw, bh, bx, by = round((x1 - x0) * w), round((y1 - y0) * h), round(x0 * w), round(y0 * h)
        chain += (f",split[m{k}][b{k}];[b{k}]crop={bw}:{bh}:{bx}:{by},boxblur=luma_radius={max(4, min(bw, bh) // 4)}:"
                  f"luma_power=3[c{k}];[m{k}][c{k}]overlay={bx}:{by}")
    return chain + f",format={pix}"


def render_video(p: Project, tl: dict, cat: dict, out: Path, codec: str, q) -> Path:
    F = Fraction(tl["fps"])
    w, h = tl["size"]
    spans = video_spans(tl)
    ten_bit = any("10" in (cat["sources"][s["src"]].get("pix") or "") for s in spans)
    pix = "p010le" if ten_bit and "hevc" in codec else "yuv420p"
    files = []
    for s in spans:
        e = cat["sources"][s["src"]]
        extra = [s.get(k) for k in ("crop", "blur", "speed") if s.get(k)]
        f = p.path("cache", "seg", f"{key(e['fp'], s['in'], s['n'], tl['fps'], w, h, codec, q, pix, *map(str, extra))}.mp4")
        if not f.exists():
            tmp = f.with_suffix(".tmp.mp4")
            vf = picture_filter(s, F, w, h, pix)
            if e["kind"] == "audio":
                inp = ["-f", "lavfi", "-i", f"color=black:s={w}x{h}:r={F}"]
            else:
                inp = ["-hwaccel", "videotoolbox", "-ss", f"{s['in']:.3f}", "-i", src_path(p, e)]
            run([FFMPEG, "-v", "error", "-y", *inp, "-frames:v", s["n"], "-vf", vf, "-an",
                 *vcodec_args(codec, q), "-fps_mode", "cfr", "-video_track_timescale", 90000, tmp])
            tmp.replace(f)
        files.append(f)
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", 0, "-i", lst, "-c", "copy", out])
    lst.unlink()
    return out


def mix_audio(p: Project, tl: dict, cat: dict, xfade_frames: int) -> np.ndarray:
    F = Fraction(tl["fps"])
    segs = tl["segments"]
    mix = np.zeros((round(tl["frames"] * R / F), 2), np.float32)
    h_def = float(xfade_frames / 2 / F)
    default_room = [h_def, h_def]
    # fade half-length per cut: limited by the silence on both sides (compile's "room")
    cut_h = [0.0] + [min(h_def, segs[j - 1].get("room", default_room)[1], segs[j].get("room", default_room)[0])
                     for j in range(1, len(segs))] + [0.0]
    for k, s in enumerate(segs):
        e = cat["sources"][s["src"]]
        if not e.get("audio_streams"):
            continue
        pre, post = cut_h[k], cut_h[k + 1]
        dur = float(s["n"] / F)
        if s.get("speed") and "tone" not in s:
            continue  # a sped-up bridge with no room tone found: silent
        at = s.get("tone", s["in"])  # a room-tone piece: picture goes on, sound from a pause elsewhere in the clip
        a = read_pcm(wav48k(p, s["src"], e), at - pre, dur + pre + post, rate=R) * 10 ** (s.get("gain_db", 0) / 20)
        n_in, n_out = max(round(2 * pre * R), round(0.003 * R)), max(round(2 * post * R), round(0.003 * R))
        a[:n_in] *= np.sin(np.linspace(0, np.pi / 2, n_in, dtype=np.float32))[:, None]
        a[-n_out:] *= np.cos(np.linspace(0, np.pi / 2, n_out, dtype=np.float32))[:, None]
        pos = round((float(s["rec"] / F) - pre) * R)
        lo, hi = max(0, pos), min(len(mix), pos + len(a))
        mix[lo:hi] += a[lo - pos:hi - pos]
    ovs = tl.get("overlays", [])
    for k, o in enumerate(ovs):  # B-roll natural sound, only when the edit asks for it (gain_db)
        e = cat["sources"][o["src"]]
        if o.get("gain_db") is None or not e.get("audio_streams"):
            continue
        a = read_pcm(wav48k(p, o["src"], e), o["in"], float(o["n"] / F), rate=R) * 10 ** (o["gain_db"] / 20)
        # 0.25 s fades into the dialogue; between back-to-back sounding overlays only a cut-length fade,
        # or 1 s shots would pump. ponytail: linear, inside each shot (no pre-roll handles); fine at 2 frames
        joined = lambda j: 0 <= j < len(ovs) and ovs[j].get("gain_db") is not None and \
            (ovs[j]["rec"] + ovs[j]["n"] == o["rec"] if j < k else o["rec"] + o["n"] == ovs[j]["rec"])
        short = round(h_def * R)
        n_in = min(short if joined(k - 1) else round(0.25 * R), len(a) // 2)
        n_out = min(short if joined(k + 1) else round(0.25 * R), len(a) // 2)
        a[:n_in] *= np.linspace(0, 1, n_in, dtype=np.float32)[:, None]
        a[len(a) - n_out:] *= np.linspace(1, 0, n_out, dtype=np.float32)[:, None]
        pos = round(float(o["rec"] / F) * R)
        hi = min(len(mix), pos + len(a))
        mix[pos:hi] += a[:hi - pos]
    return mix


def loudnorm(p: Project, mix: np.ndarray, out: Path, target: float, tp: float) -> dict:
    raw = out.with_suffix(".f32")
    raw.write_bytes(mix.astype(np.float32).tobytes())
    src = ["-f", "f32le", "-ar", R, "-ac", 2, "-i", raw]
    flt = f"loudnorm=I={target}:TP={tp}:LRA=11"
    m = json.loads(re.findall(r"\{[^{}]+\}", run([FFMPEG, "-hide_banner", *src, "-af", f"{flt}:print_format=json",
                                                   "-f", "null", "-"]).stderr.decode())[-1])
    if float(m["input_i"]) < -70:  # silence: nothing to normalize
        run([FFMPEG, "-v", "error", "-y", *src, "-c:a", "pcm_s24le", out])
    else:
        run([FFMPEG, "-v", "error", "-y", *src, "-af",
             f"{flt}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}:"
             f"measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true",
             "-ar", R, "-c:a", "pcm_s24le", out])
    raw.unlink()
    return m


# ---- captions ---------------------------------------------------------------------------------------------

def _clean(t: str) -> str:
    t = re.sub(r"\s+([,.?!;:%])", r"\1", t)
    t = re.sub(r"\.{2,}|…", "", t)  # trailing "..." from false starts reads as noise on screen
    t = re.sub(r"([.?!] [¿¡]?)(\w)", lambda m: m[1] + m[2].upper(), t)  # a cut can join "pounds. que"
    return re.sub(r"\s{2,}", " ", t).strip()


NBSP = " "  # joins words that must stay on one line: glossary terms, names, a number and its unit
UNITS = {"%", "euros", "euro", "€", "libras", "libra", "pounds", "pound", "£", "dolares", "dollars", "dolar", "$",
         "km", "kilometros", "kg", "kilos", "gramos", "minutos", "minutes", "min", "horas", "hours", "segundos",
         "seconds", "anos", "years", "dias", "days", "mil", "millones", "million", "millions", "k", "alumnos",
         "students", "personas", "people", "veces", "times"}
NUM = re.compile(r"^[£$€]?\d[\d.,]*[%€$£kKmM]?$")


def cased(orig: str, term: str) -> str:
    """Glossary spelling, but a capital the speaker's sentence needs stays: "Carbonara 8,50" with glossary "carbonara"."""
    return term[:1].upper() + term[1:] if orig[:1].isupper() and term[:1].islower() else term


def glossary_fix(words: list[tuple], glossary: list[str]) -> list[tuple]:
    """(t0, t1, text) words -> same, with glossary terms spelled right and kept whole. Conservative: exact match
    after normalizing case/accents, or very close ("Camdem Town"); merging extra words needs an even closer match."""
    from .transcribe import norm
    terms = sorted(((g, norm(g).replace(" ", "")) for g in glossary if norm(g)), key=lambda x: -len(x[1]))
    out, i = [], 0
    while i < len(words):
        hit = None
        for term, k in terms:
            n0 = len(term.split())
            for n in sorted({n0, n0 + 1, max(1, n0 - 1)}, key=lambda n: abs(n - n0)):
                if i + n > len(words):
                    continue
                cand = "".join(norm(w[2]).replace(" ", "") for w in words[i:i + n])
                need = (0.8 if len(k) >= 6 else 0.85) if n == n0 else 0.92  # "kubernetis" for "Kubernetes" is 0.9
                # a misspelling is about as long as the term; "pasta" is 0.83 to "Pasta&Co" but another word
                close_len = abs(len(cand) - len(k)) <= max(1, len(k) // 8)
                if cand == k or (len(k) >= 4 and close_len and difflib.SequenceMatcher(a=cand, b=k).ratio() >= need):
                    hit = (term, n)
                    break
            if hit:
                break
        if not hit:
            out.append(words[i])
            i += 1
            continue
        term, n = hit
        lead = re.match(r"^[¿¡\"'(]+", words[i][2])
        tail = re.search(r"[^\w]+$", words[i + n - 1][2])
        out.append((words[i][0], words[i + n - 1][1],
                    (lead[0] if lead else "") + cased(words[i][2][len(lead[0]) if lead else 0:], term.replace(" ", NBSP))
                    + (tail[0] if tail else "")))
        i += n
    return out


def keep_together(words: list[tuple], limit: int) -> list[tuple]:
    """Join names ("Café Central", "Ana García") and numbers with their unit ("13 euros") so no line or cue
    break lands between them."""
    from .transcribe import norm
    out = []
    for w in words:
        if out:
            a, b = out[-1][2], w[2]
            num_unit = bool(NUM.match(a.split(NBSP)[-1])) and norm(b) in UNITS
            names = (len(a) > 1 and a[:1].isupper() and a[-1:].isalnum() and len(b) > 1 and b[:1].isupper()
                     and b[0].isalpha())
            if (num_unit or names) and len(a) + 1 + len(b) <= limit:
                out[-1] = (out[-1][0], w[1], a + NBSP + b)
                continue
        out.append(w)
    return out


def wrap(text: str, limit: int) -> str:
    """Two balanced lines when the text is longer than one line; never breaks inside an NBSP-joined group."""
    toks = []
    for t in text.split(" "):  # a lone separator ("·", "-", "/") stays with the word before it, never starts a line
        if toks and not any(c.isalnum() for c in t):
            toks[-1] += NBSP + t
        else:
            toks.append(t)
    text = " ".join(toks)
    if len(text) <= limit or len(toks) < 2:
        return text
    best = min(range(1, len(toks)), key=lambda i: (max(len(" ".join(toks[:i])), len(" ".join(toks[i:]))),
                                                   len(" ".join(toks[:i]))))
    return " ".join(toks[:best]) + "\n" + " ".join(toks[best:])


def protect(text: str, glossary: list[str]) -> str:
    """On-screen text: glossary spelling and no line break inside a glossary term."""
    for g in sorted(glossary, key=len, reverse=True):
        text = re.sub(r"\b" + r"\s+".join(map(re.escape, g.split())) + r"\b", lambda m: cased(m[0], g.replace(" ", NBSP)),
                      text, flags=re.I)
    return text


def caption_cues(tl: dict, sources: dict, cfg: dict, glossary: list[str] | None = None) -> list[tuple[float, float, str]]:
    if tl.get("captions"):  # your own captions, edited in Resolve and pulled back: verbatim
        return [(c["a"], c["z"], c["text"]) for c in tl["captions"]]
    from .transcribe import norm
    F = Fraction(tl["fps"])
    line = int(cfg.get("line_chars", cfg["max_chars"]))
    words = []
    for s in tl["segments"]:
        W = sources[s["src"]]["words"]
        for k in s["kept"]:
            w = W[k]
            text = s.get("fix", {}).get(str(k), w["t"])
            if w.get("filler"):
                continue
            t0 = float(s["rec"] / F) + w["s"] - s["in"]
            t1 = float(s["rec"] / F) + w["e"] - s["in"]
            if not text:  # fixed away ("Cuber Netes" -> "Kubernetes"): its time joins the previous word, no gap to break on
                if words:
                    words[-1] = (words[-1][0], t1, words[-1][2])
                continue
            # stutter "the the" -> one word on screen; "it, it" or "longer, longer" is speech, not a stutter
            if words and norm(words[-1][2]) == norm(text) and words[-1][2][-1:].isalnum():
                continue
            words.append((t0, t1, text))
    words = keep_together(glossary_fix(words, glossary or []), line)
    groups, cur = [], []
    for w in words:
        if cur:
            text = " ".join(x[2] for x in cur)
            n = len(_clean(text + " " + w[2]))
            # up to 10 characters over, so the word that ends a sentence isn't left alone in the next cue
            too_long = n > cfg["max_chars"] and not (w[2][-1:] in ".?!" and n <= cfg["max_chars"] + 10)
            if (too_long or w[0] - cur[-1][1] >= cfg["break_gap"]
                    or w[1] - cur[0][0] > cfg["max_dur"] or (text[-1:] in ".?!" and len(text) >= 15)
                    or (text[-1:] in ",;:" and len(text) >= cfg["max_chars"] * 0.5)):
                carry = []
                # don't leave "what I" dangling at the end of a line: move short trailing words down
                while too_long and len(cur) > 2 and len(cur[-1][2]) <= 4 and cur[-1][2][-1:].isalnum():
                    carry.insert(0, cur.pop())
                groups.append(cur)
                cur = carry
        cur.append(w)
    if cur:
        groups.append(cur)
    cues = []
    for i, g in enumerate(groups):
        start, end = g[0][0], max(g[-1][1] + 0.25, g[0][0] + cfg["min_dur"])
        if i + 1 < len(groups):
            end = min(end, groups[i + 1][0][0] - 0.04)
        end = min(end, float(tl.get("frames", 10 ** 9) / F))  # the last cue's hold never outlasts the cut
        text = _clean(" ".join(x[2] for x in g))
        if text and (not cues or cues[-1][2][-1:] in ".?!"):  # cuts can leave a sentence starting lowercase
            text = text[0].upper() + text[1:]
        if text:
            cues.append((round(start, 3), round(end, 3), wrap(text, line)))
    return cues


def _ts(t: float, sep=",") -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{sep}{ms % 1000:03d}"


def write_srt(cues, path: Path) -> Path:
    path.write_text("".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t}\n\n" for i, (a, b, t) in enumerate(cues, 1)))
    return path


def _ass_t(t: float) -> str:
    cs = round(t * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _style(name: str, c: dict, h: int, fallback: dict) -> str:
    g = lambda k: c.get(k, fallback[k])
    back = g("back")
    box = back != "&H00000000"  # BorderStyle 3: an opaque box in the back colour instead of an outline
    return (f"Style: {name},{g('font')},{round(g('size') * h)},{g('colour')},&H00FFFFFF,"
            f"{back if box else '&H00000000'},{back},{-1 if g('bold') else 0},0,0,0,100,100,0,0,{3 if box else 1},"
            f"{max(1, round(g('outline') * h))},0,{g('align')},{round(g('margin_l') * h)},{round(g('margin_r') * h)},"
            f"{round(g('margin_v') * h)},1")


SCALE = {"chat": 0.8, "chapter": 1.6}  # text size of a chat or chapter card against a story line
CAP = {"font": "Helvetica", "bold": False, "size": 1 / 18, "colour": "&H00FFFFFF", "back": "&H00000000",
       "outline": 2 / 540, "align": 2, "margin_l": 20 / 540, "margin_r": 20 / 540, "margin_v": 1 / 20}


def _bgr(rgba: str) -> str:
    """FCPXML "r g b a" -> ASS colour override."""
    r, g, b = (round(float(x) * 255) for x in rgba.split()[:3])
    return f"&H{b:02X}{g:02X}{r:02X}&"


def shown(t: dict, glossary: list[str] | None, tc: dict) -> str:
    """An on-screen text as it will read: glossary spelling, and two balanced lines unless the editor broke it."""
    body = protect(t["text"], glossary or [])
    if "\n" not in body and tc.get("line_chars"):
        body = wrap(body, int(tc["line_chars"]))
    return body


def write_ass(cues, tl: dict, path: Path, height: int, cap_cfg: dict | None = None, text_cfg: dict | None = None,
              labels: bool = True, glossary: list[str] | None = None, place: dict | None = None,
              frames: list[dict] | None = None) -> Path:
    """place: caption lanes and text positions (pixels of the timeline frame) from place.py; frames: face and subject
    boxes to draw faintly (review render only), so it shows why each text sits where it does."""
    F = Fraction(tl["fps"])
    w = round(tl["size"][0] * height / tl["size"][1] / 2) * 2
    k = height / tl["size"][1]  # timeline pixels -> this file's pixels
    place = place or {}
    alt = {**(cap_cfg or {}), "align": 8, "margin_v": (cap_cfg or {}).get("alt_margin_v", 0.15)}
    titles = {s["id"]: s["title"] for s in tl["sections"]}
    tc = text_cfg or {}
    txt = {**CAP, "bold": True, **tc}
    hi = {**txt, "colour": "&H00000000", "back": tc.get("hi_colour", "&H0000D7FF"), "align": tc.get("hi_align", 5),
          "margin_v": tc.get("hi_margin_v", txt.get("margin_v", CAP["margin_v"]))}
    # streams: a chat question as a card in the lower third, a chapter card mid-frame; both white on a dark box
    chat = {**txt, "size": txt["size"] * SCALE["chat"], "back": "&H50000000", "align": 8,
            "margin_v": tc.get("chat_margin_v", 0.62)}
    chapter = {**txt, "size": txt["size"] * SCALE["chapter"], "back": "&H30000000", "align": 8,
               "margin_v": tc.get("chapter_margin_v", 0.40)}
    lab = round(min(height / 30, w / 34))  # a vertical review is narrow: the label must not run into the timecode
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {height}", "WrapStyle: 0", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding",
        _style("Cap", cap_cfg or {}, height, CAP),
        _style("CapAlt", alt, height, CAP),
        _style("Txt", txt, height, CAP),
        _style("Hi", {k: v for k, v in hi.items() if not k.startswith("hi_")}, height, CAP),
        _style("Chat", chat, height, CAP),
        _style("Chapter", chapter, height, CAP),
        f"Style: Lab,Menlo,{lab},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,3,"
        "4,0,9,12,12,10,1",
        f"Style: Lab2,Menlo,{lab},&H0080FFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,3,"
        f"4,0,9,12,12,{22 + lab},1",
        "Style: Face,Arial,10,&HFF000000,&HFF000000,&H6000FFFF,&HFF000000,0,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1",
        "Style: Subj,Arial,10,&HFF000000,&HFF000000,&H80FF00FF,&HFF000000,0,0,0,0,100,100,0,0,1,2,0,7,0,0,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    esc = lambda s: s.replace("{", "(").replace("}", ")").replace("\n", "\\N").replace(NBSP, "\\h")
    lanes = place.get("lanes") or ["main"] * len(cues)
    for (a, b, t), lane in zip(cues, lanes):
        lines.append(f"Dialogue: 0,{_ass_t(a)},{_ass_t(b)},{'CapAlt' if lane == 'alt' else 'Cap'},,0,0,0,,{esc(t)}")
    for t in tl.get("texts", []):
        body, a, b, pos = shown(t, glossary, tc), float(t["rec"] / F), float((t["rec"] + t["n"]) / F), ""
        pl = place.get("texts", {}).get(t["id"])
        look = t.get("look") or {}  # your font, size and colour from Resolve
        tags = (f"\\fn{look['font']}" if look.get("font") else "") + (f"\\fs{look['size'] * k:.0f}" if look.get("size") else "") \
            + (f"\\c{_bgr(look['colour'])}" if look.get("colour") else "")
        if pl:  # a slot clear of faces and the subject, maybe moved in time
            a, b = pl["a"], pl["z"]
            pos = f"{{\\an8\\pos({pl['cx'] * k:.0f},{pl['top'] * k:.0f}){tags}}}"
        elif tags:
            pos = "{" + tags + "}"
        # own layer: libass shoves same-layer events apart, which once moved a callout under the captions
        lines.append(f"Dialogue: 2,{_ass_t(a)},{_ass_t(b)},{t['style'].capitalize()},,0,0,0,,{pos}{esc(body)}")
    W, H = tl["size"]
    for f in frames or []:  # faint boxes: faces (cyan), and the subject of shots without a face (magenta)
        a, b = max(0.0, f["t"] - 0.125), f["t"] + 0.125
        boxes = [("Face", x) for x in f["faces"]]
        if not f["faces"] and f.get("salient"):
            s = max(f["salient"], key=lambda s: s[4] * (s[2] - s[0]) * (s[3] - s[1]))
            if (s[2] - s[0]) * (s[3] - s[1]) < 0.6:
                boxes.append(("Subj", s))
        for style, x in boxes:
            x0, y0, x1, y1 = (round(v * d * k) for v, d in zip(x[:4], (W, H, W, H)))
            lines.append(f"Dialogue: 3,{_ass_t(a)},{_ass_t(b)},{style},,0,0,0,,"
                         f"{{\\pos(0,0)\\p1}}m {x0} {y0} l {x1} {y0} l {x1} {y1} l {x0} {y1}{{\\p0}}")
    if labels:
        for s in tl["segments"]:
            title = titles.get(s["sec"], "")[:28] if w >= height else ""  # vertical: no room next to the timecode
            label = f"{s['sec'] or ''} {title} · {s['clip']} · #{s['id']}".replace("  ", " ").strip()
            lines.append(f"Dialogue: 1,{_ass_t(float(s['rec'] / F))},{_ass_t(float((s['rec'] + s['n']) / F))},Lab,,0,0,0,,"
                         f"{esc(label)}")
        for o in tl.get("overlays", []):
            lines.append(f"Dialogue: 1,{_ass_t(float(o['rec'] / F))},{_ass_t(float((o['rec'] + o['n']) / F))},Lab2,,0,0,0,,"
                         f"B-roll {esc(o['id'])} · {o['src']} {o['in']:.1f}s")
    path.write_text("\n".join(lines) + "\n")
    return path


def same_picture_and_sound(a: dict | None, b: dict) -> bool:
    """True when two timelines differ only in captions, on-screen texts, markers or sections."""
    if not a:
        return False
    pick = lambda tl: (tl["fps"], tl["size"], tl["frames"],
                       [(s["src"], s["in"], s["n"], s.get("gain_db"), s.get("room"), s.get("tone"), s.get("speed"),
                         s.get("crop"), s.get("blur")) for s in tl["segments"]],
                       [(o["src"], o["in"], o["n"], o["rec"], o.get("gain_db"), o.get("crop"), o.get("blur"))
                        for o in tl.get("overlays", [])])
    return pick(a) == pick(b)


def safe_zone_box(cfg: dict, w: int, h: int, scale: float) -> str | None:
    z = cfg.get("safe_zone")
    if not z:
        return None
    k = h / z["size"][1] * scale
    x, y = round(z["left"] * k), round(z["top"] * k)
    return (f"drawbox=x={x}:y={y}:w={round(w * scale - (z['left'] + z['right']) * k)}:"
            f"h={round(h * scale - (z['top'] + z['bottom']) * k)}:color=white@0.35:t=2")


def render(p: Project, tl: dict, cat: dict, sources: dict, v: int, reuse_master: Path | None = None) -> dict:
    """reuse_master: a previous master with the same picture and sound (caption- or text-only revision)."""
    rc, cc = p.cfg["render"], p.cfg["captions"]
    F = Fraction(tl["fps"])
    secs = float(tl["frames"] / F)
    w, h = tl["size"]
    d = p.path("renders", "x").parent
    master = d / f"master.v{v:03d}.mp4"
    loud = None
    if reuse_master:
        master.unlink(missing_ok=True)
        master.hardlink_to(reuse_master)  # same bytes, no re-encode, no extra disk
    else:
        p.ensure_space(secs * (2 * w * h * float(F) * 0.15 / 8 + R * 2 * 4 * 3), "render")
        with p.timed("render_video", v=v):
            video = render_video(p, tl, cat, d / f"_video.v{v:03d}.mp4", rc["master_codec"], rc["master_quality"])
        with p.timed("render_audio", v=v):
            audio = d / f"_audio.v{v:03d}.wav"
            loud = loudnorm(p, mix_audio(p, tl, cat, rc["xfade_frames"]), audio, rc["loudness"], rc["true_peak"])
        run([FFMPEG, "-v", "error", "-y", "-i", video, "-i", audio, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
             # aac_at (Apple): ffmpeg's native aac encoder decodes one AAC frame (21 ms) early here; QA caught it
             "-c:a", "aac_at", "-b:a", "320k", "-movflags", "+faststart", master])
        video.unlink()
        audio.unlink()
    gl = p.cfg.get("glossary", [])
    cues = caption_cues(tl, sources, cc, gl)
    srt = write_srt(cues, d / f"captions.v{v:03d}.srt")
    tx = p.cfg.get("texts")
    place, frames, log = {}, None, []
    burned = bool(rc.get("burn_captions", rc.get("final_burned")))  # shorts: captions in the picture
    if burned or tl.get("texts"):  # keep burned text off faces and off the dish
        from .place import caption_boxes, caption_lanes, place_texts
        from .vision import detect
        frames = detect(p, master)
        cap, alt_mv = {**CAP, **cc}, cc.get("alt_margin_v", 0.15)
        lanes = caption_lanes(cues, frames, cap, w, h, alt_mv) if burned else ["main"] * len(cues)
        busy = [(a, z, b, "cap") for a, z, b in caption_boxes(cues, lanes, cap, w, h, alt_mv)] if burned else []
        from .place import box
        texts = [{**t, "shown": shown(t, gl, tx or {})} for t in tl.get("texts", [])]
        size = (tx or {}).get("size", CAP["size"]) * h
        hand = {t["id"]: {"cx": t["place"]["cx"], "top": t["place"]["top"], "a": float(t["rec"] / F),
                         "z": float((t["rec"] + t["n"]) / F), "slot": "yours", "face": 0, "subject": 0}
               for t in texts if t.get("place")}  # where you left it in Resolve
        busy += [(x["a"], x["z"], box(t["shown"], x["cx"], x["top"], size), t["style"]) for t in texts
                 for x in [hand.get(t["id"])] if x]
        placed, log = place_texts([t for t in texts if t["id"] not in hand], F, frames, {**CAP, **(tx or {})}, w, h,
                                  p.cfg.get("safe_zone"), busy, secs)
        placed.update(hand)
        place = {"lanes": lanes, "texts": placed}
        write_json(d / f"placement.v{v:03d}.json", {**place, "log": log})
    ass = write_ass(cues, tl, d / f"review.v{v:03d}.ass", rc["review_height"], cc, tx, glossary=gl, place=place,
                    frames=frames)
    review = d / f"review.v{v:03d}.mp4"
    with p.timed("render_review", v=v):
        tc = (f"drawtext=font=Menlo:timecode='00\\:00\\:00\\:00':rate={F}:fontsize={rc['review_height'] // 24}:"
              "fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=6:x=12:y=10")
        guide = safe_zone_box(p.cfg, w, h, rc["review_height"] / h)  # faint safe-zone frame, review only
        run([FFMPEG, "-v", "error", "-y", "-i", master.name, "-vf",
             ",".join(filter(None, [f"scale=-2:{rc['review_height']}", f"subtitles={ass.name}", guide, tc])),
             "-c:v", "h264_videotoolbox", "-b:v", "3M", "-c:a", "aac_at", "-b:a", "128k", review.name], cwd=d)
    outs = {"master": str(master), "review": str(review), "srt": str(srt), "loudnorm_input": loud, "placement": log}
    # the upload file, always: on-screen texts burned in; captions too where the profile burns them (shorts),
    # otherwise they ship as the SRT (YouTube). Nothing to burn: the master itself.
    final = d / f"final.v{v:03d}.mp4"
    final.unlink(missing_ok=True)
    if burned or tl.get("texts"):
        fass = write_ass(cues if burned else [], tl, d / f"final.v{v:03d}.ass", h, cc, tx, labels=False, glossary=gl,
                         place=place)
        rate = max(8, round(16 * w * h / (1080 * 1920)))  # 16 Mb/s at 1080x1920, scaled by pixels
        with p.timed("render_final", v=v):
            run([FFMPEG, "-v", "error", "-y", "-i", master.name, "-vf", f"subtitles={fass.name}",
                 "-c:v", "h264_videotoolbox", "-b:v", f"{rate}M", "-pix_fmt", "yuv420p", "-c:a", "copy",
                 "-movflags", "+faststart", final.name], cwd=d)
        outs["final_ass"] = str(fass)
    else:
        final.hardlink_to(master)
    outs["final"] = str(final)
    return outs
