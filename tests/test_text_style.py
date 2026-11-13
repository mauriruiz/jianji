"""How burned text looks: a style of its own per kind of text, captions centred in a round box that fits them,
line spacing, fonts from your layer. Rendered by libass over a grey frame and measured."""
import subprocess

import numpy as np

from jianji import core
from jianji.core import FFMPEG
from jianji.qa import text_boxes
from jianji.render import burn, text_style, write_ass

W, H = 540, 960
TL = {"fps": "30", "size": [W, H], "sections": [], "segments": [], "texts": []}
CAP = {"font": "Helvetica", "size": 0.05, "outline": 0.0, "margin_l": 0.05, "margin_r": 0.05, "centre": 0.8,
       "box": "&H80000000", "box_radius": 0.01, "box_pad": [0.02, 0.005]}
CUES = [(0.0, 1.0, "one line"), (1.0, 2.0, "two lines of it\nthis time")]


def _frame(ass, t: float) -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c=0x808080:s={W}x{H}:r=10:d={t + 0.2}", "-vf",
                          burn(ass.name), "-ss", str(t), "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True, cwd=ass.parent).stdout
    return np.frombuffer(raw, np.uint8).astype(int).reshape(H, W)


def _span(mask, axis) -> tuple[int, int]:
    v = np.nonzero(mask.any(axis))[0]
    return int(v[0]), int(v[-1])


def test_each_kind_of_text_has_its_own_style():
    tc = {"size": 0.05, "bold": False, "outline": 0.002, "hi_colour": "&H00112233", "hi_align": 8, "chat_margin_v": 0.5,
          "hi": {"bold": True, "size": 0.04}}
    hi, chat, txt = (text_style(tc, s) for s in ("hi", "chat", "txt"))
    assert (hi["back"], hi["align"], hi["bold"], hi["size"], hi["outline"]) == ("&H00112233", 8, True, 0.04, 0.002)
    assert hi["colour"] == "&H00000000"  # black on its box, as ever
    assert (chat["margin_v"], chat["size"], chat["bold"]) == (0.5, 0.05 * 0.8, False)
    assert (txt["size"], txt["back"], text_style(None, "txt")["bold"]) == (0.05, "&H00000000", True)


def test_captions_sit_centred_in_a_round_box_that_fits_them(tmp_path):
    ass = write_ass(CUES, TL, tmp_path / "c.ass", H, CAP, labels=False)
    assert ",1,0,0,2," in next(x for x in ass.read_text().splitlines() if x.startswith("Style: Cap,"))  # no outline
    for t, lines in ((0.5, 1), (1.5, 2)):
        img = _frame(ass, t)
        box, ink = (img > 50) & (img < 90), img > 200  # grey under black at half strength; white letters
        (x0, x1), (y0, y1) = _span(box | ink, 0), _span(box | ink, 1)
        assert abs((x0 + x1) / 2 - W / 2) <= 1 and abs((y0 + y1) / 2 - 0.8 * H) <= 1  # one line or two: same centre
        assert abs((y1 - y0 + 1) - (lines * 48 + 2 * 0.005 * H)) <= 2
        ix0, ix1 = _span(ink, 0)
        assert abs((ix0 - x0) - 0.02 * H) <= 3 and abs((x1 - ix1) - 0.02 * H) <= 3  # as wide as the words, plus the pad
        assert not box[y0, x0] and box[y0 + 12, x0] and box[y0, x0 + 12]  # round corners
    boxes = text_boxes(ass, [W, H])  # QA sees each caption once, box included
    assert [(s, t) for s, _, _, _, t in boxes] == [("Cap", "one line"), ("Cap", "two lines of it this time")]
    bx = boxes[1][3]
    assert abs(bx[0] - x0) <= 2 and abs(bx[2] - x1) <= 3 and abs(bx[1] - y0) <= 2 and abs(bx[3] - y1) <= 3


