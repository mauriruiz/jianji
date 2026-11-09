"""DaVinci Resolve as your editing surface: the cut as an editable timeline on the original footage, and back.

FCPXML 1.10 is the one format (tested on Resolve 21.2 free: sources online, vertical right, PNGs transparent,
the SRT as editable subtitles; FCP7 XML lost the texts as image sequences and 6 transitions; OTIO untested).
Free Resolve runs no scripts, so `jianji/resolve/IMPORT.md` is the way in and out.

`jian resolve <name>` (also run by every `jian cut`) writes jianji/resolve/<name> vNNN.fcpxml on the original
files (every edge can be dragged to recover material), each text as an editable title (callouts over a box-only
PNG: FCPXML has no title background), a manifest for the way back and IMPORT.md. `jian pull <name>` reads what
Resolve exports (FCPXML + the subtitle SRT) and writes the next edit version at exactly your frames; `jian diff`
lists what you changed.

FCPXML is magnetic: the A-roll is the spine (V1, with its sound on A1); connected clips sit on lanes above it:
lane 1 B-roll (V2, its sound on A2), lane 2 callout boxes (V3), lane 3 texts as titles (V4), lane 4 room tone (A3, an
audio-only asset). Never a negative lane: Resolve gives it a video track under the spine, which pushed every
track up by one (an empty V1 with a grey element: the room-tone clip's missing picture).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from xml.sax.saxutils import escape as _escape

from .core import FFMPEG, Project, die, read_json, run, version_of, write_json


def escape(s) -> str:
    """Safe inside an attribute too: quotes escaped (a QA note quoting `"blur"` broke the whole file in Resolve) and
    control characters dropped (OCR text lands in markers; XML 1.0 doesn't allow them)."""
    return _escape(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s)), {'"': "&quot;"})

LANES = {"V2": 1, "V3": 2, "V4": 3, "A3": 4}  # B-roll, callout boxes, titles, room tone
AUDIO = {0: "A1", 1: "A2", 4: "A3"}  # sound by lane, as Resolve numbers it: the A-roll's, the B-roll's, room tone
WORKDIR = Path("~/Movies/Resolve")  # cache, gallery and project media for jianji projects: one folder to purge


def _secs(x) -> str:
    """FCPXML rational time."""
    x = Fraction(x)
    return f"{x.numerator}/{x.denominator}s" if x.denominator != 1 else f"{x.numerator}s"


def _t(s: str | None) -> Fraction:
    if not s:
        return Fraction(0)
    s = s.rstrip("s")
    if "/" in s:
        a, b = s.split("/")
        return Fraction(int(a), int(b))
    return Fraction(s)


def _url(path) -> str:
    return "file://" + quote(str(path))


def _path(url: str) -> str:
    u = urlparse(url)
    return str(Path(unquote(u.path if u.scheme == "file" else url)).resolve())


def _tc_secs(tc: str | None, fps: Fraction) -> Fraction:
    if not tc:
        return Fraction(0)
    h, m, s, f = (int(x) for x in re.split(r"[:;.]", tc))
    return Fraction(h * 3600 + m * 60 + s) + Fraction(f) / fps


def _rgba(ass: str) -> str:
    """ASS &HAABBGGRR -> FCPXML "r g b a" (0-1)."""
    h = ass.strip("&H")[-8:].rjust(8, "0")
    a, b, g, r = (int(h[k:k + 2], 16) for k in (0, 2, 4, 6))
    return f"{r / 255:.3g} {g / 255:.3g} {b / 255:.3g} {1 - a / 255:.3g}"


def _ass(rgba: str) -> str:
    r, g, b, *a = (float(x) for x in rgba.split())
    return f"&H{round((1 - (a[0] if a else 1)) * 255):02X}{round(b * 255):02X}{round(g * 255):02X}{round(r * 255):02X}"


def _loud_gain(p: Project, tl: dict, cat: dict, v: int) -> float:
    """dB that takes the plain mix to the profile's loudness, as jian's master does: one gain on every clip, so
    Resolve plays at the level of the render."""
    from .render import R, mix_audio
    loud = (read_json(p.out / "renders" / f"qa.v{v:03d}.json") or {}).get("loudnorm")
    if loud:  # measured by `jian cut`
        return round(p.cfg["render"]["loudness"] - float(loud["input_i"]), 2) if float(loud["input_i"]) > -70 else 0.0
    mix = mix_audio(p, tl, cat, p.cfg["render"]["xfade_frames"])
    raw = p.path("cache", "reel", "mix.f32")
    raw.write_bytes(mix.astype("float32").tobytes())
    err = run([FFMPEG, "-hide_banner", "-f", "f32le", "-ar", R, "-ac", 2, "-i", raw, "-af",
               "loudnorm=print_format=json", "-f", "null", "-"]).stderr.decode()
    raw.unlink()
    m = json.loads(re.findall(r"\{[^{}]+\}", err)[-1])
    qa = read_json(p.out / "renders" / f"qa.v{v:03d}.json")
    if qa is not None:  # measured once: a cut from before `jian cut` kept it
        write_json(p.out / "renders" / f"qa.v{v:03d}.json", {**qa, "loudnorm": m})
    i = float(m["input_i"])
    return round(p.cfg["render"]["loudness"] - i, 2) if i > -70 else 0.0


