"""jian ingest: a finished recording becomes a dated video folder, and the recording itself is never touched."""
import os
import shutil
from pathlib import Path

import pytest

from jianji import cli, core
from jianji.core import Project
from jianji.media import build_catalog

G = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture
def rec(tmp_path, monkeypatch):
    f = tmp_path / "obs" / "2026-03-01 22-30-05.mp4"  # OBS's default name: the start time
    f.parent.mkdir()
    shutil.copy2(G / "footage" / "phone_broll.mp4", f)
    monkeypatch.setenv("JIANJI_VIDEOS_ROOT", str(tmp_path / "videos"))
    return f


def _same(f: Path, before: os.stat_result) -> bool:
    st = f.stat()
    return (st.st_size, st.st_mtime_ns, st.st_ino) == (before.st_size, before.st_mtime_ns, before.st_ino)


def test_ingest_links_the_recording_and_never_touches_it(rec, tmp_path):
    before = rec.stat()
    d = cli.ingest(str(rec))
    assert d == (tmp_path / "videos").resolve() / "2026-03-01-22-30-05"
    link = d / "footage" / rec.name
    assert link.is_symlink() and link.resolve() == rec.resolve()
    assert "profile: stream" in (d / "brief.md").read_text()
    cat = build_catalog(Project(str(d)))  # jian reads it through the link
    assert [e["file"] for e in cat["sources"].values()] == [rec.name]
    assert _same(rec, before) and sorted(x.name for x in rec.parent.iterdir()) == [rec.name]


def test_ingest_again_is_the_same_folder(rec):
    d = cli.ingest(str(rec))
    (d / "brief.md").write_text("mine now")
    assert cli.ingest(str(rec)) == d and (d / "brief.md").read_text() == "mine now"
    assert [x.name for x in (d / "footage").iterdir()] == [rec.name]


def test_ingest_takes_the_newest_recording_and_your_names(rec, tmp_path, monkeypatch):
    old = rec.with_name("2026-02-27 16-15-00.mp4")
    shutil.copy2(rec, old)
    os.utime(old, (1, 1))
    (rec.parent / "notes.txt").write_text("not a recording")
    monkeypatch.setattr(core, "global_config", lambda: {"schema": core.SCHEMA, "ingest": {
        "watch": str(rec.parent), "name": "{date}-live", "profile": "short"}})
    d = cli.ingest(None)
    assert d.name == "2026-03-01-live" and (d / "footage" / rec.name).is_symlink()
    assert "profile: short" in (d / "brief.md").read_text()


def test_ingest_refuses_a_folder_with_other_footage(rec):
    cli.ingest(str(rec), name="live")
    other = rec.with_name("other.mp4")
    shutil.copy2(rec, other)
    with pytest.raises(SystemExit, match="already exists"):
        cli.ingest(str(other), name="live")
    with pytest.raises(SystemExit, match="not a recording"):
        cli.ingest(str(rec.parent / "notes.txt"))
