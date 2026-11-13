"""The queue: shorts cut out of your videos, waiting for you to say yes or no.

`jian queue` lists the candidates (every short under videos_root whose latest cut is rendered and passed QA),
`jian approve <id>` puts one in your upload folder as a DaVinci Resolve project on the original footage (timeline,
text boxes, captions, IMPORT.md, the preview), `jian reject <id>` drops it from the list, `jian requeue <id>` undoes
either. Nothing here renders a final file or publishes anything, and the only thing ever deleted is an upload folder
exactly as approve left it, when you take the approval back. A candidate's id is `<video>:<n>`; its state lives in
<video>/jianji/shorts/<n>/state.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from .core import LEGACY, WORK, Project, die, global_config, hook, read_json, version_of, write_json


def _root() -> Path:
    root = os.environ.get("JIANJI_VIDEOS_ROOT") or global_config().get("videos_root")
    return Path(root or die("no videos_root set (JIANJI_VIDEOS_ROOT or config.toml in your layer)")).expanduser().resolve()


def _upload() -> Path | None:
    up = global_config().get("queue", {}).get("upload")
    return Path(up).expanduser() if up else None


def look(sd: Path) -> dict | None:
    """One short's folder: its latest edit, where it stands and what there is to show for it. state: pending
    (rendered, QA pass or warn, not judged yet), approved, rejected, failed (QA failed: fix it or approve it by hand
    with --force), unchecked (cut with --no-qa) or unrendered. None: no edit yet."""
    edits = sorted(sd.glob("edits/edit.v[0-9][0-9][0-9].json"))
    if not edits:
        return None
    v = version_of(edits[-1])
    edit = read_json(edits[-1])
    tl = read_json(sd / "renders" / f"timeline.v{v:03d}.json") or {}
    qa = (read_json(sd / "renders" / f"qa.v{v:03d}.json") or {}).get("status")
    ready = (sd / "renders" / f"final.v{v:03d}.mp4").exists() and (sd / "preview.mp4").exists()
    st = read_json(sd / "state.json", {})
    state = st.get("state") if st.get("v") == v else None  # a new cut of a judged short is a candidate again
    state = state or ("unrendered" if not ready else "failed" if qa == "fail" else "pending" if qa else "unchecked")
    return {"short": sd.name, "v": v, "state": state, "title": " ".join(str(edit.get("title") or "").split())[:80],
            "why": str(edit.get("why") or "")[:300], "seconds": (tl.get("stats") or {}).get("seconds"), "qa": qa,
            "preview": str(sd / "preview.mp4"), "dir": str(sd),
            **({"dest": st["dest"]} if state == "approved" and st.get("dest") else {})}


def shorts() -> list[dict]:
    """Every short under videos_root (video folders one or two levels down), as `look` sees it."""
    root = _root()
    found = {e.parent for n in (WORK, LEGACY) for pat in (f"*/{n}/shorts/*/edits", f"*/*/{n}/shorts/*/edits")
             for e in root.glob(pat)}
    out = []
    for sd in found:
        it = look(sd)
        if it:
            video = str(sd.parents[2].relative_to(root))
            out.append({"id": f"{video}:{sd.name}", "video": video, **it})
    return sorted(out, key=lambda x: (x["video"], x["short"].zfill(6)))


def protected() -> list[str]:
    """Recordings a short still needs: the ones a timeline waiting in your upload folder points at, and those of
    candidates you haven't judged. Moving a folder out of the upload folder releases its recording. jian itself never
    deletes footage; anything of yours that does (a retention script for a full disk) asks here first."""
    paths = set()
    up = _upload()
    for m in (up.glob("*/*.manifest.json") if up and up.is_dir() else []):
        paths |= {s["path"] for s in (read_json(m) or {}).get("sources", {}).values()}
    for it in shorts():
        if it["state"] != "pending":
            continue
        sd = Path(it["dir"])
        video = sd.parents[2]
        footage = video / "footage" if (video / "footage").is_dir() else video
        paths |= {str((footage / e["file"]).resolve()) for e in (read_json(sd / "catalog.json") or {}).get("sources", {}).values()}
    return sorted(paths)


def queue(as_json: bool = False, everything: bool = False, announce: bool = False):
    items = shorts()
    pending = [x for x in items if x["state"] == "pending"]
    shown = items if everything else pending
    if as_json:
        print(json.dumps(shown, indent=1, ensure_ascii=False))
    else:
        for x in shown:
            secs = f"{x['seconds']:.0f}s" if x["seconds"] else "-"
            print(f"{x['id']}  {x['state']}  {secs}  QA {x['qa'] or '-'}  {x['title']}\n    {x.get('dest') or x['preview']}")
        print(f"{len(pending)} waiting" + ("" if everything or len(items) == len(pending) else f" ({len(items) - len(pending)} more with --all)"))
    if announce and pending:  # tell your hook there's something to look at; with nothing waiting, nobody is bothered
        hook("queue_ready", count=len(pending), videos=",".join(dict.fromkeys(x["video"] for x in pending)))


def _find(sid: str) -> dict:
    items = shorts()  # only ids the scan found: an id is never turned into a path
    return next((x for x in items if x["id"] == sid), None) or \
        die(f"no short '{sid}'; known: {', '.join(x['id'] for x in items) or 'none'}")


def _judge(it: dict, state: str, **more):
    write_json(Path(it["dir"]) / "state.json", {"state": state, "v": it["v"], "at": datetime.now().isoformat(timespec="seconds"), **more})


def approve(sid: str, force: bool = False) -> Path:
    """The short as a Resolve project in your upload folder: the timeline on the original footage, its text boxes and
    captions, IMPORT.md and the preview, in one folder you can open whenever. Never a final render: that's yours."""
    it = _find(sid)
    if it["state"] in ("unrendered", "failed", "unchecked") and not (force and it["state"] != "unrendered"):
        die(f"{sid} is {it['state']} (QA: {it['qa'] or 'not run'}): `jian cut {it['video']} --short {it['short']}` first"
            + ("" if it["state"] == "unrendered" else ", or --force to take it as it is"))
    up = _upload() or die("no upload folder: set `[queue] upload = \"<folder>\"` in your layer's config.toml")
    from .resolve import export
    slug = re.sub(r"[^\w -]+", "", it["title"]).strip()[:50]
    dest = Path(it["dest"]) if it.get("dest") and Path(it["dest"]).is_dir() else up / f"{Path(it['video']).name}-{it['short']} {slug}".strip()
    p = Project(str(_root() / it["video"]), short=it["short"])
    had = _touched(dest)  # a folder that was already there may hold files of yours
    export(p, it["v"], out=dest)
    shutil.copy2(it["preview"], dest / "preview.mp4")
    (dest / "title.txt").write_text(f"{it['title']}\n\n{it['why']}\n")
    wrote = {k: h for k, h in _files(dest).items() if had.get(k) != _touched(dest)[k]}  # what jian put there, just now:
    _judge(it, "approved", dest=str(dest), files=wrote)  # `requeue` removes the folder only while it holds nothing else
    hook("approve", id=sid, video=it["video"], short=it["short"], dir=dest)
    print(dest)  # last line: the folder, for scripts
    return dest