def _texts(p: Project, tl: dict, place: dict, out: Path) -> list[dict]:
    """Each text as a title: its words (glossary spelling, line breaks), style, time and centre; callouts also get
    their yellow box alone as a transparent PNG (the wording stays editable in the title above it)."""
    from .place import box
    from .render import CAP, SCALE, shown, write_ass
    F = Fraction(tl["fps"])
    W, H = tl["size"]
    tx = {**CAP, "bold": True, **(p.cfg.get("texts") or {})}
    size = tx["size"] * H
    out.mkdir(parents=True, exist_ok=True)
    res = []
    for t in tl.get("texts", []):
        pl = (place.get("texts") or {}).get(t["id"])
        a, z = (pl["a"], pl["z"]) if pl else (float(t["rec"] / F), float((t["rec"] + t["n"]) / F))
        words = shown(t, p.cfg.get("glossary", []), p.cfg.get("texts") or {}).replace(" ", " ")
        look = t.get("look") or {}
        fs = look.get("size", size * SCALE.get(t["style"], 1))
        cx, top = (pl["cx"], pl["top"]) if pl else (W / 2, tx.get(f"{t['style']}_margin_v", tx.get("margin_v", 0.15)) * H)
        b = box(words, cx, top, fs)
        it = {"text": t["id"], "words": words, "style": t["style"], "a": a, "z": z, "cx": round(cx), "cy": round((b[1] + b[3]) / 2),
              "font": look.get("font", tx["font"]), "size": round(fs), "bold": bool(tx.get("bold", True)),
              "colour": look.get("colour", "0 0 0 1" if t["style"] == "hi" else _rgba(tx.get("colour", "&H00FFFFFF"))),
              "outline": round(tx.get("outline", 0.004) * H, 1)}
        back = {"hi": tx.get("hi_colour", "&H0000D7FF"), "chat": "&H50000000", "chapter": "&H30000000"}.get(t["style"])
        if back:  # the box alone: libass draws it, the fill is transparent
            it["back"] = _rgba(back)
            one = {**tl, "texts": [t]}
            ass = write_ass([], one, out / f"{t['id']}.ass", H, p.cfg["captions"], p.cfg.get("texts"), labels=False,
                            glossary=p.cfg.get("glossary", []), place={"texts": {t["id"]: pl}} if pl else {})
            ass.write_text(re.sub(r"(,(?:Hi|Chat|Chapter),,0,0,0,,\{[^}]*)\}", r"\1\\1a&HFF&}", ass.read_text()))
            png = out / f"{t['id']}.box.png"
            run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black@0.0:s={W}x{H}:r={F}:d={z + 1:.3f},format=rgba",
                 "-vf", f"subtitles=filename={ass.name}:alpha=1", "-ss", f"{(a + z) / 2:.3f}", "-frames:v", 1, png.name],
                cwd=out)
            ass.unlink()
            it["png"] = str(png)
        res.append(it)
    return res


def _look(x: dict) -> dict:
    return {k: x[k] for k in ("speed", "crop", "blur") if x.get(k)}


def _transform(i: dict, W: int, H: int, sw: int, sh: int) -> str:
    """A crop ([x0, y0, x1, y1] of the source) as an FCPXML zoom: scale about the centre, then move the cropped
    region's centre to the frame's centre. "" when there's nothing to say."""
    if not i.get("crop"):
        return ""
    x0, y0, x1, y1 = i["crop"]
    cover = max(W / sw, H / sh)  # jian's (and Resolve's "scale full frame with crop") fit before the zoom
    z = max(1 / (x1 - x0), 1 / (y1 - y0))
    dx, dy = ((x0 + x1) / 2 - 0.5) * sw * cover * z, ((y0 + y1) / 2 - 0.5) * sh * cover * z
    return f'<adjust-transform position="{-dx / H * 100:.4g} {dy / H * 100:.4g}" scale="{z:.4g} {z:.4g}"/>'


def plan(p: Project, v: int) -> dict:
    """The cut as tracks of clips in timeline frames, for the writer and the manifest."""
    from .media import src_path
    d = p.out / "renders"
    tl = read_json(d / f"timeline.v{v:03d}.json") or die(f"no timeline v{v:03d}: run `jian cut` first")
    cat = read_json(p.out / "catalog.json")
    edit = read_json(p.out / "edits" / f"edit.v{v:03d}.json", {})
    place = read_json(d / f"placement.v{v:03d}.json", {})
    F = Fraction(tl["fps"])
    gain = _loud_gain(p, tl, cat, v)
    srcs = {}
    for sid, e in cat["sources"].items():
        fps = Fraction(e.get("fps") or tl["fps"])
        srcs[sid] = {"path": str(src_path(p, e).resolve()), "fps": fps, "dur": e["dur"], "w": e.get("w"),
                     "h": e.get("h"), "audio": bool(e.get("audio_streams")), "tc": _tc_secs(e.get("tc"), fps),
                     "channels": e.get("channels") or 2}
    fr = lambda secs: round(Fraction(secs).limit_denominator(100000) * F)  # source seconds -> frames
    items = []
    for s in tl["segments"]:
        end = s["rec"] + s["n"]
        items.append({"id": f"v{s['id']}", "track": "V1", "src": s["src"], "start": s["rec"], "end": end,
                      "in": fr(s["in"]), "piece": s["id"], "clips": [s["clip"], *s.get("also", [])], **_look(s),
                      **({"words": [s["kept"][0], s["kept"][-1]]} if s["kept"] else {})})
        if srcs[s["src"]]["audio"]:
            items.append({"id": f"a{s['id']}", "track": "A3" if "tone" in s else "A1", "src": s["src"],
                          "start": s["rec"], "end": end, "in": fr(s.get("tone", s["in"])), "piece": s["id"],
                          "gain_db": round(s.get("gain_db", 0) + gain, 2), "link": f"v{s['id']}"})
    for o in tl.get("overlays", []):
        items.append({"id": f"o_{o['id']}", "track": "V2", "src": o["src"], "start": o["rec"], "end": o["rec"] + o["n"],
                      "in": fr(o["in"]), "overlay": o["id"], **_look(o)})
        if o.get("gain_db") is not None and srcs[o["src"]]["audio"]:
            items.append({"id": f"oa_{o['id']}", "track": "A2", "src": o["src"], "start": o["rec"],
                          "end": o["rec"] + o["n"], "in": fr(o["in"]), "overlay": o["id"],
                          "gain_db": round(o["gain_db"] + gain, 2), "link": f"o_{o['id']}"})
    for t in _texts(p, tl, place, p.out / "resolve" / f"texts.v{v:03d}"):
        a, n = round(t["a"] * F), round((t["z"] - t["a"]) * F)
        if t.get("png"):
            items.append({"id": f"b_{t['text']}", "track": "V3", "png": t.pop("png"), "start": a, "end": a + n, "in": 0,
                          "text": t["text"], "box": True})
        items.append({"id": f"t_{t['text']}", "track": "V4", "start": a, "end": a + n, "in": 0, "title": t["words"],
                      **{k: v for k, v in t.items() if k not in ("a", "z", "words")}})
    markers = [{"frame": s["rec"], "kind": "section", "name": f"{s['id']} {s['title']}"} for s in tl["sections"]]
    markers += [{"frame": m["rec"], "kind": m["kind"], "name": m["kind"], "note": m["note"]} for m in tl["markers"]]
    first = {}
    for s in tl["segments"]:
        for c in [s["clip"], *s.get("also", [])]:
            first.setdefault(c, s["rec"])
            first[c + "#end"] = s["rec"] + s["n"]
    for c in edit.get("clips", []):
        if c.get("conf", 1) < 0.7 and c["id"] in first:
            markers.append({"frame": first[c["id"]], "kind": "uncertain", "name": f"check {c['id']}",
                            "note": f"conf {c['conf']}: {c.get('why', '')}"})
    for c, why in tl.get("justified", {}).items():
        if c + "#end" in first:
            markers.append({"frame": first[c + "#end"] - 1, "kind": "abrupt", "name": f"tight cut {c}", "note": why})
    for i in (read_json(d / f"qa.v{v:03d}.json", {}) or {}).get("issues", []):
        if i["severity"] in ("fail", "warn") and i.get("check") not in ("captions", None):  # None: caption pace notes
            markers.append({"frame": round(i["t"] * F), "kind": "qa", "name": f"QA {i['severity']}: {i['check']}",
                            "note": i["msg"]})
    markers = sorted((m for m in markers if 0 <= m["frame"] < tl["frames"]), key=lambda m: m["frame"])
    srt = d / f"captions.v{v:03d}.srt"
    return {"name": f"{p.name} v{v:03d}", "video": p.name, "v": v, "fps": F, "size": tl["size"], "frames": tl["frames"],
            "sources": srcs, "items": items, "markers": markers, "srt": str(srt) if srt.exists() else None,
            "loud_gain": gain}


