"""Every text and every shot must register: texts stay up their reading time (crossing cuts, never shortened to
dodge a face), picture shots hold their type's minimum. Compile and placement only: no renders."""
import json
import tomllib
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from jianji.compile import compile_timeline, reading_time, settle
from jianji.place import place_texts

G = Path(__file__).parent / "fixtures" / "golden"
PROFILE = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "short.toml").read_text())
CUT = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "explainer.toml").read_text())["cut"]
TX, SHOTS, ZONE = PROFILE["texts"], PROFILE["shots"], PROFILE["safe_zone"]


@pytest.fixture
def sources():
    tr = json.loads((G / "transcript.A.json").read_text())
    return {"A": {**tr, "dur": 42.92, "fps": "25/1", "w": 640, "h": 360, "env": np.load(G / "env.A.npy")},
            "B": {"words": [], "voiced": [], "cues": [], "dur": 8.0, "fps": "25/1", "w": 360, "h": 640,
                  "env": np.full(801, -60.0)}}


@pytest.fixture
def edit():
    e = json.loads((G / "edit.v001.json").read_text())
    e["overlays"] = []
    return e


def _compile(edit, sources):
    return compile_timeline(edit, sources, CUT, None, TX, SHOTS)


def test_reading_time():
    t = "Café Central · Lavapiés"
    assert reading_time(t, TX) == pytest.approx(0.4 + len(t) / 13)  # 2.17 s; it used to get 1.3
    assert reading_time("8,50 €", TX) == 1.5  # short texts get the floor
    assert reading_time("x" * 120, TX) == 4.5  # capped: too long to read, shorten the words


def test_text_keeps_its_reading_time_across_a_cut(edit, sources):
    """A story line near the end of c1 runs on into c2 instead of being cut at the shot change; the next story
    line waits for it; one too long to read in 4.5 s is capped (QA warns); the last one pulls back to fit."""
    edit["texts"] = [{"id": "t1", "text": "Every service owns its data", "over": "c1", "at": 4.5, "dur": 1.0},
                     {"id": "t2", "text": "even the ledger", "over": "c2", "at": 0.5},
                     {"id": "h1", "text": "x" * 80, "style": "hi", "over": "c3", "at": 0.0},
                     {"id": "t3", "text": "Takeaway", "over": "c5", "at": 1.0, "dur": 0.5}]
    tl = _compile(edit, sources)
    t = {x["id"]: x for x in tl["texts"]}
    c1_end = max(s["rec"] + s["n"] for s in tl["segments"] if s["clip"] == "c1")
    assert t["t1"]["n"] == round(reading_time("Every service owns its data", TX) * 25)
    assert t["t1"]["rec"] < c1_end < t["t1"]["rec"] + t["t1"]["n"], "crosses the cut into c2, whole"
    assert t["t2"]["rec"] == t["t1"]["rec"] + t["t1"]["n"]
    assert t["h1"]["n"] == round(4.5 * 25)
    assert t["t3"]["rec"] + t["t3"]["n"] == tl["frames"] and t["t3"]["n"] == round(1.5 * 25)
    assert any("t2 starts" in w and "t1 can be read" in w for w in tl["warnings"]), tl["warnings"]
    assert any("t3 starts" in w and "earlier" in w for w in tl["warnings"]), tl["warnings"]


def test_short_establishing_shot_is_extended(edit, sources):
    edit["overlays"] = [{"id": "b1", "src": "B", "in": 2.0, "out": 3.0, "over": "c2", "word": 29, "shot": "place"}]
    tl = _compile(edit, sources)
    (b1,) = tl["overlays"]
    assert b1["in"] == 2.0 and b1["n"] >= round(SHOTS["place"] * 25), b1  # longer into its source, same start
    (shot,) = tl["shots"]
    assert shot["visible"] >= round(shot["need"] * 25) and any("b1: place shot held" in w for w in tl["warnings"])


def test_short_establishing_shot_without_room_is_flagged(edit, sources):
    """The sign shot is the source's last second and the shot before it is chained to it: nothing to extend."""
    edit["overlays"] = [{"id": "b0", "src": "B", "in": 0.0, "out": 2.0, "over": "c2", "at": 0.0, "shot": "detail"},
                        {"id": "b1", "src": "B", "in": 7.0, "out": 8.0, "over": "c2", "at": 2.0, "shot": "place"}]
    tl = _compile(edit, sources)
    short = [s for s in tl["shots"] if s["visible"] < round(s["need"] * 25) - 1]
    assert [s["id"] for s in short] == ["b1"], tl["shots"]  # QA shot_readable counts it
    assert any("b1: a place shot wants 2.0s" in w for w in tl["warnings"]), tl["warnings"]


