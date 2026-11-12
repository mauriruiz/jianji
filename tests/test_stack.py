"""A short cut out of a long video: a work folder of its own inside the video's, clips with a two-panel layout (one
region of the source over another), the outro, the preview, and the same picture as an FCPXML for Resolve.
The source is synthetic: four coloured quadrants and a tone. Renders 4 s at 1080x1920, so ~15 s."""
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np
import pytest
from conftest import cut_short, quadrants

from jianji import core, resolve
from jianji.compile import EditError, _stacked, add_outro
from jianji.core import FFMPEG, Project, read_json
from jianji.media import ffprobe
from jianji.render import NBSP, panels, picture_filter, shown

LAYOUT = {"top": [0.5, 0, 1, 0.5], "bottom": [0, 0.5, 0.5, 1]}  # red over green
EDIT = {"v": 1, "clips": [{"id": "p1", "src": "A", "in": 0.0, "out": 2.0, "layout": "cam"},
                          {"id": "p2", "src": "A", "in": 2.0, "out": 4.0, "layout": "cam", "crop": [0, 0, 0.5, 0.5]}],
        "texts": [{"id": "t1", "text": "It works", "over": "p1", "at": 0.2}]}


CONFIG = (f'[layouts.cam]\ntop = {LAYOUT["top"]}\nbottom = {LAYOUT["bottom"]}\n\n'
          '[outro]\ntext = "Live Mon-Thu 22:30"\nseconds = 1.5\n')


@pytest.fixture(scope="module")
def short(tmp_path_factory):
    d = tmp_path_factory.mktemp("stream")
    (d / "footage").mkdir()
    quadrants(d / "footage" / "screen.mp4")
    (d / "brief.md").write_text("---\nprofile: stream\n---\n")
    video, p = cut_short(d, CONFIG, EDIT)
    return video, p, read_json(p.out / "renders" / "timeline.v001.json")


def _frame(path, t: float) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                          "scale=54:96", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).astype(int).reshape(96, 54, 3)


def test_a_short_lives_inside_the_video_and_shares_its_cache(short):
    video, p, tl = short
    assert p.out == video.out / "shorts" / "1" and p.profile == "short" and p.name.endswith("short 1")
    assert (p.out / "cache").resolve() == (video.out / "cache").resolve() and any((video.out / "cache" / "seg").iterdir())
    assert not (video.out / "catalog.json").is_symlink() and read_json(p.out / "catalog.json") == read_json(video.out / "catalog.json")
    assert p.cfg["stream"] == video.cfg["stream"] and p.cfg["asr"] == video.cfg["asr"]  # heard and read as the video
    assert not (video.out / "edits").exists() and tl["size"] == [1080, 1920]


def test_each_panel_shows_its_region(short):
    video, p, tl = short
    assert [s["clip"] for s in tl["segments"]] == ["p1", "p2"]  # back to back in the source, but not the same picture
    is_ = lambda px, rgb: all(abs(int(a) - b) < 60 for a, b in zip(px, rgb))
    a, b = _frame(p.out / "renders" / "master.v001.mp4", 1.0), _frame(p.out / "renders" / "master.v001.mp4", 3.0)
    assert is_(a[:33].mean((0, 1)), (255, 0, 0)) and is_(a[37:].mean((0, 1)), (0, 128, 0)), a.mean(1)  # red over green
    assert is_(b[:33].mean((0, 1)), (255, 0, 0)) and is_(b[37:].mean((0, 1)), (0, 0, 255))  # its crop: blue below


def test_the_filter_fills_two_panels_exactly():
    st = {**LAYOUT, "split": 0.36}
    assert panels(st, 1080, 1920) == [(LAYOUT["top"], (0, 0, 1080, 692)), (LAYOUT["bottom"], (0, 692, 1080, 1920))]
    vf = picture_filter({"stack": st}, 30, 1080, 1920, "yuv420p")
    assert "crop=iw*0.5000:ih*0.5000:iw*0.5000:ih*0.0000,scale=1080:692:force_original_aspect_ratio=increase,crop=1080:692" in vf
    assert "scale=1080:1228:force_original_aspect_ratio=increase,crop=1080:1228[qb];[qt][qb]vstack" in vf


def test_layouts_are_checked_with_the_edit():
    clip = {"id": "c1", "src": "A", "in": 0, "out": 2, "layout": "cam"}
    assert _stacked({**clip, "crop": [0, 0, 0.5, 0.5]}, {"cam": LAYOUT})["stack"] == \
        {"top": LAYOUT["top"], "bottom": [0, 0, 0.5, 0.5], "split": 0.36}
    assert _stacked({"id": "c2", "src": "A", "in": 0, "out": 2, "crop": [0, 0, 0.5, 0.5]}, None).get("stack") is None
    for bad, why in (({}, "isn't defined"), ({"cam": {**LAYOUT, "top": [0.5, 0, 0.4, 1]}}, "top must be"),
                     ({"cam": {"top": LAYOUT["top"]}}, "bottom must be"), ({"cam": {**LAYOUT, "split": 1}}, "split")):
        with pytest.raises(EditError, match=why):
            _stacked(clip, bad)
    with pytest.raises(EditError, match="sped up"):
        _stacked({**clip, "speed": 8}, {"cam": LAYOUT})