def _pos(cx: float, cy: float, W: int, H: int) -> str:
    """FCPXML position: from the frame centre, y up, in percent of the frame height."""
    return f"{(cx - W / 2) / H * 100:.4g} {(H / 2 - cy) / H * 100:.4g}"


def write_fcpxml(pl: dict, path: Path) -> Path:
    F = pl["fps"]
    fd = _secs(Fraction(1) / F)
    W, H = pl["size"]
    t = lambda frames: _secs(Fraction(frames) / F)
    res = [f'<format id="r0" frameDuration="{fd}" width="{W}" height="{H}" colorSpace="1-1-1 (Rec. 709)"/>',
           '<effect id="rT" name="Basic Title" uid=".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/'
           'Basic Title.moti"/>']
    used = {i["src"] for i in pl["items"] if "src" in i}
    tone = {i["src"] for i in pl["items"] if i["track"] == "A3"}
    for sid, s in pl["sources"].items():
        if sid not in used:
            continue
        common = (f'start="{_secs(s["tc"])}" duration="{_secs(Fraction(s["dur"]).limit_denominator(100000))}" '
                  f'audioSources="1" audioChannels="{s["channels"]}" audioRate="48000"')
        src = f'<media-rep kind="original-media" src="{escape(_url(s["path"]))}"/>'
        res.append(f'<format id="f_{sid}" frameDuration="{_secs(Fraction(1) / s["fps"])}" width="{s["w"] or W}" '
                   f'height="{s["h"] or H}"/>')
        res.append(f'<asset id="a_{sid}" name="{escape(Path(s["path"]).name)}" {common} hasVideo="{1 if s["w"] else 0}" '
                   f'format="f_{sid}" hasAudio="{1 if s["audio"] else 0}">{src}</asset>')
        if sid in tone:  # room tone: the same file as sound only, so Resolve makes no video item for it
            res.append(f'<asset id="s_{sid}" name="{escape(Path(s["path"]).name)} (sound)" {common} hasVideo="0" '
                       f'hasAudio="1">{src}</asset>')
    for i in pl["items"]:
        if i.get("png"):
            res.append(f'<asset id="p_{i["text"]}" name="{escape(Path(i["png"]).name)}" start="0s" duration="0s" '
                       f'hasVideo="1" format="r0"><media-rep kind="original-media" src="{escape(_url(i["png"]))}"/></asset>')
    src_t = lambda sid, frames: _secs(pl["sources"][sid]["tc"] + Fraction(frames) / F)
    a_of = {i["piece"]: i for i in pl["items"] if i["track"] in ("A1", "A3") and "piece" in i}
    v1 = sorted((i for i in pl["items"] if i["track"] == "V1"), key=lambda i: i["start"])
    under = lambda i: next((v for v in v1 if v["start"] <= i["start"] < v["end"]), v1[-1])
    spine = []
    for v in v1:
        a = a_of.get(v["piece"])
        own = a is not None and a["track"] == "A1"
        local = lambda frames: _secs(pl["sources"][v["src"]]["tc"] + Fraction(v["in"] + frames - v["start"]) / F)
        name = escape(f"{','.join(v['clips'])} #{v['piece']}")
        el = [f'<asset-clip ref="a_{v["src"]}" offset="{t(v["start"])}" name="{name}" start="{src_t(v["src"], v["in"])}" '
              f'duration="{t(v["end"] - v["start"])}" tcFormat="NDF"{"" if own else " srcEnable=\"video\""}>']
        if own:
            el.append(f'<adjust-volume amount="{a["gain_db"]:+.2f}dB"/>')
        src = pl["sources"][v["src"]]
        el.append(_transform(v, W, H, src["w"] or W, src["h"] or H))
        if v.get("speed"):  # a sped-up bridge: source time runs `speed` times faster than the timeline
            s0 = src["tc"] + Fraction(v["in"]) / F
            el.append(f'<timeMap><timept time="{_secs(s0)}" value="{_secs(s0)}" interp="linear"/><timept '
                      f'time="{_secs(s0 + Fraction(v["end"] - v["start"]) / F)}" value="{_secs(s0 + Fraction(v["end"] - v["start"]) * Fraction(v["speed"]).limit_denominator(100) / F)}" '
                      'interp="linear"/></timeMap>')
        if a is not None and a["track"] == "A3":
            el.append(f'<asset-clip ref="s_{a["src"]}" lane="{LANES["A3"]}" offset="{local(a["start"])}" name="room tone" '
                      f'start="{src_t(a["src"], a["in"])}" duration="{t(a["end"] - a["start"])}">'
                      f'<adjust-volume amount="{a["gain_db"]:+.2f}dB"/></asset-clip>')
        for i in pl["items"]:
            if i["track"] not in ("V2", "V3", "V4") or under(i) is not v:
                continue
            dur, lane = t(i["end"] - i["start"]), LANES[i["track"]]
            if i["track"] == "V2":
                snd = next((x for x in pl["items"] if x.get("link") == i["id"]), None)
                bs = pl["sources"][i["src"]]
                el.append(f'<asset-clip ref="a_{i["src"]}" lane="{lane}" offset="{local(i["start"])}" name="{escape(i["overlay"])}" '
                          f'start="{src_t(i["src"], i["in"])}" duration="{dur}"{"" if snd else " srcEnable=\"video\""}>'
                          + (f'<adjust-volume amount="{snd["gain_db"]:+.2f}dB"/>' if snd else "")
                          + _transform(i, W, H, bs["w"] or W, bs["h"] or H) + "</asset-clip>")
            elif i["track"] == "V3":
                el.append(f'<video ref="p_{i["text"]}" lane="{lane}" offset="{local(i["start"])}" name="{escape(i["text"])} box" '
                          f'start="0s" duration="{dur}"/>')
            else:
                words = escape(i["title"]).replace("\n", "&#10;")
                back = f' backgroundColor="{i["back"]}"' if i.get("back") else ""
                el.append(f'<title ref="rT" lane="{lane}" offset="{local(i["start"])}" name="{escape(i["text"])}" start="0s" '
                          f'duration="{dur}"><text><text-style ref="ts_{i["text"]}">{words}</text-style></text>'
                          f'<text-style-def id="ts_{i["text"]}"><text-style font="{escape(i["font"])}" fontSize="{i["size"]}" '
                          f'fontColor="{i["colour"]}" bold="{1 if i["bold"] else 0}" strokeColor="0 0 0 1" '
                          f'strokeWidth="{0 if i.get("back") else i["outline"]}"{back} alignment="center"/></text-style-def>'
                          f'<adjust-transform position="{_pos(i["cx"], i["cy"], W, H)}"/></title>')
        for m in pl["markers"]:
            if v["start"] <= m["frame"] < v["end"]:
                el.append(f'<marker start="{local(m["frame"])}" duration="{fd}" value="{escape(m["name"])}" '
                          f'note="{escape(m.get("note", ""))}"/>')
        el.append("</asset-clip>")
        spine.append("".join(el))
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.10"><resources>' + "".join(res)
        + f'</resources><library><event name="jianji"><project name="{escape(pl["name"])}"><sequence format="r0" '
        f'duration="{t(pl["frames"])}" tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k"><spine>'
        + "".join(spine) + "</spine></sequence></project></event></library></fcpxml>\n")
    return path


