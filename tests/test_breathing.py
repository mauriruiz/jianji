"""Breathing room on a real render of a speaker who never pauses (tests/fixtures/runon): the sentence boundary
gets room tone, QA measures the gap on the render's audio, and without room tone QA calls the cut too tight."""
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
    return p, tl, sources, decode(Path(outs["master"]))[:, 0]


@pytest.fixture(scope="module")
def with_room(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("room"))


def test_room_tone_gives_the_sentence_its_gap(with_room):
    p, tl, sources, heard = with_room
    (j,) = breathing(tl, sources, heard, p.cfg["cut"])
    assert j["class"] == "sentence" and not j["tight"], j


def test_created_gap_is_room_tone_not_digital_silence(with_room):
    p, tl, sources, heard = with_room
    F = Fraction(tl["fps"])
    (tone,) = [s for s in tl["segments"] if "tone" in s]
    a, b = float(tone["rec"] / F) + 0.03, float((tone["rec"] + tone["n"]) / F) - 0.03
    gap = heard[int(a * 16000):int(b * 16000)]
    rms_db = 20 * np.log10(np.sqrt((gap ** 2).mean()) + 1e-9)
    assert -75 < rms_db < -35, rms_db  # the clip's own room, well under the voice, never zero


def test_without_room_tone_qa_calls_it_too_tight(tmp_path):
    p, tl, sources, heard = _render(tmp_path / "noroom", {"max_room": 0})
    assert not any("tone" in s for s in tl["segments"])
    (j,) = breathing(tl, sources, heard, p.cfg["cut"])
    assert j["tight"] and j["quiet"] < 0.1, j
