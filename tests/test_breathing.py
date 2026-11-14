"""Breathing room on a real render of a speaker who never pauses (tests/fixtures/runon): the sentence boundary
gets room tone and QA measures the gap on the render's audio. With `max_room = 0` nothing is made up: every piece
is the take itself, the cut stays as tight as the take is, and QA says so instead of failing it."""
import json
import shutil
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from jianji.cli import _tr_path, compile_edit, load_sources
from jianji.core import Project
from jianji.media import build_catalog, decode
from jianji.qa import breathing
from jianji.render import render

RUNON = Path(__file__).parent / "fixtures" / "runon"
EDIT = {"v": 1, "clips": [{"id": "a", "src": "A", "from": 0, "to": 3}, {"id": "b", "src": "A", "from": 12, "to": 17}]}


def _render(d: Path, cut: dict | None = None):
    (d / "footage").mkdir(parents=True)
    shutil.copy2(RUNON / "footage" / "runon.mp4", d / "footage" / "runon.mp4")
    p = Project(str(d))
    if cut:
        p.cfg["cut"].update(cut)
    cat = build_catalog(p)
    shutil.copy(RUNON / "transcript.A.json", _tr_path(p, "A", cat["sources"]["A"]))
    (p.out / "edits").mkdir()
    (p.out / "edits" / "edit.v001.json").write_text(json.dumps(EDIT))
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    outs = render(p, tl, cat, sources, v)
    return p, tl, sources, decode(Path(outs["master"]))[:, 0], outs


@pytest.fixture(scope="module")
def with_room(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("room"))


def test_room_tone_gives_the_sentence_its_gap(with_room):
    p, tl, sources, heard, _ = with_room
    (j,) = breathing(tl, sources, heard, p.cfg["cut"])
    assert j["class"] == "sentence" and not j["tight"] and not j["straight"], j


def test_created_gap_is_room_tone_not_digital_silence(with_room):
    p, tl, sources, heard, _ = with_room
    F = Fraction(tl["fps"])
    (tone,) = [s for s in tl["segments"] if "tone" in s]
    a, b = float(tone["rec"] / F) + 0.03, float((tone["rec"] + tone["n"]) / F) - 0.03
    gap = heard[int(a * 16000):int(b * 16000)]
    rms_db = 20 * np.log10(np.sqrt((gap ** 2).mean()) + 1e-9)
    assert -75 < rms_db < -35, rms_db  # the clip's own room, well under the voice, never zero


@pytest.fixture(scope="module")
def bare(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("bare"), {"max_room": 0})


def test_without_room_tone_every_piece_is_the_take_itself(bare):
    """`max_room = 0`: no piece is put in between two cuts, in the timeline or in what Resolve gets: each one holds
    words of the take, runs on in the source, and shows no frame the piece before it showed."""
    from jianji.resolve import plan
    p, tl, sources, heard, _ = bare
    F = Fraction(tl["fps"])
    segs = tl["segments"]
    assert len(segs) == 2 and sum(s["n"] for s in segs) == tl["frames"]
    assert all(s["kept"] and "tone" not in s for s in segs), segs
    assert all(not (a["src"] == b["src"] and a["in"] <= b["in"] < a["in"] + a["n"] / F) for a, b in zip(segs, segs[1:]))
    assert "room" not in tl["stats"]
    items = plan(p, 1)["items"]
    assert sorted(i["track"] for i in items) == ["A1", "A1", "V1", "V1"], items  # no room-tone track, no extra clip
    assert [(i["start"], i["end"]) for i in items if i["track"] == "V1"] == [(s["rec"], s["rec"] + s["n"]) for s in segs]


def test_a_cut_the_take_gives_no_pause_for_is_straight_not_too_tight(bare):
    p, tl, sources, heard, _ = bare
    (j,) = breathing(tl, sources, heard, p.cfg["cut"])
    assert j["tight"] and j["quiet"] < 0.1 and j["straight"], j
    tl2 = {**tl, "segments": [{**s, "join": {**s["join"], "kept": 0.3}} if "join" in s else s for s in tl["segments"]]}
    (j,) = breathing(tl2, sources, heard, p.cfg["cut"])
    assert j["tight"] and not j["straight"], j  # the plan kept a pause and the render lost it: still a failure


def test_qa_passes_a_straight_cut_and_fails_a_lost_pause(bare):
    from jianji.qa import speech_cut_issues
    p, tl, sources, heard, _ = bare
    (j,) = breathing(tl, sources, heard, p.cfg["cut"])
    bad, detail = speech_cut_issues([j], [])
    assert not bad and "0 without breathing room" in detail and "1 straight where the take has no pause" in detail
    bad, detail = speech_cut_issues([{**j, "straight": False}], [])
    assert len(bad) == 1 and "cut without breathing room: sentence gap" in bad[0][1] and "straight" not in detail


def test_a_pause_in_a_noisy_room_is_still_a_pause():
    """A stream: music and keys 25 dB under the voice, and a noise gate that closes now and then, so the noise floor
    sits far under the room. Against the floor that room is never quiet and every cut looked tight; against the
    voice, 0.4 s between two sentences is a pause, and words that run together still aren't."""
    import tomllib
    cut = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "short.toml").read_text())["cut"]
    rng = np.random.default_rng(0)
    level = lambda db, secs: rng.normal(0, 10 ** (db / 20), round(secs * 16000)).astype(np.float32)
    words = [{"i": i, "t": t, "s": s, "e": s + 0.5} for i, (t, s) in enumerate((("one", 1.0), ("two.", 1.5), ("three", 5.0), ("four.", 5.5)))]

    def join(pause):  # gate closed | "one two." | the room | "three four." | gate closed
        heard = np.concatenate([level(-80, 1), level(-20, 1), level(-45, pause), level(-20, 1), level(-80, 1)])
        n = round((2 + pause / 2) * 25)
        tl = {"fps": "25/1", "segments": [
            {"id": 1, "clip": "a", "sec": None, "src": "A", "in": 0.0, "n": n, "rec": 0, "kept": [0, 1]},
            {"id": 2, "clip": "b", "sec": None, "src": "A", "in": 5.0 - pause / 2, "n": n, "rec": n, "kept": [2, 3]}]}
        (j,) = breathing(tl, {"A": {"words": words}}, heard, cut)
        return j

    assert not join(0.4)["tight"] and 0.35 <= join(0.4)["quiet"] <= 0.45, join(0.4)
    assert join(0.08)["tight"], join(0.08)
