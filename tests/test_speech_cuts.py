"""Speech cuts land on clause or sentence ends with breathing room, and nothing is on screen for a jarring fraction
of a second. The take is synthetic: word timings from a real fast-talking line (numbers only, made-up words), speech
at -20 dB inside words, room at -60 dB between."""
import tomllib
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from jianji.compile import compile_timeline, gaps, picture_runs
from jianji.qa import fragments, mid_sentence, through_speech

PROFILE = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "short.toml").read_text())
CUT, SHOTS, TX = PROFILE["cut"], PROFILE["shots"], PROFILE["texts"]
WORDS = [("vale.", 1.83, 2.10), ("Bueno,", 4.69, 5.18), ("resumen,", 5.24, 5.76), ("cuesta", 5.78, 5.86),
         ("poco,", 6.83, 7.09), ("eh.", 7.17, 7.31), ("Si", 7.63, 7.67), ("venís", 7.69, 7.85),
         ("pronto,", 7.95, 8.42), ("os", 10.03, 10.13), ("va", 10.15, 10.19), ("a", 10.21, 10.23),
         ("gustar", 10.29, 10.53), ("bastante.", 10.59, 10.93), ("O", 10.99, 11.01), ("sea,", 11.03, 11.07),
         ("si", 11.13, 11.19), ("no", 11.23, 11.29), ("os", 11.33, 11.38), ("gusta", 11.40, 11.56),
         ("el", 11.58, 11.64), ("ruido...", 11.66, 12.09), ("Perfecto.", 13.14, 13.55)]


@pytest.fixture
def sources():
    env = np.full(1601, -60.0)
    W = []
    for i, (t, s, e) in enumerate(WORDS):
        env[round(s * 100):round(e * 100)] = -20.0
        W.append({"i": i, "t": t, "s": s, "e": e, "p": 0.9, **({"filler": True} if t == "eh." else {})})
    take = {"words": W, "voiced": [], "cues": [], "spans": [[1.8, 2.1], [4.6, 13.6]], "dur": 16.0, "fps": "25/1",
            "w": 1080, "h": 1920, "env": env}
    broll = {"words": [], "voiced": [], "cues": [], "dur": 10.0, "fps": "25/1", "w": 1080, "h": 1920,
             "env": np.full(1001, -60.0), "note": "SHOWS: close-up of a coffee cup"}
    return {"M": take, "B": broll}


def _compile(edit, sources):
    return compile_timeline(edit, sources, CUT, {"size": [1080, 1920], "fps": "25/1"}, TX, SHOTS)


def test_no_cut_inside_a_clause_and_the_tag_stays(sources):
    """"cuesta [0.97 s] poco" used to be cut mid-clause and "eh." dropped, leaving 0.77 s and 0.67 s pieces.
    The hesitation stays, "eh." closes its sentence, and the only pause cut is at "pronto," (a clause end)."""
    tl = _compile({"clips": [{"id": "c9", "src": "M", "from": 2, "to": 21}]}, sources)
    (a, b) = [s for s in tl["segments"] if s["kept"]]
    assert a["kept"] == list(range(2, 9)) and b["kept"] == list(range(9, 22)), tl["segments"]  # 5 "eh." kept
    assert b["join"]["class"] == "sentence" and b["join"]["kept"] + b["join"]["room"] >= gaps(CUT)["sentence"][0]
    assert all(s["n"] >= 25 for s in tl["segments"]) and not mid_sentence(tl, sources, CUT)


def test_a_sentence_that_runs_on_is_finished(sources):
    """The clip stops at "bastante." but the speaker goes straight on ("O sea, si no os gusta el ruido..."): no
    pause, so not a real end. Compile lets it run on to the pause, +1.2 s of the same speech."""
    tl = _compile({"clips": [{"id": "c9", "src": "M", "from": 2, "to": 13}]}, sources)
    assert tl["segments"][-1]["kept"][-1] == 21, tl["segments"]
    assert any("runs on to 'ruido...'" in w for w in tl["warnings"]), tl["warnings"]
    assert not mid_sentence(tl, sources, CUT)


def test_mid_sentence_cut_fails_unless_justified(sources):
    """Too far to finish within finish_max: compile warns and QA flags it; "abrupt" (a topic jump) is the only way
    to keep it, and the reason travels to QA and review.md."""
    cut = {**CUT, "finish_max": 0.5}
    tl = compile_timeline({"clips": [{"id": "c9", "src": "M", "from": 2, "to": 13}]}, sources, cut, None, TX, SHOTS)
    assert tl["segments"][-1]["kept"][-1] == 13 and any("lands mid-sentence" in w for w in tl["warnings"])
    (m,) = mid_sentence(tl, sources, cut)
    assert m["word"] == "bastante." and m["why"] is None
    why = "topic jump: moves on to who it's for"
    tl = compile_timeline({"clips": [{"id": "c9", "src": "M", "from": 2, "to": 13, "abrupt": why}]}, sources, cut,
                          None, TX, SHOTS)
    assert not any("mid-sentence" in w for w in tl["warnings"]) and tl["justified"] == {"c9": why}
    assert [m["why"] for m in mid_sentence(tl, sources, cut)] == [why]


