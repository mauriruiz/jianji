"""Resolve round trip on the golden clip: the FCPXML puts the cut on the original footage and reads back unchanged;
edits made "in Resolve" (a cut extended 1 s, a text moved and reworded, a level raised) come back as exactly those
changes, and the pulled version renders to the timeline you left. jian's own writer stands in for Resolve here;
Resolve's real export was checked by hand on Resolve 21.2 (docs/ARCHITECTURE.md, Resolve)."""
import copy
import json
import shutil
from pathlib import Path

import pytest

from jianji.cli import _tr_path, compile_edit, load_sources
from jianji.core import Project
from jianji.media import build_catalog
from jianji.render import render
from jianji.resolve import changes, export, plan, pull, read_fcpxml, write_fcpxml

G = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture(scope="module")
def exported(tmp_path_factory):
    d = tmp_path_factory.mktemp("resolve")
    (d / "footage").mkdir()
    shutil.copy2(G / "footage" / "golden.mp4", d / "footage" / "golden.mp4")
    shutil.copy2(G / "footage" / "phone_broll.mp4", d / "footage" / "phone_broll.mp4")
    p = Project(str(d))
    cat = build_catalog(p)
    shutil.copy(G / "transcript.A.json", _tr_path(p, "A", cat["sources"]["A"]))
    (p.out / "edits").mkdir()
    edit = json.loads((G / "edit.v001.json").read_text())
    edit["texts"] = [{"id": "t1", "text": "Every service owns its data", "over": "c2", "at": 0.3},
                     {"id": "h1", "text": "Sagas", "style": "hi", "over": "c4", "at": 0.5}]
    (p.out / "edits" / "edit.v001.json").write_text(json.dumps(edit))
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    render(p, tl, cat, sources, v)
    export(p, 1)
    return p


def _man(p):
    return json.loads(next((p.out / "resolve").glob("*v001.manifest.json")).read_text())


def test_fcpxml_reads_back_unchanged(exported):
    p = exported
    man = _man(p)
    got = read_fcpxml(next((p.out / "resolve").glob("* v001.fcpxml")))
    ch, m_of = changes(man, got)
    assert ch == [] and len(m_of) == len(man["items"]), ch
    assert {i["track"] for i in got["items"]} == {i["track"] for i in man["items"]}  # no track shifted by a lane
    xml = next((p.out / "resolve").glob("* v001.fcpxml")).read_text()
    assert 'lane="-' not in xml  # a negative lane is a video track under the spine in Resolve: everything moved up
    assert xml.count("<title ") == 2 and "backgroundColor" in xml  # editable titles; the callout keeps its box colour
    box = Path(next(i for i in man["items"] if i.get("box"))["png"])
    assert box.exists()  # and its box alone as a PNG, in case Resolve drops the title background
    assert any(m["kind"] == "section" for m in man["markers"])


def test_a_title_resized_in_resolve_comes_back_in_burned_pixels(exported):
    man = _man(exported)
    got = read_fcpxml(next((exported.out / "resolve").glob("* v001.fcpxml")))
    m = next(i for i in man["items"] if i.get("text") == "t1" and "title" in i)
    assert m["size"] == pytest.approx(m["fs"] * 0.84 * 1080 / man["size"][1], abs=0.06)  # Resolve's units
    next(i for i in got["items"] if i.get("title") == m["title"])["size"] = m["size"] * 1.5
    ch, _ = changes(man, got)
    assert ch == [{"kind": "look", "item": m["id"], "who": "t1", "size": round(m["fs"] * 1.5, 1)}]


def _resolve_edits(p, v=1):
    """What you do in Resolve, on jian's own writer: extend the 2nd A-roll piece's end 1 s (ripple), move the
    story line 0.4 s later and reword it, raise the 3rd piece's level 3 dB."""
    pl = plan(p, v)
    mod = copy.deepcopy(pl)
    F = int(pl["fps"])
    v1 = sorted((i for i in mod["items"] if i["track"] == "V1"), key=lambda i: i["start"])
    target, old_end = v1[1], v1[1]["end"]
    for i in mod["items"]:
        if i["start"] >= old_end:
            i["start"] += F
            i["end"] += F
    for i in mod["items"]:
        if i.get("piece") == target["piece"] and i["track"] in ("V1", "A1", "A3"):
            i["end"] += F
    mod["frames"] += F
    t = next(i for i in mod["items"] if i.get("text") == "t1" and "title" in i)
    t["start"], t["end"], t["title"] = t["start"] + 10, t["end"] + 10, "Each service owns its data"
    a = next(i for i in mod["items"] if i["track"] == "A1" and i.get("piece") == v1[2]["piece"])
    a["gain_db"] += 3
    out = write_fcpxml(mod, p.out / "resolve" / "from-resolve" / f"{pl['name']}.fcpxml")
    return out, mod