def test_layouts_and_outro_come_from_your_layer(short, monkeypatch):
    video, p, tl = short
    monkeypatch.setattr(core, "global_config", lambda: {"schema": core.SCHEMA, "layouts": {"wide": LAYOUT, "cam": {"split": 0.5}},
                                                        "outro": {"text": "from the layer", "seconds": 9}})
    cfg = Project(str(video.dir), short="1").cfg
    assert cfg["layouts"]["wide"] == LAYOUT and cfg["layouts"]["cam"] == {**LAYOUT, "split": 0.5}
    assert cfg["outro"] == {"text": "Live Mon-Thu 22:30", "seconds": 1.5}  # this short's own config.toml wins


def test_the_outro_breaks_where_you_break_it():
    tl = add_outro({"fps": "30", "frames": 300, "texts": [], "warnings": []}, {"text": "Live Mon-Thu 22:30\nSundays 11:00"}, {})
    assert shown(tl["texts"][-1], [], {"line_chars": 10}).replace(NBSP, " ") == "Live Mon-Thu 22:30\nSundays 11:00"


def test_the_outro_closes_the_cut(short):
    video, p, tl = short
    o = tl["texts"][-1]
    assert (o["id"], o["text"], o["style"]) == ("outro", "Live Mon-Thu 22:30", "hi") and o["rec"] + o["n"] == tl["frames"]
    assert o["n"] >= 45  # 1.5 s, or longer: its reading time
    base = {"fps": "30", "frames": 300, "warnings": [], "texts": [
        {"id": "a", "text": "x", "style": "hi", "rec": 100, "n": 150}, {"id": "b", "text": "x", "style": "hi", "rec": 200, "n": 90},
        {"id": "c", "text": "x", "style": "txt", "rec": 250, "n": 50}]}
    out = add_outro(base, {"text": "Bye", "seconds": 3}, {})
    assert [(t["id"], t["rec"], t["n"]) for t in out["texts"]] == [("a", 100, 110), ("c", 250, 50), ("outro", 210, 90)]
    assert add_outro({**base, "texts": []}, {}, {})["texts"] == []  # no [outro]: nothing


def test_preview_is_small_and_plays_anywhere(short):
    video, p, tl = short
    f = p.out / "preview.mp4"
    st = {s["codec_type"]: s for s in ffprobe(f)["streams"]}
    assert (st["video"]["codec_name"], st["video"]["width"], st["video"]["height"]) == ("h264", 540, 960)
    assert st["audio"]["codec_name"] == "aac" and f.stat().st_size < 8e6


def test_resolve_gets_two_trimmed_clips_of_the_same_frames(short):
    video, p, tl = short
    f = p.out / "resolve" / f"{p.name} v001.fcpxml"
    spine = ET.parse(f).getroot().find(".//spine")
    bottom = spine[0]
    top = next(c for c in bottom if c.tag == "asset-clip")
    nums = lambda el, tag, attr: [float(x) for x in el.find(tag).get(attr).split()]
    trim = lambda el: [float(el.find("adjust-crop/trim-rect").get(k)) for k in ("left", "top", "right", "bottom")]
    # 1280x720 in 1080x1920, cover fit 2.6667: worked by hand from the two regions and the 692 px split, then into
    # Resolve's units: sides and x are fractions of the frame applied to the filled image (16/9 squared: 3.1605)
    assert trim(top) == pytest.approx([53.0527 * 9 / 16, 0, 3.0527 * 9 / 16, 50], abs=0.01)
    assert nums(top, "adjust-transform", "scale") == pytest.approx([0.72083] * 2, abs=1e-4)
    assert nums(top, "adjust-transform", "position") == pytest.approx([-32.0370 / 3.1605, 13.9583], abs=0.01)
    assert trim(bottom) == pytest.approx([12.6323 * 9 / 16, 50, 62.6323 * 9 / 16, 0], abs=0.01)
    assert nums(bottom, "adjust-transform", "scale") == pytest.approx([1.27917] * 2, abs=1e-4)
    assert nums(bottom, "adjust-transform", "position") == pytest.approx([56.8519 / 3.1605, 13.9583], abs=0.01)
    assert top.find("adjust-conform").get("type") == bottom.find("adjust-conform").get("type") == "fill"
    assert top.get("lane") == "1" and top.get("srcEnable") == "video" and top.get("ref") == bottom.get("ref")
    assert (top.get("offset"), top.get("duration")) == (bottom.get("start"), bottom.get("duration"))
    assert [t.get("name") for t in spine.iter("title")] == ["t1", "outro"]
    media = [Path(unquote(urlparse(m.get("src")).path)) for m in ET.parse(f).getroot().iter("media-rep")]
    assert "outro-box.png" in [m.name for m in media] and all(m.exists() for m in media)
    assert all(m.name.count(".") == 1 for m in media if m.suffix == ".png")  # Resolve drops the middle of "a.b.png"
    assert "Two-panel clips" in (p.out / "resolve" / "IMPORT.md").read_text()
    with pytest.raises(SystemExit, match="two-panel"):
        resolve.pull(p, str(f))