GUIDE = """# {name} in DaVinci Resolve

**In** (Resolve 21.2, free):
1. File > New Project: `jianji {video}`. Open it.
2. File > Import > Timeline… and pick
   `{fcpxml}`
   In the dialog: "Automatically set project settings" **on** ({w}x{h}, {fps} fps), "Automatically import source
   clips into media pool" **on**, "Use sizing information" **on** (text positions). Nothing else.
3. File > Import > Subtitle… and pick `{srt}`; drag it from the Media Pool onto the timeline at its first frame.
   The captions land on a subtitle track, editable.

The media is your original footage in `{footage}` (linked by path: drag any edge to recover material) and the
callout boxes in `{texts}`. Tracks: V1 A-roll, V2 B-roll, V3 callout boxes, V4 texts (editable titles), A1 the
A-roll's sound, A2 B-roll sound, A3 room tone. Markers: blue sections, yellow check this, red tight cuts, purple QA.
One-time, optional: Preferences > System > Media Storage, put `{work}` first (cache and gallery out of ~/Movies).

**Back** (after editing):
1. File > Export > Timeline… > "FCPXML 1.10", save as `{back}/{name}.fcpxml`.
2. If you edited captions: right-click the subtitle track > Export Subtitle… > SRT, save as `{back}/{name}.srt`.
3. Tell Claude, or run `jian pull {video}` then `jian diff {video}`.
"""


def export(p: Project, v: int | None = None) -> Path:
    v = v or version_of(p.latest_edit())
    out = p.path("resolve", "x").parent
    (out / "from-resolve").mkdir(exist_ok=True)
    pl = plan(p, v)
    f = write_fcpxml(pl, out / f"{pl['name']}.fcpxml")
    manifest = {k: pl[k] for k in ("name", "video", "v", "size", "frames", "srt", "loud_gain")}
    manifest.update(fps=str(pl["fps"]), items=pl["items"], markers=pl["markers"],
                    sources={k: {**s, "fps": str(s["fps"]), "tc": float(s["tc"])} for k, s in pl["sources"].items()})
    write_json(out / f"{pl['name']}.manifest.json", manifest)
    for sub in ("CacheClip", "Gallery", "ProjectMedia"):
        (WORKDIR.expanduser() / sub).mkdir(parents=True, exist_ok=True)
    W, H = pl["size"]
    (out / "IMPORT.md").write_text(GUIDE.format(name=pl["name"], video=p.name, fcpxml=f, w=W, h=H, fps=pl["fps"],
                                                srt=pl["srt"], footage=p.footage, texts=out / f"texts.v{v:03d}",
                                                work=WORKDIR.expanduser(), back=out / "from-resolve"))
    print(f"resolve {f}\n        guide: {out / 'IMPORT.md'}")
    return f


# ---- back from Resolve ----------------------------------------------------------------------------------------

STORY = {"asset-clip", "clip", "video", "audio", "title", "gap", "ref-clip", "sync-clip", "mc-clip", "transition"}