def test_talking_head_flash_between_broll_is_covered(sources):
    """The talking head used to show ~0.4 s between two B-roll shots, with a jump cut inside. The B-roll
    stretches over it, and nothing is on screen for under 1 s."""
    base = {"clips": [{"id": "c1", "src": "M", "from": 2, "to": 8}, {"id": "c2", "src": "M", "from": 9, "to": 21}]}
    c1 = next(s for s in _compile(base, sources)["segments"] if s["clip"] == "c1")
    F = Fraction(25)
    out = 0.5 + float(c1["n"] / F) - 0.5 - 0.2  # ends 0.2 s before the cut...
    edit = {**base, "overlays": [{"id": "b1", "src": "B", "in": 0.5, "out": out, "over": "c1", "at": 0.5},
                                 {"id": "b2", "src": "B", "in": 6.0, "out": 7.5, "over": "c2", "at": 0.2}]}  # ...and after
    tl = _compile(edit, sources)
    runs = picture_runs(tl["segments"], tl["overlays"], F)
    assert all(r["end"] - r["start"] >= 25 for r in runs), [(r["id"], r["end"] - r["start"]) for r in runs]
    assert any(w.startswith("b1 runs 0.20s longer") for w in tl["warnings"]), tl["warnings"]
    b1, b2 = tl["overlays"]
    assert b1["rec"] + b1["n"] == b2["rec"], "chained across the cut"


def test_a_flash_nothing_can_cover_is_flagged(sources):
    """"Perfecto." on its own between two other lines is ~0.7 s of talking head: no B-roll next to it, so compile
    warns and QA's short_segments fails it (merge it into a neighbour, extend or drop it)."""
    tl = _compile({"clips": [{"id": "c1", "src": "M", "from": 2, "to": 8}, {"id": "c2", "src": "M", "from": 22, "to": 22},
                             {"id": "c3", "src": "M", "from": 9, "to": 21}]}, sources)
    short = [r for r in picture_runs(tl["segments"], tl["overlays"], Fraction(25)) if r["end"] - r["start"] < 25]
    assert [r["id"] for r in short] == ["c2"], short
    assert any(w.startswith("c2 on screen") for w in tl["warnings"]), tl["warnings"]


def test_a_pause_cut_inside_a_clause_fails(sources):
    """A pause shortened mid-clause ("cuesta [0.97 s] | poco"). Compile no longer cuts there; a clip
    split at that spot still cuts it, and compile and QA both call it mid-sentence."""
    tl = _compile({"clips": [{"id": "a", "src": "M", "from": 2, "to": 3}, {"id": "b", "src": "M", "from": 4, "to": 8}]},
                  sources)
    assert [m["word"] for m in mid_sentence(tl, sources, CUT)] == ["cuesta"]
    assert any(w.startswith("a: speech cut after 'cuesta' lands mid-sentence") for w in tl["warnings"]), tl["warnings"]


def test_words_whisper_didnt_write_after_the_last_one(sources):
    """A question followed 0.2 s later by a false start Whisper never wrote. Compile took it for the word's decay
    and cut inside it; speech_cuts only looked at transcribed words, so it passed. Here "ruido..." gets the same: a dip 0.06 s after it, then voice with no word in the transcript."""
    env = sources["M"]["env"]
    env[1209:1215], env[1215:1222], env[1222:1270] = -22.0, -31.0, -21.0
    cut = {**CUT, "finish_max": 0.5}  # nothing to finish it with: the run-on stays visible to QA
    tl = compile_timeline({"clips": [{"id": "c", "src": "M", "from": 9, "to": 21}]}, sources, cut, None, TX, SHOTS)
    last = [s for s in tl["segments"] if s["kept"]][-1]
    assert last["in"] + last["n"] / 25 <= 12.23, last  # ends in the dip, before the words nobody wrote
    assert not through_speech(tl, sources, cut)
    old = {**tl, "segments": [{**last, "n": round((12.75 - last["in"]) * 25)}]}  # out at the next quiet, as the old cut did
    assert [(x["word"], x["side"]) for x in through_speech(old, sources, cut)] == [("ruido...", "ends")]
    end = float(old["segments"][0]["n"] / 25)
    (f,) = fragments(old, sources, cut, [(end - 0.4, "por"), (end - 0.2, "tres")])  # Whisper on the render agrees
    assert f["heard"] == "por tres" and f["side"] == "ends"
    assert not fragments(old, sources, cut, [])  # loud but no word there (a lid, a laugh): not a fail
    # no pause after it: a run-on, so QA wants it finished or marked abrupt, with the reason in review.md
    assert [m["word"] for m in mid_sentence(tl, sources, cut)] == ["ruido..."]


def test_a_pause_under_room_noise_ends_a_sentence():
    """A stream behind a noise gate: the room (music, keys) sits 25 dB under the voice and 35 dB over the floor the
    gate leaves. A sentence followed by 0.6 s of that room has ended; one the speaker runs on from hasn't."""
    from jianji.compile import is_boundary
    words = [("It", 2.0, 2.2), ("works.", 2.2, 2.6), ("And", 3.2, 3.4), ("then.", 3.4, 3.8), ("Next", 3.85, 4.1), ("one.", 4.1, 4.5)]
    env = np.full(801, -45.0)  # the room, with the gate open
    env[:150] = env[650:] = -80.0  # the gate closed: that's where the floor is measured
    W = []
    for i, (t, a, b) in enumerate(words):
        env[round(a * 100):round(b * 100)] = -20.0
        W.append({"i": i, "t": t, "s": a, "e": b})
    src = {"words": W, "env": env}
    assert is_boundary(src, 1, CUT) and not is_boundary(src, 3, CUT)  # "works." | pause; "then." runs into "Next"
