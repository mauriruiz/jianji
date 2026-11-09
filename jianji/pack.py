"""The model's reading views: transcript.md (phrase lines with word ids) and catalog.md."""
from __future__ import annotations

from fractions import Fraction

def mmss(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


LEGEND = """Line: [first-last | start-end] text. Word ids are per source; edits reference them (`jian words` shows every id).
Marks: {um} hesitation filler, {voiced 0.4} untranscribed vocal sound, (es)/(en) language switch, · 2.1s long pause,
<<RETAKE drops a-b>> <<MARK a-b>> <<NOTE: ...>> shooting cues (cue words themselves are removed), [? "text" why] audio Whisper heard but we dropped.
"""


def phrases(words: list[dict], gap: float = 0.5) -> list[list[dict]]:
    out = []
    for w in words:
        if w.get("cue"):
            continue
        prev = out[-1][-1] if out else None
        if prev and w["s"] - prev["e"] < gap and w["lang"] == prev["lang"] and w["i"] == prev["i"] + 1:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def source_lines(tr: dict) -> list[str]:
    words = tr["words"]
    voiced = {round(a, 2): b - a for a, b in tr.get("voiced", [])}
    events = []  # (time, line)
    lang = None
    for ph in phrases(words):
        parts = []
        for w in ph:
            parts.append(f"{{{w['t']}}}" if w.get("filler") else w["t"])
            g = next((d for a, d in voiced.items() if abs(a - w["e"]) < 0.011), None)
            if g and w is not ph[-1]:
                parts.append(f"{{voiced {g:.1f}}}")
        tag = f"({ph[0]['lang']}) " if lang and ph[0]["lang"] != lang else ""
        lang = ph[0]["lang"]
        events.append((ph[0]["s"], f"[{ph[0]['i']}-{ph[-1]['i']} | {ph[0]['s']:.2f}-{ph[-1]['e']:.2f}] {tag}{' '.join(parts)}"))
    for c in tr.get("cues", []):
        t = words[c["cue"][0]]["s"]
        if c["type"] == "retake":
            rng = f" drops {c['drop'][0]}-{c['drop'][1]}" if "drop" in c else ""
            events.append((t, f"  <<RETAKE{rng}>>"))
        elif c["type"] == "mark":
            rng = f" {c['keep'][0]}-{c['keep'][1]}" if "keep" in c else ""
            events.append((t, f"  <<MARK{rng}>>"))
        else:
            events.append((t, f"  <<NOTE: {c.get('text', '')}>>"))
    for d in tr.get("dropped", []):
        events.append((d["s"], f"  [? \"{d['t']}\" {d['why']} {d['s']:.2f}-{d['e']:.2f}]"))
    events.sort(key=lambda x: x[0])
    lines, last_end = [], None
    for t, line in events:
        if last_end is not None and t - last_end >= 1.5 and line.startswith("["):
            lines.append(f"  · {t - last_end:.1f}s")
        lines.append(line)
        if line.startswith("["):
            last_end = float(line.split("|")[1].split("]")[0].split("-")[1])
    return lines


def lang_mix(tr: dict) -> str:
    tot = sum(tr.get("langs", {}).values()) or 1
    return " ".join(f"{k} {round(100 * v / tot)}%" for k, v in tr.get("langs", {}).items() if v)


def describe(e: dict) -> str:
    if e["kind"] == "audio":
        return f"audio {e.get('channels', '?')}ch"
    return f"{e['w']}x{e['h']}@{float(Fraction(e['fps'])):.3g}"


def transcript_md(name: str, cat: dict, trs: dict) -> str:
    out = [f"# Transcript: {name}", "", LEGEND]
    for sid, e in cat["sources"].items():
        tr = trs.get(sid)
        head = f"## {sid}  {e['file']}  {mmss(e['dur'])}  {describe(e)}"
        if not tr or len(tr["words"]) < 5:
            out += [head + "  no speech: B-roll / ambience candidate (see broll.md)", ""]
            continue
        out += [f"{head}  {lang_mix(tr)}  speech {round(100 * tr['speech_ratio'])}%", *source_lines(tr), ""]
    return "\n".join(out)


def catalog_md(name: str, cat: dict, trs: dict) -> str:
    rows = ["| id | file | dur | format | audio | words | notes |", "|---|---|---|---|---|---|---|"]
    for sid, e in cat["sources"].items():
        notes = []
        if e.get("vfr"):
            notes.append("VFR")
        if e.get("rot"):
            notes.append(f"rotated {e['rot']}")
        if e.get("transfer") in ("arib-std-b67", "smpte2084"):
            notes.append("HDR")
        if e.get("audio_streams", 0) > 1:
            notes.append(f"{e['audio_streams']} audio tracks (using 1st)")
        if e["kind"] == "video" and not e.get("audio_streams"):
            notes.append("no audio")
        tr = trs.get(sid) or {}
        rows.append(f"| {sid} | {e['file']} | {mmss(e['dur'])} | {describe(e)} | {e.get('audio_streams', 0)} | "
                    f"{len(tr.get('words', []))} | {', '.join(notes)} |")
    skipped = f"\nSkipped (not media): {', '.join(cat['skipped'])}\n" if cat.get("skipped") else ""
    return f"# Catalog: {name}\n\n" + "\n".join(rows) + "\n" + skipped


def words_view(tr: dict, a: int, b: int) -> str:
    ws = tr["words"][a:b + 1]
    out = []
    for w, nxt in zip(ws, ws[1:] + [None]):
        flag = "~" if w.get("filler") else "!" if w.get("cue") else ""
        out.append(f"{w['i']}:{w['t']}{flag}")
        if nxt and nxt["s"] - w["e"] >= 0.3:
            out.append(f"‖{nxt['s'] - w['e']:.1f}‖")
    return f"{ws[0]['s']:.2f}-{ws[-1]['e']:.2f}  " + " ".join(out) + "\n(~ filler, ! cue word, ‖0.6‖ pause seconds)"