def test_changes_in_resolve_come_back_and_render_the_same(exported):
    p = exported
    sim, mod = _resolve_edits(p)
    pull(p, str(sim))
    rec = json.loads((p.out / "diffs" / "v002.json").read_text())
    assert sorted(c["kind"] for c in rec["changes"]) == ["level", "move", "tail", "words"], rec["changes"]
    tail = next(c for c in rec["changes"] if c["kind"] == "tail")
    move = next(c for c in rec["changes"] if c["kind"] == "move")
    assert tail["by"] == 1.0 and move["by"] == 0.4, rec["changes"]
    edit = json.loads((p.out / "edits" / "edit.v002.json").read_text())
    assert sum(1 for c in edit["clips"] if c.get("locked")) == 2  # the extended piece and the louder one
    t1 = next(t for t in edit["texts"] if t["id"] == "t1")
    assert t1["locked"] is True and t1["text"] == "Each service owns its data"
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    assert v == 2 and tl["frames"] == mod["frames"]
    render(p, tl, cat, sources, v)
    again = plan(p, 2)
    key = lambda i: (i["track"], i.get("src") or Path(i.get("png", "")).name.replace(".v002", ".v001"), i["start"],
                     i["end"], i["in"], i.get("title"))
    pick = lambda pl: sorted(key(i) for i in pl["items"] if not i.get("box"))
    assert pick(again) == pick(mod)
    rel = lambda pl: sorted((key(i), round(i["gain_db"] - pl["loud_gain"], 1)) for i in pl["items"] if "gain_db" in i)
    assert rel(again) == rel(mod)  # the 3 dB you added, on that piece only


def test_resolve_fcpxml_dialect(tmp_path):
    """Resolve's own export: the timeline starts at 01:00:00:00, a clip can be a <clip> around a <video>, a gap
    can carry connected clips, titles have a position, transitions and keyframed levels aren't representable."""
    xml = """<?xml version="1.0"?><fcpxml version="1.10"><resources>
      <format id="r0" frameDuration="1/25s" width="1080" height="1920"/>
      <asset id="r1" start="0s" duration="100s" hasVideo="1" hasAudio="1"><media-rep kind="original-media" src="file:///x/a.mp4"/></asset>
      <effect id="r2" name="Basic Title"/></resources>
      <library><event><project name="t"><sequence format="r0" tcStart="3600s" duration="10s"><spine>
        <asset-clip ref="r1" offset="3600s" start="10s" duration="2s" name="one">
          <adjust-volume amount="-3dB"/>
          <title ref="r2" lane="3" offset="10s" duration="1s" name="t1"><text><text-style ref="s">Hola</text-style></text>
            <text-style-def id="s"><text-style font="Helvetica Neue" fontSize="88" fontColor="1 1 1 1"/></text-style-def>
            <adjust-transform position="0 40"/></title>
        </asset-clip>
        <transition offset="3601s" duration="2/25s" name="Cross Dissolve"/>
        <clip offset="3602s" start="50s" duration="2s" name="two"><video ref="r1" offset="50s" start="20s" duration="2s"/></clip>
        <gap offset="3604s" start="0s" duration="1s"><asset-clip ref="r1" lane="1" offset="0s" start="30s" duration="1s" name="b">
          <adjust-volume amount="0dB"><param name="amount"><keyframeAnimation><keyframe time="0s" value="-6dB"/></keyframeAnimation></param></adjust-volume>
        </asset-clip></gap>
      </spine></sequence></project></event></library></fcpxml>"""
    f = tmp_path / "r.fcpxml"
    f.write_text(xml)
    got = read_fcpxml(f)
    v = {(i["track"], i["start"], i["end"], i["in"]) for i in got["items"] if i["track"][0] == "V" and "title" not in i}
    assert v == {("V1", 0, 50, 250), ("V1", 50, 100, 500), ("V2", 100, 125, 750)}, v
    a1 = next(i for i in got["items"] if i["track"] == "A1")
    assert a1["gain_db"] == -3 and (a1["start"], a1["in"]) == (0, 250)
    t = next(i for i in got["items"] if "title" in i)
    assert t["title"] == "Hola" and t["track"] == "V4" and (t["start"], t["end"]) == (0, 25) and t["cy"] == 960 - 768
    assert any("transition" in o for o in got["odd"]) and any("keyframes" in o for o in got["odd"])


def test_fcpxml_survives_quotes_in_notes_and_texts(exported, tmp_path):
    """A QA privacy note quoting `"blur": [[...]]` left a raw quote in a marker's note and Resolve refused the
    whole file ("DOM parser error. Line 3")."""
    import xml.etree.ElementTree as ET
    pl = copy.deepcopy(plan(exported, 1))
    note = 'email on screen: \'a@example.com\': blur it, "blur": [[0.1, 0.2, 0.3, 0.4]] & <here>\x0c'
    pl["markers"].append({"frame": 0, "kind": "qa", "name": 'QA "fail"', "note": note})
    t = next(i for i in pl["items"] if "title" in i)
    t["title"] = 'Dijo "no"'
    ET.parse(write_fcpxml(pl, tmp_path / "q.fcpxml"))  # raises on malformed XML
    got = read_fcpxml(tmp_path / "q.fcpxml")
    assert any(i.get("title") == 'Dijo "no"' for i in got["items"])
