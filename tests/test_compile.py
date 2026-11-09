"""compile: word ids -> frames. Deterministic: frozen transcript + envelope, no models, no ffmpeg.
UPDATE_GOLDEN=1 pytest rewrites timeline.expected.json after an intended change."""
import copy
import json
import os
import tomllib
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from jianji.compile import EditError, compile_timeline, gaps

G = Path(__file__).parent / "fixtures" / "golden"
RUNON = Path(__file__).parent / "fixtures" / "runon"
CUT = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "explainer.toml").read_text())["cut"]


@pytest.fixture
def sources():
    tr = json.loads((G / "transcript.A.json").read_text())
    return {"A": {**tr, "dur": 42.92, "fps": "25/1", "w": 640, "h": 360, "env": np.load(G / "env.A.npy")},
            "B": {"words": [], "voiced": [], "cues": [], "dur": 8.0, "fps": "25/1", "w": 360, "h": 640,
                  "env": np.full(801, -60.0)}}


@pytest.fixture
def edit():
    return json.loads((G / "edit.v001.json").read_text())


def test_golden_snapshot(edit, sources):
    tl = compile_timeline(edit, sources, CUT)
    exp = G / "timeline.expected.json"
    if os.environ.get("UPDATE_GOLDEN") or not exp.exists():
        exp.write_text(json.dumps(tl, indent=1, ensure_ascii=False))
    assert tl == json.loads(exp.read_text())


def test_invariants(edit, sources):
    tl = compile_timeline(edit, sources, CUT)
    F = Fraction(tl["fps"])
    W = sources["A"]["words"]
    rec = 0
    for s in tl["segments"]:
        assert isinstance(s["n"], int) and s["n"] >= 1
        assert s["rec"] == rec
        rec += s["n"]
        end = s["in"] + s["n"] / F
        for k in s["kept"]:
            assert s["in"] <= W[k]["s"] and W[k]["e"] <= end, f"kept word {k} clipped"
    removed = {7} | {w["i"] for w in W if w.get("cue")}  # the filler and every cue word
    for s in tl["segments"]:
        end = s["in"] + s["n"] / F
        for k in removed:
            assert min(end, W[k]["e"]) - max(s["in"], W[k]["s"]) <= 1 / F, f"removed word {k} in piece {s['id']}"
    assert tl["frames"] == rec
    assert tl["stats"]["fillers"] == 1


def test_filler_cut_leaves_room_for_crossfade(edit, sources):
    tl = compile_timeline(edit, sources, CUT)
    c1 = [s for s in tl["segments"] if s["clip"] == "c1"]
    assert len(c1) == 2, "filler 7 splits c1 in two"
    assert all(r >= 0 for s in c1 for r in s["room"])


def _gaps(tl, sources) -> list[tuple[str, float]]:
    """(join class, silence at the cut: the quiet kept from the source plus room tone created) per cut.
    Quiet = after the last word audibly decays, before the next audibly starts (not word timestamps:
    wav2vec2 word edges sit 0.1-0.15 s inside the audible word)."""
    return [(s["join"]["class"], round(s["join"]["kept"] + s["join"]["room"], 3))
            for s in tl["segments"] if s.get("join", {}).get("class") in ("intra", "sentence", "section")]


def test_sentence_boundary_keeps_its_gap(edit, sources):
    """The cut after the removed "Um," follows "pattern.": a sentence boundary, so it keeps sentence air."""
    mn, tg, mx = gaps(CUT)["sentence"]
    got = [g for c, g in _gaps(compile_timeline(edit, sources, CUT), sources) if c == "sentence"]
    assert got and all(mn <= g <= mx + 0.05 for g in got), got


def test_topic_change_gets_a_longer_gap(edit, sources):
    js = _gaps(compile_timeline(edit, sources, CUT), sources)
    sec, sen = [g for c, g in js if c == "section"], [g for c, g in js if c == "sentence"]
    assert len(sec) == 4 and min(sec) >= gaps(CUT)["section"][0] and min(sec) > max(sen), js


def test_breathing_knob_scales_every_gap(edit, sources):
    tight = _gaps(compile_timeline(edit, sources, {**CUT, "breathing": "tight"}), sources)
    relaxed = _gaps(compile_timeline(edit, sources, {**CUT, "breathing": "relaxed"}), sources)
    assert all(t < r for (_, t), (_, r) in zip(tight, relaxed)), (tight, relaxed)
    with pytest.raises(EditError, match="breathing"):
        compile_timeline(edit, sources, {**CUT, "breathing": "loose"})