def reject(sid: str):
    """Out of the list. Nothing is deleted: the short stays in its folder, and a new cut of it is a candidate again."""
    it = _find(sid)
    _judge(it, "rejected")
    hook("reject", id=sid, video=it["video"], short=it["short"])
    print(f"{sid} rejected")


def _files(dest: Path) -> dict[str, str]:
    """What an upload folder holds, {path inside it: content hash}. Finder's own .DS_Store isn't yours or jian's."""
    return {str(f.relative_to(dest)): hashlib.sha1(f.read_bytes()).hexdigest()
            for f in sorted(dest.rglob("*")) if f.is_file() and f.name != ".DS_Store"}


def _touched(dest: Path) -> dict[str, int]:
    """{path inside the folder: when the file last changed in any way}: a file jian writes again moves on, even when
    it writes the same bytes; a file of yours it never opens doesn't."""
    return {str(f.relative_to(dest)): f.stat().st_ctime_ns for f in dest.rglob("*") if f.is_file()} if dest.is_dir() else {}


def requeue(sid: str):
    """Undo a yes or a no: the short waits in the queue again. If it was approved, its folder in your upload folder
    goes too, but only while it is exactly what approve wrote: a file you added or changed there (a note, a Resolve
    export) means the folder stays, whole, and jian says which files. Nothing else is touched."""
    it = _find(sid)
    if it["state"] not in ("approved", "rejected"):
        die(f"{sid} is {it['state']}: there is no yes or no to undo")
    sd = Path(it["dir"])
    st = read_json(sd / "state.json", {})
    dest, up, note = Path(st.get("dest") or ""), _upload(), ""
    if it["state"] == "approved" and st.get("dest") and dest.is_dir():
        now, then = _files(dest), st.get("files")
        yours = sorted(k for k in now if then is not None and then.get(k) != now[k])
        if then is None:  # approved before jian kept track of what it wrote
            note = f"\n  kept {dest}: this jian can't tell whether you changed it. Delete it yourself if you didn't"
        elif yours:
            note = f"\n  kept {dest}: you added or changed {', '.join(yours)}. Nothing was deleted"
        elif not up or dest.parent.resolve() != up.resolve():
            note = f"\n  kept {dest}: it isn't in your upload folder any more"
        else:
            shutil.rmtree(dest)
            note = f"\n  removed {dest} (untouched since you approved it)"
    (sd / "state.json").unlink()
    print(f"{sid} is {look(sd)['state']} again{note}")