def read_fcpxml(path: Path) -> dict:
    """A timeline exported from Resolve (or written by jian) as {"fps", "size", "items", "markers", "odd"}: items in
    timeline frames, source in-points in frames, levels in dB, titles with their words, style and centre; "odd"
    lists what jian can't represent (speed, transforms, transitions, keyframed levels, compound clips)."""
    root = ET.parse(path).getroot()
    res = root.find("resources")
    fmts = {f.get("id"): f for f in res.iter("format")}
    assets = {}
    for a in res.iter("asset"):
        mr = a.find("media-rep")
        src = mr.get("src") if mr is not None else a.get("src")
        assets[a.get("id")] = {"file": _path(src) if src else None, "start": _t(a.get("start")),
                               "video": a.get("hasVideo") == "1", "audio": a.get("hasAudio") == "1"}
    seq = root.find(".//sequence")
    fmt = fmts[seq.get("format")]
    F = 1 / _t(fmt.get("frameDuration"))
    W, H = int(fmt.get("width")), int(fmt.get("height"))
    t0 = _t(seq.get("tcStart"))
    fr = lambda secs: round(secs * F)
    items, markers, odd = [], [], []

    def level(el):
        av = el.find("adjust-volume")
        if av is None:
            return None
        if av.find(".//keyframe") is not None:
            odd.append(f"level automation (keyframes) on '{el.get('name', '')}'")
        m = re.match(r"([-+\d.]+)\s*dB", av.get("amount", "0dB"))
        return float(m.group(1)) if m else 0.0

    def place(el, at: Fraction, lane: int):
        """el starts at timeline second `at` on `lane` (0: the spine)."""
        tag, dur = el.tag, _t(el.get("duration"))
        name = el.get("name", "")
        if tag in ("ref-clip", "sync-clip", "mc-clip"):
            odd.append(f"compound/multicam clip '{name}' at {float(at):.2f}s")
        look = {}
        tm = el.findall("timeMap/timept")
        if len(tm) >= 2:
            span = _t(tm[-1].get("time")) - _t(tm[0].get("time"))
            look["speed"] = round(float((_t(tm[-1].get("value")) - _t(tm[0].get("value"))) / span), 2) if span else 1.0
        tr = el.find("adjust-transform")
        if tag != "title" and tr is not None and (tr.get("scale", "1 1") not in ("1 1", "1") or
                                                 tr.get("position", "0 0") not in ("0 0",)):
            look["zoom"] = f"{tr.get('position', '0 0')}|{tr.get('scale', '1 1')}"
        if tag == "transition":
            odd.append(f"transition '{name}' at {float(at):.2f}s")
            return
        base = {"start": fr(at), "end": fr(at + dur), "name": name, "lane": lane, **look}
        if tag in ("asset-clip", "video", "audio", "clip"):
            ref, start = el.get("ref"), _t(el.get("start"))
            if tag == "clip":  # a container: its own source is the first video/audio inside it
                inner = next((c for c in el if c.tag in ("video", "audio", "asset-clip") and c.get("lane") is None), None)
                if inner is None:
                    return
                ref, start = inner.get("ref"), _t(inner.get("start")) + (start - _t(inner.get("offset")))
            a = assets.get(ref) or {}
            src_in = fr(start - a.get("start", 0))
            enable = el.get("srcEnable", "all")
            gain = level(el)
            if a.get("video") and tag != "audio" and enable != "audio":
                items.append({**base, "track": f"V{lane + 1}", "in": src_in, "file": a.get("file")})
            if a.get("audio") and tag != "video" and enable != "video":
                items.append({**base, "track": AUDIO.get(lane, f"A{lane + 1}"), "in": src_in, "file": a.get("file"),
                              **({"gain_db": gain} if gain is not None else {})})
        elif tag == "title":
            ts = el.find(".//text-style-def/text-style")
            words = "".join(x.text or "" for x in el.iter("text-style") if x.get("ref"))
            st = {} if ts is None else {"font": ts.get("font"), "size": float(ts.get("fontSize") or 0),
                                        "colour": ts.get("fontColor"), "back": ts.get("backgroundColor")}
            x, y = (float(v) for v in ((tr.get("position") if tr is not None else None) or "0 0").split())
            items.append({**base, "track": f"V{lane + 1}", "in": 0, "title": words, "file": None,
                          "cx": round(W / 2 + x * H / 100), "cy": round(H / 2 - y * H / 100), **st})
        for m in el.findall("marker") + el.findall("chapter-marker"):
            markers.append({"frame": fr(at + _t(m.get("start")) - _t(el.get("start"))), "name": m.get("value", ""),
                            "note": m.get("note", "")})
        for c in el:  # connected clips, timed in this clip's own (source) time
            if c.tag in STORY and c.get("lane") is not None:
                place(c, at + _t(c.get("offset")) - _t(el.get("start")), lane + int(c.get("lane")))

    for el in seq.find("spine"):
        if el.tag in STORY:
            place(el, _t(el.get("offset")) - t0, 0)
    seen, uniq = set(), []
    for it in items:  # one element can carry picture and sound: keep both, but never the same twice
        key = (it["track"], it["file"], it["start"], it["end"], it["in"], it.get("title"))
        if key not in seen:
            seen.add(key)
            uniq.append(it)
    return {"fps": F, "size": [W, H], "items": uniq, "markers": markers, "odd": odd}


def _norm(s: str | None) -> str:
    return " ".join((s or "").split())


def match(got: list[dict], man: dict) -> dict[int, dict]:
    """Each exported item -> the manifest item it came from (index -> item): same file and kind, the most overlap
    of source range, then of timeline range; a title by its name (the text id) or its words. Unmatched items are
    your additions."""
    src = {k: str(Path(s["path"]).resolve()) for k, s in man["sources"].items()}
    file_of = lambda m: str(Path(m["png"]).resolve()) if m.get("png") else src.get(m.get("src"))
    ov = lambda a0, a1, b0, b1: max(0, min(a1, b1) - max(a0, b0))
    pairs = []
    for gi, g in enumerate(got):
        for mi, m in enumerate(man["items"]):
            t = ov(g["start"], g["end"], m["start"], m["end"])
            if "title" in m or "title" in g:
                if "title" in m and "title" in g:
                    s = 1000 * (g["name"] == m["text"]) + 100 * (_norm(g["title"]) == _norm(m["title"]))
                    if s or t:
                        pairs.append((s, t, gi, mi))
                continue
            if m["track"][0] != g["track"][0] or not g.get("file"):
                continue
            if g["file"] != file_of(m) and Path(g["file"]).name != Path(file_of(m) or "?").name:
                continue
            n_g, n_m = g["end"] - g["start"], m["end"] - m["start"]
            s = n_m if m.get("png") else ov(g["in"], g["in"] + n_g, m["in"], m["in"] + n_m)
            if s > 0:
                pairs.append((s, t, gi, mi))
    out, used = {}, set()
    for _, _, gi, mi in sorted(pairs, key=lambda x: (-x[0], -x[1])):
        if gi not in out and mi not in used:
            out[gi] = man["items"][mi]
            used.add(mi)
    return out