def test_long_pauses_shorten_to_their_class(sources):
    """1.5 s after "pattern.", 0.7 s after "Um,": both clause ends, but cutting both would leave "Um," alone on
    screen, so it joins the next sentence across the shorter pause and only the long one becomes a cut."""
    e = {"clips": [{"id": "c", "src": "A", "from": 0, "to": 16, "keep": ["filler"]}]}
    tl = compile_timeline(e, sources, {**CUT, "max_pause": 0.5})
    G, js = gaps(CUT), _gaps(tl, sources)
    assert [c for c, _ in js] == ["sentence"] and tl["stats"]["pauses"] == 1, js
    ((_, s),) = js
    assert G["sentence"][0] <= s <= G["sentence"][2] + 0.05, js
    assert [s["words"] for s in tl["segments"]] == [[0, 6], [7, 16]]


@pytest.fixture
def runon():
    tr = json.loads((RUNON / "transcript.A.json").read_text())
    return {"A": {**tr, "dur": 9.56, "fps": "25/1", "w": 640, "h": 360, "env": np.load(RUNON / "env.A.npy")}}


def test_run_on_speaker_gets_room_tone(runon):
    """"The cache was cold. [sentence removed] so we added a second layer." with no pause anywhere: the sentence
    boundary needs air the source doesn't have, so room tone from the clip's own silence fills it."""
    e = {"clips": [{"id": "a", "src": "A", "from": 0, "to": 3}, {"id": "b", "src": "A", "from": 12, "to": 17}]}
    tl = compile_timeline(e, runon, CUT)
    tone = [s for s in tl["segments"] if "tone" in s]
    assert len(tone) == 1 and tone[0]["tone"] + tone[0]["n"] / 25 <= 1.45, tone  # from the 1.5 s before any speech
    ((c, g),) = _gaps(tl, runon)
    assert c == "sentence" and g >= gaps(CUT)["sentence"][0], g
    assert tl["stats"]["room"] > 0.2


def test_retake_attempt_warns(sources):
    e = {"clips": [{"id": "c", "src": "A", "from": 17, "to": 34}]}
    tl = compile_timeline(e, sources, CUT)
    assert any("RETAKE" in w for w in tl["warnings"])
    W = sources["A"]["words"]
    assert all(W[23]["i"] not in s["kept"] for s in tl["segments"]), "cue word 'retake' never kept"


def test_user_cut_and_keep_filler(sources):
    e = {"clips": [{"id": "c", "src": "A", "from": 24, "to": 34, "cut": [[29, 30]]}]}
    tl = compile_timeline(e, sources, CUT)
    kept = {k for s in tl["segments"] for k in s["kept"]}
    assert not kept & {29, 30} and {28, 31} <= kept
    e2 = {"clips": [{"id": "c", "src": "A", "from": 0, "to": 16, "keep": ["filler"]}]}
    assert 7 in {k for s in compile_timeline(e2, sources, CUT)["segments"] for k in s["kept"]}


def test_back_to_back_clips_merge(sources):
    e = {"clips": [{"id": "a", "src": "A", "from": 8, "to": 11}, {"id": "b", "src": "A", "from": 12, "to": 16}]}
    tl = compile_timeline(e, sources, CUT)
    assert len(tl["segments"]) == 1 and tl["segments"][0]["also"] == ["b"]


def test_markers(edit, sources):
    tl = compile_timeline(edit, sources, CUT)
    kinds = {m["kind"] for m in tl["markers"]}
    assert {"uncertain", "note", "highlight"} <= kinds


def test_overlay_starts_on_its_word(edit, sources):
    tl = compile_timeline(edit, sources, CUT)
    (ov,) = tl["overlays"]
    W = sources["A"]["words"]
    seg = next(s for s in tl["segments"] if 29 in s["kept"])
    assert ov["rec"] == seg["rec"] + round((W[29]["s"] - seg["in"]) * 25)
    assert ov["n"] == 75 and ov["gain_db"] == -18
    assert ov["rec"] + ov["n"] > seg["rec"] + seg["n"], "fixture overlay crosses the cut into S3"
    assert tl["frames"] == compile_timeline({**edit, "overlays": []}, sources, CUT)["frames"], "B-roll never changes length"


@pytest.mark.parametrize("overlays, msg", [
    ([{"id": "x", "src": "B", "in": 0, "out": 2, "over": "c2"}, {"id": "y", "src": "B", "in": 3, "out": 5, "over": "c2", "at": 1}], "overlap"),
    ([{"id": "x", "src": "B", "in": 0, "out": 8, "over": "c5"}], "past the end"),
    ([{"id": "x", "src": "B", "in": 0, "out": 2, "over": "c1", "word": 7}], "not kept"),
    ([{"id": "x", "src": "B", "in": 0, "out": 2, "over": "c9"}], "must name a clip"),
    ([{"id": "x", "src": "B", "in": 6, "out": 9, "over": "c1"}], "outside source"),
])
def test_bad_overlays(overlays, msg, edit, sources):
    with pytest.raises(EditError, match=msg):
        compile_timeline({**edit, "overlays": overlays}, sources, CUT)


