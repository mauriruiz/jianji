"""short profile on a real render: a vertical, picture-led cut (natural sound + on-screen texts, no speech).
Renders 8 s at 1080x1920, so ~20 s."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from jianji.cli import compile_edit, load_sources
from jianji.core import FFMPEG, Project
from jianji.media import build_catalog
from jianji.qa import overlaps, run_qa, safe_zone, text_boxes
from jianji.render import R, mix_audio, render, write_ass
from jianji.transcribe import Engine

G = Path(__file__).parent / "fixtures" / "golden"
EDIT = {
    "v": 1, "profile": "short",
    "clips": [{"id": "p1", "src": "A", "in": 0.0, "out": 3.0}, {"id": "p2", "src": "A", "in": 4.0, "out": 7.0}],
    # two sounding overlays back to back at 2.0 s: no fade dip between them
    "overlays": [{"id": "b1", "src": "A", "in": 3.0, "out": 4.0, "over": "p1", "at": 1.0, "gain_db": -6},
                 {"id": "b2", "src": "A", "in": 7.0, "out": 8.0, "over": "p1", "at": 2.0, "gain_db": -6}],
    "texts": [{"id": "t1", "text": "Las hacen al momento…", "over": "p1", "at": 0.2},
              {"id": "t2", "text": "£13 · la grande", "style": "hi", "over": "p2", "at": 0.5}],
}


@pytest.fixture(scope="module")
def short(tmp_path_factory):
    d = tmp_path_factory.mktemp("short")
    (d / "footage").mkdir()
    shutil.copy2(G / "footage" / "phone_broll.mp4", d / "footage" / "phone_broll.mp4")
    (d / "brief.md").write_text("---\nprofile: short\nlanguage: es\n---\n")
    p = Project(str(d))
    build_catalog(p)
    (p.out / "edits").mkdir()
    (p.out / "edits" / "edit.v001.json").write_text(json.dumps(EDIT))
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    return p, cat, sources, tl, render(p, tl, cat, sources, v)


def _rows(path, t: float) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                          "scale=36:64,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).astype(float).reshape(64, 36)


@pytest.mark.models
def test_vertical_cut_passes_qa(short):
    p, cat, sources, tl, outs = short
    res = run_qa(tl, sources, outs, Engine(p.cfg["asr"]), p.cfg["captions"], p.cfg["render"], texts=p.cfg["texts"],
                 hook=p.cfg["hook"])
    st = {c["name"]: c["status"] for c in res["checks"]}
    assert st["size"] == "pass" and "final" in outs and "1080x1920" in next(c for c in res["checks"] if c["name"] == "size")["detail"]
    assert st["hook"] == "pass" and "picture" in next(c for c in res["checks"] if c["name"] == "hook")["detail"]  # no speech
    assert st["text_readable"] == "pass" and st["clipped_words"] == "pass" and res["status"] != "fail", res
    # p1 would flash 0.8 s before b1, so b1 starts earlier and covers it; b2 is the source's last second, chained
    # after b1: it can't be held 1.2 s, QA counts it (a warning)
    assert st["shot_readable"] == "warn" and [i["msg"] for i in res["issues"] if i["check"] == "shot_readable"] == \
        ["detail shot b2 on screen 1.0s, wants 1.2s"], res


@pytest.mark.models
def test_burned_text_stays_inside_the_safe_zone(short):
    p, cat, sources, tl, outs = short
    res = run_qa(tl, sources, outs, Engine(p.cfg["asr"]), p.cfg["captions"], p.cfg["render"], zone=p.cfg["safe_zone"])
    st = {c["name"]: c["status"] for c in res["checks"]}
    assert st["safe_zone"] == "pass", res


def test_text_under_the_platform_ui_fails(short):
    p, cat, sources, tl, outs = short
    ass = write_ass([], tl, Path(outs["final_ass"]).with_name("too_high.ass"), 1920, p.cfg["captions"],
                    {**p.cfg["texts"], "margin_v": 0.03}, labels=False)  # story text pushed under the top bar
    crossed = safe_zone(text_boxes(ass, tl["size"]), tl["size"], p.cfg["safe_zone"])
    assert any("top" in sides and px > 100 for _, _, sides, px in crossed), crossed


def test_overlap_needs_same_time_same_place_and_different_styles():
    boxes = [("Cap", 0, 2, (100, 1000, 900, 1200), "cap"), ("Hi", 1, 3, (200, 1100, 800, 1300), "callout"),
             ("Txt", 1, 3, (0, 0, 50, 50), "far away"), ("Hi", 5, 6, (100, 1000, 900, 1200), "later")]
    assert overlaps(boxes) == [(1, "cap", "callout")]


def test_callout_and_caption_on_screen_together_dont_overlap(short):
    p, cat, sources, tl, outs = short
    t2 = next(t for t in tl["texts"] if t["id"] == "t2")  # the "hi" callout
    cue = (float(t2["rec"] / 30), float((t2["rec"] + t2["n"]) / 30), "Están en\nPlaza Mayor")  # a two-line caption
    ass = write_ass([cue], tl, Path(outs["final_ass"]).with_name("both.ass"), 1920, p.cfg["captions"],
                    p.cfg["texts"], labels=False)
    assert overlaps(text_boxes(ass, tl["size"])) == []


def test_texts_burned_into_final_only(short):
    p, cat, sources, tl, outs = short
    top = slice(7, 16)  # txt sits under the top bar (margin_v 0.12)
    with_text = np.abs(_rows(outs["final"], 1.0)[top] - _rows(outs["master"], 1.0)[top]).mean()
    without = np.abs(_rows(outs["final"], 5.6)[top] - _rows(outs["master"], 5.6)[top]).mean()
    assert with_text > 2 and with_text > 20 * without, (with_text, without)  # measured 4.1 vs 0.05


def test_back_to_back_overlays_dont_dip(short):
    p, cat, sources, tl, outs = short
    bed = mix_audio(p, tl, cat, p.cfg["render"]["xfade_frames"]) - mix_audio(p, {**tl, "overlays": []}, cat, 2)
    rms = lambda t, w=0.05: float(np.sqrt((bed[round((t - w) * R):round((t + w) * R)] ** 2).mean()))
    assert rms(2.0) > 0.4 * rms(1.6), (rms(2.0), rms(1.6))  # 0.25 s fades on both sides would leave ~0.1
