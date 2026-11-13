"""The queue: a short cut out of an ingested recording is listed, approved into the upload folder as a Resolve project
on the original file, or rejected; nothing is rendered again, published or deleted. Renders 4 s once, ~6 s."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote

import pytest
from conftest import cut_short, quadrants
from test_stack import CONFIG, EDIT

from jianji import cli, core
from jianji import queue as q
from jianji.core import write_json

DUMP = ["sh", "-c", 'env | grep "^JIAN_" | sort > "$HOOK_OUT"']
SID = "2026-03-01-22-30-05:1"


@pytest.fixture(scope="module")
def stream(tmp_path_factory):
    base = tmp_path_factory.mktemp("queue")
    rec = quadrants(base / "obs" / "2026-03-01 22-30-05.mp4") if (base / "obs").mkdir() is None else None
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("JIANJI_VIDEOS_ROOT", str(base / "videos"))
        d = cli.ingest(str(rec))
    video, p = cut_short(d, CONFIG, {**EDIT, "title": "Why it's a u32: `rm -rf /`; $(boom)", "why": "said 'mark that'"})
    return base, rec, video, p


@pytest.fixture
def layer(stream, tmp_path, monkeypatch, capsys):
    base, rec, video, p = stream
    cfg = {"schema": core.SCHEMA, "videos_root": str(base / "videos"), "queue": {"upload": str(tmp_path / "To upload")},
           "hooks": {f"on_{e}": DUMP for e in ("queue_ready", "approve", "reject")}}
    for m in (core, q):
        monkeypatch.setattr(m, "global_config", lambda: cfg)
    monkeypatch.setenv("HOOK_OUT", str(tmp_path / "hook.txt"))
    (p.out / "state.json").unlink(missing_ok=True)
    write_json(p.out / "renders" / "qa.v001.json", {"status": "warn", "summary": "QA: warn", "checks": [], "issues": []})
    facts = lambda: dict(x.split("=", 1) for x in (tmp_path / "hook.txt").read_text().splitlines())
    listed = lambda *a: capsys.readouterr() and cli.main(["queue", "--json", *a]) or json.loads(capsys.readouterr().out)
    return tmp_path / "To upload", facts, listed


def test_queue_lists_what_waits_and_announces_it(stream, layer):
    base, rec, video, p = stream
    up, facts, listed = layer
    (it,) = listed("--announce")
    assert (it["id"], it["state"], it["qa"], it["seconds"]) == (SID, "pending", "warn", 4.0)
    assert it["title"].startswith("Why it's a u32") and Path(it["preview"]).exists()
    assert facts() == {"JIAN_EVENT": "queue_ready", "JIAN_COUNT": "1", "JIAN_VIDEOS": "2026-03-01-22-30-05"}
    assert q.protected() == [str(rec.resolve())]  # a candidate nobody judged yet still needs its recording


def test_status_says_where_every_short_stands(stream, layer, capsys):
    base, rec, video, p = stream
    up, facts, listed = layer
    (p.out.parent / "2").mkdir()  # a short someone started and never wrote
    stands = lambda: capsys.readouterr() and cli.main(["status", str(video.dir)]) or \
        next(x for x in capsys.readouterr().out.splitlines() if x.startswith("shorts: "))
    assert stands() == f"shorts: 1 pending, 2 empty  (jian status {video.dir.name} --short <n>)"
    write_json(p.out / "renders" / "qa.v001.json", {"status": "fail", "summary": "QA: fail", "checks": [], "issues": []})
    assert stands().startswith("shorts: 1 failed, 2 empty")
    (p.out / "renders" / "qa.v001.json").unlink()
    assert stands().startswith("shorts: 1 unchecked")  # cut with --no-qa
    (p.out.parent / "2").rmdir()


def test_approve_makes_a_resolve_project_on_the_original(stream, layer):
    base, rec, video, p = stream
    up, facts, listed = layer
    before = rec.stat()
    cli.main(["approve", SID])
    (dest,) = up.iterdir()
    assert dest.name == "2026-03-01-22-30-05-1 Why its a u32 rm -rf  boom"  # a title is text, never a path or a command
    names = sorted(x.name for x in dest.iterdir())
    fcpxml = next(x for x in dest.iterdir() if x.suffix == ".fcpxml")
    assert {"IMPORT.md", "preview.mp4", "title.txt", "texts", "from-resolve"} <= set(names)
    assert [x.suffix for x in dest.iterdir() if x.suffix in (".mp4", ".mov")] == [".mp4"]  # the preview: no final render
    srcs = [unquote(m.get("src")[7:]) for m in ET.parse(fcpxml).getroot().iter("media-rep")]
    assert str(rec.resolve()) in srcs and all(s == str(rec.resolve()) or s.startswith(str(dest)) for s in srcs), srcs
    assert all(Path(s).exists() for s in srcs) and (dest / fcpxml.with_suffix(".srt").name).exists()
    assert str(dest) in (dest / "IMPORT.md").read_text() and str(p.out) not in (dest / "IMPORT.md").read_text()
    assert facts() == {"JIAN_EVENT": "approve", "JIAN_ID": SID, "JIAN_VIDEO": "2026-03-01-22-30-05", "JIAN_SHORT": "1",
                       "JIAN_DIR": str(dest)}
    assert listed() == [] and [x["state"] for x in listed("--all")] == ["approved"]
    # waiting to be uploaded: its recording is protected, and no clean-up touches it
    assert q.protected() == [str(rec.resolve())]
    cli.main(["clean", str(video.dir)])
    st = rec.stat()
    assert (st.st_size, st.st_mtime_ns, st.st_ino) == (before.st_size, before.st_mtime_ns, before.st_ino)
    cli.main(["approve", SID])  # again: the same folder
    assert [x.name for x in up.iterdir()] == [dest.name]
    dest.rename(up.parent / "uploaded")  # moved out once published: the recording is free again
    assert q.protected() == []


def test_reject_deletes_nothing_and_a_new_cut_comes_back(stream, layer):
    base, rec, video, p = stream
    up, facts, listed = layer
    files = sorted(x for x in p.out.rglob("*") if x.is_file())
    cli.main(["reject", SID])
    assert facts() == {"JIAN_EVENT": "reject", "JIAN_ID": SID, "JIAN_VIDEO": "2026-03-01-22-30-05", "JIAN_SHORT": "1"}
    assert listed() == [] and sorted(x for x in p.out.rglob("*") if x.is_file()) == files + [p.out / "state.json"]
    assert not up.exists() and q.protected() == []
    write_json(p.out / "state.json", {"state": "rejected", "v": 0})  # judged at an older version
    assert [x["state"] for x in listed()] == ["pending"]


def test_requeue_undoes_a_yes_or_a_no_and_never_deletes_your_work(stream, layer, capsys):
    base, rec, video, p = stream
    up, facts, listed = layer
    undo = lambda: capsys.readouterr() and cli.main(["requeue", SID]) or capsys.readouterr().out
    with pytest.raises(SystemExit, match="is pending: there is no yes or no to undo"):
        cli.main(["requeue", SID])
    cli.main(["reject", SID])
    files = sorted(x for x in p.out.rglob("*") if x.is_file() and x.name != "state.json")
    assert undo() == f"{SID} is pending again\n" and [x["state"] for x in listed()] == ["pending"]
    assert sorted(x for x in p.out.rglob("*") if x.is_file()) == files  # a no taken back: nothing else moved
    cli.main(["approve", SID])
    (dest,) = up.iterdir()
    (dest / ".DS_Store").write_bytes(b"finder")  # you opened the folder: that isn't touching it
    out = undo()
    assert out.startswith(f"{SID} is pending again\n  removed {dest}") and not dest.exists() and up.is_dir()
    assert [x["state"] for x in listed()] == ["pending"] and q.protected() == [str(rec.resolve())]
    cli.main(["approve", SID])  # approved again, and this time you worked on it
    (dest / "from-resolve" / "mine.fcpxml").write_text("<fcpxml/>")
    (dest / "title.txt").write_text("My better title\n")
    before = sorted(x for x in dest.rglob("*") if x.is_file())
    out = undo()
    assert "kept" in out and "you added or changed from-resolve/mine.fcpxml, title.txt" in out and "Nothing was deleted" in out
    assert sorted(x for x in dest.rglob("*") if x.is_file()) == before and [x["state"] for x in listed()] == ["pending"]
    cli.main(["approve", SID])  # into the folder that stayed: jian rewrites its own files, and yours is still yours
    out = undo()
    assert "you added or changed from-resolve/mine.fcpxml." in out and (dest / "from-resolve" / "mine.fcpxml").exists()
    cli.main(["approve", SID])
    (dest / "from-resolve" / "mine.fcpxml").unlink()
    (dest / "preview.mp4").unlink()  # deleting a file isn't work to protect
    assert "removed" in undo() and not dest.exists()
    cli.main(["approve", SID])
    st = core.read_json(p.out / "state.json")
    write_json(p.out / "state.json", {k: v for k, v in st.items() if k != "files"})  # approved by a jian that kept no record
    assert "can't tell" in undo() and dest.exists()
    write_json(p.out / "renders" / "qa.v001.json", {"status": "fail", "summary": "QA: fail", "checks": [], "issues": []})
    cli.main(["approve", SID, "--force"])
    assert "is failed again" in undo()  # taken by hand, given back: it's the QA failure it was


def test_a_short_that_failed_qa_never_reaches_the_queue(stream, layer):
    base, rec, video, p = stream
    up, facts, listed = layer
    write_json(p.out / "renders" / "qa.v001.json", {"status": "fail", "summary": "QA: fail", "checks": [], "issues": []})
    assert listed("--announce") == [] and [x["state"] for x in listed("--all")] == ["failed"]
    assert not (up.parent / "hook.txt").exists()  # nothing waiting: nobody is told
    with pytest.raises(SystemExit, match="is failed"):
        cli.main(["approve", SID])
    with pytest.raises(SystemExit, match="no short"):
        cli.main(["approve", "../../etc:1"])
    cli.main(["approve", SID, "--force"])  # your call, by hand
    assert len(list(up.iterdir())) == 1