@pytest.mark.parametrize("bad, msg", [
    ({"clips": [{"id": "c", "src": "Z", "from": 0, "to": 3}]}, "unknown source"),
    ({"clips": [{"id": "c", "src": "A", "from": 70, "to": 99}]}, "outside source"),
    ({"clips": [{"id": "c", "src": "A", "from": 0, "to": 3}, {"id": "c", "src": "A", "from": 5, "to": 6}]}, "unique"),
    ({"clips": [{"id": "c", "src": "A", "in": 40, "out": 60}]}, "outside source"),
])
def test_bad_edits(bad, msg, sources):
    with pytest.raises(EditError, match=msg):
        compile_timeline(copy.deepcopy(bad), sources, CUT)


def test_vertical_picture_led_cut(edit, sources):
    """short profile: 9:16 from the profile whatever the sources; natural-sound clips with their own gain;
    on-screen texts anchored like overlays."""
    short = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "short.toml").read_text())
    e = {"clips": [{"id": "p1", "src": "B", "in": 0.0, "out": 3.0}, {"id": "p2", "src": "B", "in": 3.0, "out": 6.0, "gain_db": -6},
                   {"id": "c1", "src": "A", "from": 24, "to": 34}],
         "texts": [{"id": "t1", "text": "Las hacen al momento…", "over": "p1", "at": 0.5},
                   {"id": "t2", "text": "13 pounds", "style": "hi", "over": "c1", "word": 29, "dur": 1.0}]}
    tl = compile_timeline(e, sources, short["cut"], short["timeline"])
    assert tl["size"] == [1080, 1920] and tl["fps"] == "30"
    assert compile_timeline(e, sources, short["cut"], None)["size"] == [360, 640], "falls back to the first clip"
    assert compile_timeline({**e, "timeline": {"size": [720, 1280]}}, sources, short["cut"], short["timeline"])["size"] == [720, 1280]
    p1, p2 = tl["segments"][:2]
    assert "gain_db" not in p1 and p2["gain_db"] == -6, "different gains never merge"
    t1, t2 = tl["texts"]
    assert (t1["rec"], t1["n"], t1["style"]) == (15, 45, "txt")
    c1 = next(s for s in tl["segments"] if s["clip"] == "c1")
    assert t2["rec"] == c1["rec"] + round((sources["A"]["words"][29]["s"] - c1["in"]) * 30) and t2["n"] == 30


@pytest.mark.parametrize("texts, msg", [
    ([{"id": "x", "text": "a", "over": "c1", "dur": 1.5}, {"id": "y", "text": "b", "over": "c1", "at": 1.0}], "overlap"),
    ([{"id": "x", "text": "a", "over": "c5", "dur": 9}], "past the end"),
    ([{"id": "x", "text": " ", "over": "c1"}], "empty"),
    ([{"id": "x", "text": "a", "over": "c1", "style": "big"}], "style"),
    ([{"id": "x", "text": "a", "over": "c9"}], "must name a clip"),
])
def test_bad_texts(texts, msg, edit, sources):
    with pytest.raises(EditError, match=msg):
        compile_timeline({**edit, "texts": texts}, sources, CUT)
    ok = [{"id": "x", "text": "a", "over": "c1"}, {"id": "y", "text": "b", "style": "hi", "over": "c1", "at": 1.0}]
    assert len(compile_timeline({**edit, "texts": ok}, sources, CUT)["texts"]) == 2, "a txt and a hi may share the screen"


def test_caption_fix(edit, sources):
    """fix rewrites caption text for misheard words (or drops them with ""); cuts don't change."""
    from jianji.render import caption_cues
    W = sources["A"]["words"]
    cap = {"max_chars": 42, "min_dur": 0.9, "max_dur": 3.2, "break_gap": 0.6}
    fixed = {**edit, "clips": [{**edit["clips"][0], "fix": {"0": "XYZZY", "1": ""}}, *edit["clips"][1:]]}
    a, b = compile_timeline(edit, sources, CUT), compile_timeline(fixed, sources, CUT)
    assert [s["n"] for s in a["segments"]] == [s["n"] for s in b["segments"]]
    first = caption_cues(b, sources, cap)[0][2]
    assert first.startswith("XYZZY") and W[1]["t"] not in first.split()[:2], first
    both = caption_cues(compile_timeline({"clips": [{**edit["clips"][0], "fix": {"1": ""}}]}, sources, CUT), sources, cap)
    plain = caption_cues(compile_timeline({"clips": [edit["clips"][0]]}, sources, CUT), sources, cap)
    assert len(both) <= len(plain), "a word fixed away never splits a caption"
    from jianji.render import _clean
    assert _clean("13 pounds. que comen dos. ¿verdad?") == "13 pounds. Que comen dos. ¿Verdad?"
    with pytest.raises(EditError, match="outside the clip"):
        compile_timeline({"clips": [{**edit["clips"][0], "fix": {"99": "x"}}]}, sources, CUT)