def _zoom(m: dict, man: dict) -> str | None:
    """The zoom jian wrote for a manifest item, as read_fcpxml reports one: "position|scale"."""
    W, H = man["size"]
    src = man["sources"].get(m.get("src"), {})
    t = _transform(m, W, H, src.get("w") or W, src.get("h") or H)
    return "|".join(re.findall(r'"([^"]*)"', t)) if t else None


def changes(man: dict, got: dict) -> tuple[list[dict], dict[int, dict]]:
    """What you changed, item by item: edges extended or trimmed (source frames recovered or dropped), B-roll and
    texts moved or retimed against the A-roll under them, a text's words, place and look, levels, deletions,
    additions, reorders, markers, and what jian can't represent ("unsupported": render that one from Resolve)."""
    F = Fraction(man["fps"])
    H = man["size"][1]
    items = got["items"]
    m_of = match(items, man)
    s = lambda frames: round(float(frames / F), 3)
    out = [{"kind": "unsupported", "what": w} for w in got["odd"]]
    by_id = {m["id"]: m for m in man["items"]}
    g_of = {m["id"]: gi for gi, m in m_of.items()}
    v1 = [m for m in man["items"] if m["track"] == "V1"]
    anchor = lambda frame: next((x for x in v1 if x["start"] <= frame < x["end"]), None)
    for gi, g in enumerate(items):
        m = m_of.get(gi)
        if m is None:
            if "title" in g:
                out.append({"kind": "insert", "track": "title", "file": g["title"], "at": s(g["start"]),
                            "dur": s(g["end"] - g["start"]), "in": 0})
            elif g["track"][0] == "V" or not any(x["track"][0] == "V" and x["file"] == g["file"] and x["start"] == g["start"]
                                                 for x in items):
                out.append({"kind": "insert", "track": g["track"], "file": Path(g["file"] or "?").name, "at": s(g["start"]),
                            "dur": s(g["end"] - g["start"]), "in": s(g["in"])})
            continue
        if m.get("box"):
            continue  # a callout's box follows its title
        if g.get("zoom") != _zoom(m, man) and m["track"][0] == "V":
            out.append({"kind": "unsupported", "what": f"zoom/position changed on {m.get('overlay') or m['id']}"})
        if round(float(g.get("speed", 1)), 1) != round(float(m.get("speed", 1)), 1) and m["track"][0] == "V":
            out.append({"kind": "unsupported", "what": f"speed changed on {m.get('overlay') or m['id']}"})
        n_g, n_m = g["end"] - g["start"], m["end"] - m["start"]
        who = m.get("text") or m.get("overlay") or ",".join(by_id.get(m.get("link"), m).get("clips", [m["id"]]))
        if m["track"] in ("V1", "V2"):
            if g["in"] != m["in"]:
                out.append({"kind": "head", "track": m["track"], "item": m["id"], "who": who,
                            "by": s(m["in"] - g["in"])})  # > 0: extended earlier into the source
            if g["in"] + n_g != m["in"] + n_m:
                out.append({"kind": "tail", "track": m["track"], "item": m["id"], "who": who,
                            "by": s(g["in"] + n_g - m["in"] - n_m)})  # > 0: extended later
        if m["track"] in ("V2", "V4"):  # against the A-roll piece it sits on
            am = anchor(m["start"])
            ag = items[g_of[am["id"]]] if am and am["id"] in g_of else None
            if ag is not None and (g["start"] - ag["start"]) != (m["start"] - am["start"]):
                out.append({"kind": "move", "track": m["track"], "item": m["id"], "who": who,
                            "by": s((g["start"] - ag["start"]) - (m["start"] - am["start"]))})
        if "title" in m:
            if n_g != n_m:
                out.append({"kind": "retime", "track": "V4", "item": m["id"], "who": who, "by": s(n_g - n_m)})
            if _norm(g["title"]) != _norm(m["title"]):
                out.append({"kind": "words", "item": m["id"], "who": who, "was": _norm(m["title"]), "now": _norm(g["title"])})
            if abs(g["cx"] - m["cx"]) > 0.01 * H or abs(g["cy"] - m["cy"]) > 0.01 * H:
                out.append({"kind": "place", "item": m["id"], "who": who, "dx": g["cx"] - m["cx"], "dy": g["cy"] - m["cy"]})
            look = {k: g[k] for k in ("font", "size", "colour") if g.get(k) not in (None, m.get(k)) and
                    not (k == "size" and abs(float(g[k]) - m[k]) < 1)}
            if look:
                out.append({"kind": "look", "item": m["id"], "who": who, **look})
        if m["track"][0] == "A":
            if "gain_db" in g and abs(g["gain_db"] - m.get("gain_db", 0)) >= 0.1:
                out.append({"kind": "level", "track": m["track"], "item": m["id"], "who": who,
                            "db": round(g["gain_db"] - m.get("gain_db", 0), 1)})
            link = by_id.get(m.get("link"))
            lg = items[g_of[link["id"]]] if link and link["id"] in g_of else None
            if lg is not None and (lg["start"], lg["end"]) != (g["start"], g["end"]) and \
                    (m["start"], m["end"]) == (link["start"], link["end"]):
                out.append({"kind": "unsupported", "what": f"split edit (J/L cut) on {who}: sound and picture edges differ"})
    for m in man["items"]:
        if m["id"] not in g_of and not m.get("box"):
            out.append({"kind": "delete", "track": m["track"], "item": m["id"],
                        "who": m.get("text") or m.get("overlay") or ",".join(m.get("clips", [m["id"]]))})
    order = [m_of[gi]["id"] for gi, g in sorted(enumerate(items), key=lambda x: x[1]["start"])
             if gi in m_of and m_of[gi]["track"] == "V1"]
    if order != [m["id"] for m in v1 if m["id"] in order]:
        out.append({"kind": "reorder", "track": "V1", "order": order})
    known = [(m["frame"], m["name"]) for m in man["markers"]]
    for mk in got["markers"]:
        if not any(abs(mk["frame"] - f) <= 1 and n == mk["name"] for f, n in known):
            out.append({"kind": "marker", "at": s(mk["frame"]), "note": " ".join(x for x in (mk["name"], mk["note"]) if x)})
    return out, m_of