def test_captions_without_a_box_stay_as_they_were(tmp_path):
    ass = write_ass(CUES, TL, tmp_path / "c.ass", H, {"font": "Helvetica", "size": 0.05}, labels=False)
    events = [x for x in ass.read_text().splitlines() if x.startswith("Dialogue:")]
    assert events == ["Dialogue: 0,0:00:00.00,0:00:01.00,Cap,,0,0,0,,one line",
                      "Dialogue: 0,0:00:01.00,0:00:02.00,Cap,,0,0,0,,two lines of it\\Nthis time"]


def test_line_spacing_opens_the_lines_and_adds_no_words(tmp_path):
    tl = {**TL, "texts": [{"id": "t1", "text": "First line\nsecond line", "style": "txt", "rec": 0, "n": 30}]}
    place = {"texts": {"t1": {"cx": W / 2, "top": 200, "a": 0.0, "z": 1.0}}}
    rows = []
    for ls in (1.0, 1.5):
        ass = write_ass([], tl, tmp_path / f"t{ls}.ass", H, None, {"font": "Helvetica", "size": 0.05, "line_spacing": ls},
                        labels=False, place=place)
        ink = _frame(ass, 0.5) > 200
        mid = (_span(ink, 1)[0] + _span(ink, 1)[1]) // 2
        rows.append((_span(ink[:mid], 1), _span(ink[mid:], 1)[0] + mid))
        assert [t for *_, t in text_boxes(ass, [W, H])] == ["First line second line"]
    (a0, _), b0 = rows[0]
    (a1, _), b1 = rows[1]
    assert abs((b1 - a1) - (b0 - a0) - 0.5 * 48) <= 2  # half a line more between the two


def test_colours_come_out_as_written(tmp_path):
    """libass bends the colours of a script that doesn't say which matrix they were picked on: a yellow of
    (255, 215, 0) came out (255, 204, 0)."""
    tl = {**TL, "texts": [{"id": "h1", "text": "MMMM\nMMMM", "style": "hi", "rec": 0, "n": 30}]}
    ass = write_ass([], tl, tmp_path / "h.ass", H, None, {"font": "Helvetica", "size": 0.1, "hi_colour": "&H0000D7FF"},
                    labels=False)
    raw = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r=10:d=1", "-vf",
                          f"setparams=colorspace=bt709,{burn(ass.name)}", "-frames:v", "1", "-pix_fmt", "rgb24", "-f",
                          "rawvideo", "-"], capture_output=True, check=True, cwd=tmp_path).stdout
    img = np.frombuffer(raw, np.uint8).astype(int).reshape(H, W, 3)
    box = img[(img[..., 0] > 200) & (img[..., 2] < 60)]
    assert len(box) > 1000 and np.abs(np.median(box, 0) - (255, 215, 0)).max() <= 3, np.median(box, 0)


def test_fonts_in_your_layer_are_found(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "HOME", tmp_path)
    assert burn("x.ass") == "subtitles=filename=x.ass"
    (tmp_path / "fonts").mkdir()
    assert burn("x.ass", ":alpha=1") == f"subtitles=filename=x.ass:alpha=1:fontsdir={tmp_path / 'fonts'}"


def test_a_text_sits_at_its_own_margin_when_that_spot_is_free():
    from fractions import Fraction

    from jianji.place import place_texts, slots
    zone = {"size": [1080, 1920], "top": 270, "bottom": 670, "left": 65, "right": 150}
    rows = lambda pref: sorted({top for _, _, top in slots(1080, 1920, zone, 400, 100, pref)})
    assert rows(288) == rows(290.5) == rows(None) and len(rows(None)) == 5  # above the zone, or a row already
    assert rows(518) == sorted([*rows(None), 518])
    tx = {"size": 0.046, "hi_margin_v": 0.27}
    text = {"id": "h1", "text": "A callout this long", "style": "hi", "rec": 0, "n": 60}
    free = [{"t": k / 4, "faces": [], "salient": []} for k in range(9)]
    face = [{**f, "faces": [[0.1, 0.25, 0.9, 0.33, 0.9]]} for f in free]  # a face all along that row
    at = lambda frames: place_texts([text], Fraction(30), frames, tx, 1080, 1920, zone, [], 2.0)[0]["h1"]
    assert at(free)["top"] == 518 and at(face)["top"] != 518 and at(face)["face"] <= 0.1
