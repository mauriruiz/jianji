"""How a cut opens (`[hook]`, QA `hook`): on its line, never on a run-up, said in one go, and on the line the edit
names. The take is synthetic: made-up words, speech at -20 dB inside them, room at -60 dB between."""
import tomllib
from pathlib import Path

import numpy as np
import pytest

from jianji.compile import EditError, compile_timeline, opening

PROFILE = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "short.toml").read_text())
CUT, HOOK = PROFILE["cut"], PROFILE["hook"]
WORDS = [("So,", 0.50, 0.62), ("the", 0.70, 0.78), ("tick", 0.80, 1.00), ("rate", 1.02, 1.25), ("is", 1.28, 1.36),
         ("wrong.", 1.40, 1.85), ("It", 2.60, 2.68), ("runs", 2.70, 2.95), ("twice", 3.00, 3.35), ("per", 3.40, 3.50),
         ("frame,", 3.55, 3.95), ("um,", 4.20, 4.35), ("and", 4.60, 4.70), ("nobody", 4.75, 5.10),
         ("noticed.", 5.15, 5.70), ("This", 7.50, 7.65), ("function", 9.20, 9.70), ("is", 9.75, 9.85),
         ("the", 9.90, 9.98), ("bug.", 10.30, 10.70)]


@pytest.fixture
def sources():
    env = np.full(1301, -60.0)
    W = []
    for i, (t, s, e) in enumerate(WORDS):
        env[round(s * 100):round(e * 100)] = -20.0
        W.append({"i": i, "t": t, "s": s, "e": e, "p": 0.9, **({"filler": True} if t == "um," else {})})
    quiet = {"words": [], "voiced": [], "cues": [], "dur": 10.0, "fps": "30/1", "w": 1080, "h": 1920, "env": np.full(1001, -60.0)}
    return {"A": {"words": W, "voiced": [], "cues": [], "dur": 13.0, "fps": "30/1", "w": 1080, "h": 1920, "env": env},
            "B": quiet}


def _cut(sources, *clips, **edit):
    tl = compile_timeline({"clips": [{"id": f"c{k}", "src": "A", **c} for k, c in enumerate(clips)], **edit}, sources,
                          CUT, {"size": [1080, 1920], "fps": "30/1"}, None, None, None, HOOK)
    return tl, opening(tl, sources, HOOK), [w for w in tl["warnings"] if w.startswith("opening:")]


def test_a_cut_that_opens_on_its_line_passes(sources):
    tl, (found, how), warned = _cut(sources, {"from": 1, "to": 5}, {"from": 12, "to": 14}, hook="The tick rate is wrong")
    assert found == [] and warned == [] and tl["hook"] == "The tick rate is wrong"
    assert how.startswith("opens on 'the tick rate is wrong.") and "lands at 1.2s" in how


def test_a_run_up_word_or_a_filler_first_fails(sources):
    _, (found, _), warned = _cut(sources, {"from": 0, "to": 5})
    assert [(sev, msg.split(":")[0]) for sev, _, msg in found] == [("fail", "opens on a run-up ('so')")]
    assert warned == ["opening: opens on a run-up ('so'): start the first clip on the line itself (QA fails it)"]
    _, (found, _), _ = _cut(sources, {"from": 11, "to": 14, "keep": ["filler"]})  # "um, and nobody noticed."
    assert found[0][0] == "fail" and "run-up ('um,')" in found[0][2]
    mine = {**HOOK, "preamble": ["and nobody"]}  # your own run-ups, phrases included, replace the built-in list
    tl, _, _ = _cut(sources, {"from": 12, "to": 14})
    assert opening(tl, sources, HOOK)[0] == [] and "run-up ('and nobody')" in opening(tl, sources, mine)[0][0][2]


def test_the_cut_opens_on_the_hook_the_edit_names(sources):
    _, (found, _), _ = _cut(sources, {"from": 1, "to": 5}, {"from": 12, "to": 14}, hook="Nobody noticed")
    assert [x[0] for x in found] == ["fail"] and "opens on 'the tick'" in found[0][2]
    _, (found, _), _ = _cut(sources, {"from": 12, "to": 14}, {"from": 1, "to": 5}, hook="and nobody noticed")
    assert found == []  # told out of order: the strongest line first
    with pytest.raises(EditError, match="hook"):
        _cut(sources, {"from": 1, "to": 5}, hook=["the tick rate"])


def test_a_halting_line_fails_and_a_late_landing_warns(sources):
    _, (found, how), warned = _cut(sources, {"from": 15, "to": 19})  # "This ... function is the bug."
    assert [x[0] for x in found] == ["fail", "warn"] and "1.7s from 'This' to the next word" in found[0][2]
    assert "lands at" in found[1][2] and len(warned) == 2 and "QA fails it" in warned[0] and "QA fails it" not in warned[1]
    tl, (found, _), _ = _cut(sources, {"from": 6, "to": 14, "cut": [[11, 11]]})  # one long sentence, said in one go
    assert found == [] and [x[0] for x in opening(tl, sources, {**HOOK, "seconds": 2.0})[0]] == ["warn"]
    _, (found, how), _ = _cut(sources, {"from": 1, "to": 10, "keep": ["pauses"]})  # "...is wrong." and then a beat
    assert found == [] and "lands at 1.2s" in how  # what follows the opening line is pacing, not the hook


def test_a_picture_led_opening_is_the_editors_call(sources):
    tl, (found, how), warned = _cut(sources, {"src": "B", "in": 0.0, "out": 2.0}, {"from": 0, "to": 5})
    assert found == [] and warned == [] and "picture" in how
    assert not any(w.startswith("opening:") for w in compile_timeline(
        {"clips": [{"id": "c", "src": "A", "from": 0, "to": 5}]}, sources, CUT)["warnings"])  # no [hook]: not judged