def build_edit(base: dict, man: dict, got: dict, m_of: dict[int, dict], ch: list[dict], placement: dict) -> dict:
    """The next edit version from your timeline, at exactly your frames, so a jian render matches what you saw in
    Resolve. What you changed is "locked": true (your call: kept by every revision, never judged by QA); the rest
    is "pinned" (jian's own cut as it was: same frames, its word range kept, so a revision may re-cut it)."""
    F = Fraction(man["fps"])
    sec = lambda frames: round(float(Fraction(frames) / F), 4)
    items = got["items"]
    sid_of = {str(Path(s["path"]).resolve()): k for k, s in man["sources"].items()}
    sid_of.update({Path(s["path"]).name: k for k, s in man["sources"].items()})
    sid = lambda g: sid_of.get(g.get("file")) or sid_of.get(Path(g.get("file") or "?").name)
    touched = {c["item"] for c in ch if "item" in c}
    g_of = {m["id"]: gi for gi, m in m_of.items()}
    linked = lambda m: {x["id"] for x in man["items"] if m and x.get("link") == m["id"]}
    sound = lambda m: next((items[g_of[x]] for x in linked(m) if x in g_of), None)
    mine = lambda m: not m or m["id"] in touched or bool(linked(m) & touched)
    base_clip = {c["id"]: c for c in base.get("clips", [])}
    n_pieces = lambda key: sum(key in x.get("clips", []) for x in man["items"] if x["track"] == "V1")
    clips, start_of, count, sec_now, owner = [], {}, {}, None, {}
    v1 = sorted((gi for gi, g in enumerate(items) if g["track"] == "V1" and sid(g)), key=lambda gi: items[gi]["start"])
    for gi in v1:
        g, m = items[gi], m_of.get(gi)
        cl = m["clips"] if m else []
        key = cl[0] if cl else "added"
        count[key] = count.get(key, 0) + 1
        cid = key if m and n_pieces(key) == 1 else f"{key}.{count[key]}"
        b = base_clip.get(key, {})
        sec_now = b.get("sec", sec_now)
        n = g["end"] - g["start"]
        c = {"id": cid, "src": sid(g), "in": sec(g["in"]), "out": sec(g["in"] + n), "sec": sec_now,
             **({"speech": True} if "from" in b or not m else {})}  # its words become captions; natural sound doesn't
        if mine(m):
            c.update(locked=True, why="your cut in Resolve" + (f" (was {', '.join(cl)})" if cl else " (added)"))
        else:
            c.update(pinned=True, why=b.get("why", ""), **({"words": m["words"]} if m.get("words") else {}))
            c.update({k: b[k] for k in ("conf", "keep", "shot") if k in b})
            if "abrupt" in b and count[key] == n_pieces(key):  # the tight cut is where the clip ends
                c["abrupt"] = b["abrupt"]
        snd = sound(m) if m else next((x for x in items if x["track"][0] == "A" and x["file"] == g["file"]
                                        and x["start"] == g["start"]), None)
        if snd is not None:
            if snd["in"] != g["in"] or (m and any(by["track"] == "A3" for by in man["items"] if by.get("link") == m["id"])):
                c["tone"] = sec(snd["in"])  # room tone under the picture going on
            if "gain_db" in snd and abs(snd["gain_db"] - man["loud_gain"]) >= 0.01:
                c["gain_db"] = round(snd["gain_db"] - man["loud_gain"], 2)
        fix = {}
        for x in cl:
            fix.update(base_clip.get(x, {}).get("fix", {}))
        if fix:
            c["fix"] = fix
        clips.append(c)
        start_of[cid] = g["start"]
        owner[gi] = cid
    under = lambda frame: next((owner[gi] for gi in v1 if items[gi]["start"] <= frame < items[gi]["end"]), clips[0]["id"])
    anchor = lambda frame: {"over": under(frame), "at": sec(frame - start_of[under(frame)])}
    mark = lambda m: {"locked": True} if mine(m) else {"pinned": True}
    overlays, base_ov = [], {o["id"]: o for o in base.get("overlays", [])}
    for gi, g in sorted(((gi, g) for gi, g in enumerate(items) if g["track"] == "V2" and sid(g)), key=lambda x: x[1]["start"]):
        m = m_of.get(gi)
        b = base_ov.get(m["overlay"], {}) if m else {}
        n = g["end"] - g["start"]
        o = {"id": m["overlay"] if m else f"added_b{gi}", "src": sid(g), "in": sec(g["in"]), "out": sec(g["in"] + n),
             **anchor(g["start"]), **mark(m), **{k: b[k] for k in ("why", "shot") if k in b}}
        snd = sound(m)
        if snd is not None and "gain_db" in snd:
            o["gain_db"] = round(snd["gain_db"] - man["loud_gain"], 2)
        elif b.get("gain_db") is not None:
            o["gain_db"] = b["gain_db"]
        overlays.append(o)
    texts, base_tx = [], {t["id"]: t for t in base.get("texts", [])}
    for gi, g in sorted(((gi, g) for gi, g in enumerate(items) if "title" in g), key=lambda x: x[1]["start"]):
        m = m_of.get(gi)
        if not m:
            continue
        t = base_tx[m["text"]]
        new = {k: v for k, v in t.items() if k not in ("word", "at", "over", "dur", "place")}
        new.update(**anchor(g["start"]), dur=sec(g["end"] - g["start"]), **mark(m))
        pl = (placement.get("texts") or {}).get(t["id"]) or {}
        mv = next((c for c in ch if c["kind"] == "place" and c["item"] == m["id"]), None)
        if pl:
            new["place"] = {"cx": round(pl["cx"] + (mv["dx"] if mv else 0)), "top": round(pl["top"] + (mv["dy"] if mv else 0))}
        if _norm(g["title"]) != _norm(m["title"]):
            new["text"] = g["title"]
        look = next((c for c in ch if c["kind"] == "look" and c["item"] == m["id"]), None)
        if look:
            new["look"] = {k: look[k] for k in ("font", "size", "colour") if k in look}
        texts.append(new)
    markers = [x for x in base.get("markers", []) if x.get("at") in start_of]
    for c in ch:
        if c["kind"] == "marker":
            markers.append({"at": under(round(c["at"] * F)), "kind": "note", "note": c["note"], "by": "you"})
    return {**base, "clips": clips, "overlays": overlays, "texts": texts, "markers": markers}