def test_picture_clip_holds_without_running_into_a_word(sources):
    """A detail shot cut from the talking-head source before its first word: held 1.2 s, never into "Today"."""
    e = {"clips": [{"id": "p", "src": "A", "in": 0.5, "out": 1.0, "shot": "detail"},
                   {"id": "c", "src": "A", "from": 10, "to": 16}]}
    tl = _compile(e, sources)
    p = tl["segments"][0]
    assert p["n"] == round(1.2 * 25) and p["in"] + p["n"] / 25 < sources["A"]["words"][0]["s"], p
    unlabeled = compile_timeline({"clips": [{"id": "p", "src": "A", "in": 0.5, "out": 1.0}]}, sources, CUT, None, TX, SHOTS)
    assert unlabeled["segments"][0]["n"] == round(0.5 * 25) and not unlabeled["shots"]  # the talking head: no rule


def test_action_holds_until_its_motion_settles():
    m = np.r_[np.full(20, 1.0), np.full(15, 12.0), np.full(20, 1.0)].astype(np.float32)  # moving 2.0-3.5 s
    src = {"motion": m}
    assert settle(src, 2.5, 4.0) == pytest.approx(3.5, abs=0.11)
    assert settle(src, 1.0, 2.0) == 1.0  # already calm
    assert settle(src, 2.5, 3.0) is None  # still moving at the limit: flagged


FACE_TOP = [0.35, 0.17, 0.65, 0.33, 0.9]  # a close-up's face in the upper middle, where callouts sit


def test_badge_crossing_into_a_close_up_changes_slot_not_length():
    """A badge, "Café Central · Plaza Mayor", starts on the sign and runs on into the presenter's close-up. It used to be
    shortened to the sign shot (1.2 s); now it keeps its reading time and takes a slot clear of the face."""
    frames = [{"t": k / 4, "faces": [FACE_TOP] if k / 4 >= 3.0 else [], "salient": []} for k in range(32)]
    text = "Café Central ·\nPlaza Mayor"
    n = round(reading_time(text, TX) * 30)
    placed, log = place_texts([{"id": "h1", "text": text, "style": "hi", "rec": 75, "n": n}], Fraction(30), frames,
                              TX, 1080, 1920, ZONE, [], 8.0)
    h1 = placed["h1"]
    assert h1["a"] == 2.5 and h1["z"] - h1["a"] == pytest.approx(n / 30, abs=0.002) and h1["face"] <= 0.1, h1
    assert "slot" in log[0] and "shorten" not in " ".join(log), log


def test_badge_with_no_clear_slot_stays_readable_and_is_flagged():
    """The face fills every slot above two-line captions except during a 1.2 s sign shot: too short to read the
    badge, so it keeps its time over the face and review.md says why."""
    face = [[0.36, 0.24, 0.69, 0.45, 0.9]]
    frames = [{"t": k / 4 + 0.125, "faces": face if not 2.75 <= k / 4 < 4.0 else [], "salient": []} for k in range(28)]
    text = "Café Central ·\nPlaza Mayor"
    n = round(reading_time(text, TX) * 30)
    placed, log = place_texts([{"id": "h1", "text": text, "style": "hi", "rec": 82, "n": n}], Fraction(30), frames,
                              TX, 1080, 1920, ZONE, [(0.0, 7.0, (100, 980, 980, 1240), "cap")], 7.0)
    h1 = placed["h1"]
    assert h1["z"] - h1["a"] == pytest.approx(n / 30, abs=0.002), h1
    assert "kept readable but over a face" in log[-1], log


def test_story_lines_in_a_run_share_one_slot():
    """A face drops into the story-line slot during the second of three lines: the whole run moves down together
    instead of one line jumping."""
    face = [0.4, 0.16, 0.6, 0.22, 0.9]
    frames = [{"t": k / 4, "faces": [face] if 2.0 <= k / 4 < 4.0 else [], "salient": []} for k in range(40)]
    texts = [{"id": f"t{k}", "text": w, "style": "txt", "rec": k * 60, "n": 60}
             for k, w in enumerate(("Uno dos tres", "Cuatro cinco", "Seis siete"))]
    placed, log = place_texts(texts, Fraction(30), frames, TX, 1080, 1920, ZONE, [], 10.0)
    tops = {p["top"] for p in placed.values()}
    assert len(tops) == 1 and tops != {290}, placed  # one position, not the default
    assert all(p["face"] <= 0.1 and p["z"] - p["a"] == pytest.approx(2.0, abs=0.002) for p in placed.values()), placed
    assert log and log[0].startswith("story lines t0, t1, t2 share slot"), log
