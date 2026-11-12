"""Stream profile pieces without a stream: what's on screen from OCR lines, what may be private, sped-up bridges,
zoom and blur on the picture, reordered sections and the chat/chapter cards."""
import json
import tomllib
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from jianji.compile import EditError, compile_timeline
from jianji.render import picture_filter
from jianji.stream import classify, private

G = Path(__file__).parent / "fixtures" / "golden"
CUT = tomllib.loads((Path(__file__).parent.parent / "jianji" / "profiles" / "stream.toml").read_text())["cut"]


def test_screen_is_classified_from_its_words():
    assert classify(["pub fn tick(&mut self) {", "let grid = Grid::new();", "use crate::space::Space;", "grid.rs"]) == "editor"
    assert classify(["~/dev/engine $ cargo test", "Compiling engine v0.1.0", "Finished dev"]) == "terminal"
    assert classify(["https://github.com/someone/engine", "Pull requests"]) == "browser"
    assert classify(["excalidraw.com", "Space", "Grid"]) == "diagram"


def test_private_things_on_screen_are_found():
    box = [0.1, 0.1, 0.4, 0.15, 0.9]
    lines = [["contact: dev@example.com", *box], ["OPENAI_API_KEY=sk-" + "fake" * 8, *box],  # built here: no key-shaped literal
             ["WhatsApp: Mamá: ¿vienes a cenar?", 0.75, 0.02, 0.98, 0.08, 0.9], ["cliente ACME internal", *box],
             ["fn main() {}", *box]]
    kinds = [k for k, _, _ in private(lines, ["ACME"])]
    assert kinds == ["email", "api key", ".env", "notification", "never_show 'ACME'"], kinds


@pytest.fixture
def sources():
    tr = json.loads((G / "transcript.A.json").read_text())
    return {"A": {**tr, "dur": 42.92, "fps": "25/1", "w": 640, "h": 360, "env": np.load(G / "env.A.npy"),
                  "spans": [[2.0, 47.0]]}}


def test_bridge_crop_blur_reorder_and_cards(sources):
    e = {"sections": [{"id": "S1", "title": "late"}, {"id": "S2", "title": "early"}],
         "clips": [{"id": "c5", "sec": "S1", "src": "A", "from": 70, "to": 74, "crop": [0.1, 0.1, 0.6, 0.6]},
                   {"id": "w", "sec": "S2", "src": "A", "in": 0.0, "out": 8.0, "speed": 8,
                    "blur": [[0.7, 0.0, 1.0, 0.1]]},
                   {"id": "c1", "sec": "S2", "src": "A", "from": 0, "to": 6}],
         "texts": [{"id": "ch", "text": "Parte 2", "style": "chapter", "over": "w", "at": 0},
                   {"id": "q", "text": "Chat: ¿por qué Rust?", "style": "chat", "over": "c1", "at": 0}]}
    tl = compile_timeline(e, sources, CUT)
    w = next(s for s in tl["segments"] if s["clip"] == "w")
    assert w["speed"] == 8 and w["n"] == 25  # 8 s of work in 1 s, over the take's room tone if it has any
    assert next(s for s in tl["segments"] if s["clip"] == "c5")["crop"] == [0.1, 0.1, 0.6, 0.6]
    assert w["blur"] == [[0.7, 0.0, 1.0, 0.1]]
    assert tl["reorders"] and tl["reorders"][0]["sec"] == "S2"  # S2 starts earlier in the take than S1
    assert {t["style"] for t in tl["texts"]} == {"chapter", "chat"}
    with pytest.raises(EditError, match="style"):
        compile_timeline({**e, "texts": [{"id": "x", "text": "a", "style": "huge", "over": "c1"}]}, sources, CUT)


def test_picture_filter_speeds_zooms_and_blurs():
    vf = picture_filter({"speed": 8, "crop": [0.1, 0.2, 0.6, 0.7], "blur": [[0.7, 0.0, 1.0, 0.1]]}, Fraction(30),
                        1920, 1080, "yuv420p")
    assert vf.startswith("setpts=PTS/8,fps=30,crop=iw*0.5000:ih*0.5000:iw*0.1000:ih*0.2000,scale=1920:1080")
    assert "crop=576:108:1344:0,boxblur" in vf and vf.endswith("overlay=1344:0,format=yuv420p")


def test_emails_on_screen_tight_and_allowed():
    """A real address on screen fails; OCR of code that looks like one doesn't (`cfg.validate_x@Q.ie.ehcl:`, a
    decorator, a version pin), nor GitHub's noreply addresses in a git log, nor the addresses you allow."""
    box = [0.1, 0.1, 0.4, 0.15, 0.9]
    seen = lambda text, allow=(): [k for k, _, _ in private([[text, *box]], [], list(allow))]
    assert seen("Author: Ana <ana@example.com>") == ["email"]
    assert seen("let ok = cfg.validate_conti@Q.ie.ehcl:") == []
    assert seen("@property def name(self):") == [] and seen("uses: actions/checkout@v4.2") == []
    assert seen("Author: dev <123+dev@users.noreply.github.com>") == []
    assert seen("contact me@example.org", ["Me@Example.org"]) == []
    assert seen("me@example.org and boss@example.org", ["me@example.org"]) == ["email"]


def test_marks_made_while_recording_count_as_mark_that(tmp_path):
    from jianji.stream import cues_in, marks_for
    rec = tmp_path / "obs" / "2026-03-01 22-30-05.mkv"
    rec.parent.mkdir()
    rec.touch()
    assert marks_for(rec) == []
    rec.with_suffix(".marks").write_text("# pressed on stream\n3761.5\n1:02:41 the grid renders\n\n62:05,25\nlater, maybe\n12\n")
    marks = [(12.0, ""), (3725.25, ""), (3761.0, "the grid renders"), (3761.5, "")]
    assert marks_for(rec) == marks
    link = tmp_path / "video" / "footage" / rec.name  # as `jian ingest` leaves it: the marks stay by the recording
    link.parent.mkdir(parents=True)
    link.symlink_to(rec)
    assert marks_for(link) == marks
    assert cues_in("ok, it works now", marked=True) == ["works", "mark"] and cues_in("nothing said", marked=True) == ["mark"]
    assert cues_in("mark that, it works", marked=True) == ["mark", "works"] and cues_in("nothing said") == []
