"""Burned text vs faces, on a real face (tests/fixtures/face: NASA portrait, public domain). The story line's and
the badge's default slots both sit on the head; placement must find them clear spots and QA must agree."""
import json
import shutil
from pathlib import Path

import pytest

from jianji.cli import compile_edit, load_sources
from jianji.core import Project
from jianji.media import build_catalog
from jianji.place import caption_lanes
from jianji.qa import text_boxes, text_over_faces
from jianji.render import render, write_ass
from jianji.vision import detect

FACE = Path(__file__).parent / "fixtures" / "face"
EDIT = {"v": 1, "clips": [{"id": "p1", "src": "A", "in": 0.0, "out": 6.0}],
        "texts": [{"id": "t1", "text": "Una carbonara a las 3 de la mañana", "over": "p1", "at": 0.3, "dur": 2.4},
                  {"id": "b1", "text": "Pasta&Co · Lavapiés", "style": "hi", "over": "p1", "at": 3.0, "dur": 2.4}]}


@pytest.fixture(scope="module")
def face(tmp_path_factory):
    d = tmp_path_factory.mktemp("face")
    (d / "footage").mkdir()
    shutil.copy2(FACE / "footage" / "face.mp4", d / "footage" / "face.mp4")
    (d / "brief.md").write_text("---\nprofile: short\nlanguage: es\n---\n")
    p = Project(str(d))
    build_catalog(p)
    (p.out / "edits").mkdir()
    (p.out / "edits" / "edit.v001.json").write_text(json.dumps(EDIT))
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    outs = render(p, tl, cat, sources, v)
    return p, tl, outs, detect(p, Path(outs["master"]))


def test_vision_sees_the_face(face):
    p, tl, outs, frames = face
    assert sum(bool(f["faces"]) for f in frames) >= 0.9 * len(frames), "a face in (nearly) every frame"


def test_badge_and_story_line_avoid_the_face(face):
    p, tl, outs, frames = face
    placed = json.loads((p.out / "renders" / "placement.v001.json").read_text())
    assert set(placed["texts"]) == {"t1", "b1"} and all(x["face"] <= 0.1 for x in placed["texts"].values()), placed
    want = {t["id"]: t["n"] / 30 for t in tl["texts"]}  # moved, never shortened: each keeps its reading time
    assert all(abs(x["z"] - x["a"] - want[k]) < 0.002 for k, x in placed["texts"].items()), (placed, want)
    on_face, _ = text_over_faces(text_boxes(Path(outs["final_ass"]), tl["size"]), frames, tl["size"])
    assert on_face == [], on_face  # measured on the real libass boxes of the final
    assert placed["log"], "moving off the default slot is logged for review.md"


def test_default_slot_would_cover_the_face(face):
    p, tl, outs, frames = face
    naive = write_ass([], tl, Path(outs["final_ass"]).with_name("naive.ass"), tl["size"][1], p.cfg["captions"],
                      p.cfg["texts"], labels=False)  # no placement: the profile's default slots
    on_face, _ = text_over_faces(text_boxes(naive, tl["size"]), frames, tl["size"])
    # the story line covers the forehead, the badge the eyes: QA would fail both
    assert [t[0][4] for t in on_face] == ["Una carbonara a las 3 de la mañana", "Pasta&Co · Lavapiés"], on_face


def test_review_render_draws_the_faces(face):
    p, tl, outs, frames = face
    ass = Path(outs["review"]).with_suffix(".ass").read_text()
    assert ass.count(",Face,,") >= 20  # one faint box per sample, 4 per second


def test_caption_lane_switch_is_stable():
    """A face drops into the caption lane from 3 s to 5 s: those cues and their close neighbours move up together,
    the rest stay; no cue flips on its own."""
    frames = [{"t": t / 4, "faces": [[0.3, 0.62, 0.7, 0.8, 0.9]] if 12 <= t < 20 else [[0.3, 0.2, 0.7, 0.35, 0.9]],
               "salient": []} for t in range(40)]
    cues = [(k * 0.8, k * 0.8 + 0.7, "dos líneas de\nsubtítulo aquí") for k in range(12)]
    cap = {"size": 0.046, "margin_l": 0.044, "margin_r": 0.089, "margin_v": 0.36}
    lanes = caption_lanes(cues, frames, cap, 1080, 1920, 0.15)
    alt = [k for k, x in enumerate(lanes) if x == "alt"]
    assert alt and alt == list(range(alt[0], alt[-1] + 1)), lanes  # one contiguous run, no flicker
    assert all(lanes[k] == "alt" for k, (a, z, _) in enumerate(cues) if 3.0 <= (a + z) / 2 < 5.0), lanes
    assert lanes[0] == "main" and lanes[-1] == "main", lanes