def pull(p: Project, file: str | None) -> Path:
    """Your timeline back from Resolve (the newest .fcpxml in jianji/resolve/from-resolve/, and its .srt if you
    edited captions) as the next edit version; what you changed goes to jianji/diffs/vNNN.json."""
    back = p.out / "resolve" / "from-resolve"
    if file:
        path = Path(file).expanduser()
    else:
        cands = sorted(back.glob("*.fcpxml"), key=lambda f: f.stat().st_mtime)
        path = cands[-1] if cands else die(f"no .fcpxml in {back}: File > Export > Timeline > FCPXML 1.10 there first")
    got = read_fcpxml(path)
    m = re.search(r"v(\d{3})", path.stem)
    mans = sorted((p.out / "resolve").glob("* v[0-9][0-9][0-9].manifest.json"),
                  key=lambda f: int(re.search(r" v(\d{3})\.manifest", f.name).group(1)))
    man_path = next((f for f in mans if m and f"v{m.group(1)}." in f.name), mans[-1] if mans else None)
    man = read_json(man_path) if man_path else die("no manifest: run `jian resolve` first")
    ch, m_of = changes(man, got)
    base = read_json(p.out / "edits" / f"edit.v{man['v']:03d}.json") or die(f"no edit v{man['v']:03d}")
    placement = read_json(p.out / "renders" / f"placement.v{man['v']:03d}.json", {})
    new = build_edit(base, man, got, m_of, ch, placement)
    srt = path.with_suffix(".srt")
    if srt.exists():
        from .qa import parse_srt
        ours = parse_srt(Path(man["srt"])) if man.get("srt") else []
        edited = parse_srt(srt)
        if [(round(a, 2), round(b, 2), t) for a, b, t in edited] != [(round(a, 2), round(b, 2), t) for a, b, t in ours]:
            new["captions"] = [{"a": round(a, 3), "z": round(b, 3), "text": t} for a, b, t in edited]
            ch.append({"kind": "captions", "cues": len(edited), "was": len(ours)})
    v = version_of(p.latest_edit()) + 1
    new["v"] = v
    new["pulled"] = {"from": str(path), "base": man["v"], "by": "you",
                     "unsupported": [c["what"] for c in ch if c["kind"] == "unsupported"]}
    write_json(p.out / "edits" / f"edit.v{v:03d}.json", new)
    write_json(p.path("diffs", f"v{v:03d}.json"), {"base": man["v"], "pulled": v, "from": str(path), "changes": ch})
    with p.path("diffs", "style.jsonl").open("a") as f:  # every change you ever made: material for your style card
        for c in ch:
            f.write(json.dumps({"video": p.name, "v": v, **c}, ensure_ascii=False) + "\n")
    print(f"edit v{v:03d} from {path.name} (base v{man['v']:03d}): {len(ch)} changes")
    print(describe(ch))
    if new["pulled"]["unsupported"]:
        print("Resolve has things jian can't render: render the final from Resolve, or undo them there.")
    return p.out / "edits" / f"edit.v{v:03d}.json"


def describe(ch: list[dict]) -> str:
    say = {"head": lambda c: f"{c['who']}: start {'extended' if c['by'] > 0 else 'trimmed'} {abs(c['by']):.2f}s",
           "tail": lambda c: f"{c['who']}: end {'extended' if c['by'] > 0 else 'trimmed'} {abs(c['by']):.2f}s",
           "move": lambda c: f"{c['track']} {c['who']} moved {c['by']:+.2f}s",
           "retime": lambda c: f"text {c['who']} {'longer' if c['by'] > 0 else 'shorter'} by {abs(c['by']):.2f}s",
           "words": lambda c: f"text {c['who']} reworded: {c['was']!r} -> {c['now']!r}",
           "place": lambda c: f"text {c['who']} moved in the frame ({c['dx']:+d}, {c['dy']:+d} px)",
           "look": lambda c: f"text {c['who']} restyled: " + ", ".join(f"{k} {c[k]}" for k in ("font", "size", "colour") if k in c),
           "level": lambda c: f"{c['track']} {c['who']} level {c['db']:+.1f} dB",
           "delete": lambda c: f"{c['track']} {c['who']} deleted",
           "insert": lambda c: f"{c['track']} added {c['file']} {c['in']:.2f}s, {c['dur']:.2f}s at {c['at']:.2f}s",
           "reorder": lambda c: "A-roll pieces reordered",
           "marker": lambda c: f"marker at {c['at']:.2f}s: {c['note']}",
           "captions": lambda c: f"captions edited ({c['cues']} cues, were {c['was']})",
           "unsupported": lambda c: f"can't render in jian: {c['what']}"}
    return "\n".join(f"  - {say[c['kind']](c)}" for c in ch) or "  (no changes)"


def show_diff(p: Project, v: int | None):
    d = p.out / "diffs"
    files = sorted(d.glob("v[0-9][0-9][0-9].json")) if d.exists() else []
    f = d / f"v{v:03d}.json" if v else (files[-1] if files else None)
    rec = read_json(f) if f else None
    if not rec:
        die("no pulled version yet: `jian pull` first")
    print(f"v{rec['pulled']:03d} vs v{rec['base']:03d} ({Path(rec['from']).name}):")
    print(describe(rec["changes"]))
