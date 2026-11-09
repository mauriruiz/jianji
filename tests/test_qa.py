"""QA on real renders of the golden clip: the good cut passes, deliberately broken renders fail.
Uses the ASR models (QA re-transcribes the render), so this is the slow part of the suite (~1 min)."""
import copy
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from jianji.broll import Vision, describe, sheet
from jianji.cli import _tr_path, compile_edit, load_sources
from jianji.core import FFMPEG, Project
from jianji.media import build_catalog
from jianji import qa as qa_mod
from jianji.qa import run_qa
from jianji.render import render
from jianji.transcribe import Engine

G = Path(__file__).parent / "fixtures" / "golden"


def _project(d: Path, talking: Path = G / "footage" / "golden.mp4"):
    """A rendered golden project in d. `talking` swaps in a variant of the talking clip (same speech timing)."""
    (d / "footage").mkdir(parents=True)
    shutil.copy2(talking, d / "footage" / "golden.mp4")  # catalog ids: A = golden (talking), B = phone B-roll
    shutil.copy2(G / "footage" / "phone_broll.mp4", d / "footage" / "phone_broll.mp4")
    p = Project(str(d))
    cat = build_catalog(p)
    shutil.copy(G / "transcript.A.json", _tr_path(p, "A", cat["sources"]["A"]))  # frozen: no ASR for prep
    (p.out / "edits").mkdir()
    shutil.copy(G / "edit.v001.json", p.out / "edits" / "edit.v001.json")
    cat, sources = load_sources(p, with_audio=True)
    tl, v = compile_edit(p, None, sources)
    return p, cat, sources, tl, render(p, tl, cat, sources, v)


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    p, cat, sources, tl, outs = _project(tmp_path_factory.mktemp("golden"))
    return p, cat, sources, tl, outs, Engine(p.cfg["asr"])


def qa(p, tl, sources, outs, eng):
    res = run_qa(tl, sources, outs, eng, p.cfg["captions"], p.cfg["render"])
    return res, {c["name"]: c["status"] for c in res["checks"]}


def _whisper_misses(monkeypatch, tl, sources, *word_ids):
    """Make QA's re-transcription miss these source-A words, as Whisper does in loud market noise."""
    F = 25
    at = [s["rec"] / F + sources["A"]["words"][k]["s"] - s["in"] for k in word_ids
          for s in tl["segments"] if k in s["kept"]]
    real = qa_mod.transcribe_audio

    def deaf(audio, eng):
        r = real(audio, eng)
        r["words"] = [w for w in r["words"] if min(abs(w["s"] - t) for t in at) > 0.25]
        return r
    monkeypatch.setattr(qa_mod, "transcribe_audio", deaf)


@pytest.mark.models
def test_caption_only_revision_reuses_master_and_sound_checks(tmp_path, monkeypatch):
    """A caption fix doesn't spend a review iteration: same master (hardlink), sound checks carried over."""
    import json
    from jianji.cli import cut
    p, cat, sources, tl, outs = _project(tmp_path / "rev")
    edits = p.out / "edits"
    edit = json.loads((edits / "edit.v001.json").read_text())
    for v, word in ((2, "Idempotency"), (3, "Idempotency,")):
        edit["clips"][2]["fix"] = {"35": "", "36": word}  # c3: Whisper heard "I'd potency"
        (edits / f"edit.v{v:03d}.json").write_text(json.dumps(edit))
        if v == 3:  # by now v002 has a QA result: the expensive checks must not run again
            monkeypatch.setattr(qa_mod, "clipped_words", lambda *a: pytest.fail("re-ran the alignment"))
            monkeypatch.setattr(qa_mod, "asr_diffs", lambda *a: pytest.fail("re-ran Whisper"))
        cut(p, None)
        r = p.out / "renders"
        assert (r / f"master.v{v:03d}.mp4").stat().st_ino == (r / "master.v001.mp4").stat().st_ino
        assert word in (r / f"captions.v{v:03d}.srt").read_text()
    qa3 = json.loads((p.out / "renders" / "qa.v003.json").read_text())
    assert {"clipped_words", "asr_diff", "captions"} <= {c["name"] for c in qa3["checks"]}


@pytest.mark.models
def test_cut_in_background_noise_before_speech_is_not_a_fail(golden, tmp_path, monkeypatch):
    """A false fail seen on real footage: a cut landing in loud background noise ~0.3 s before the first word, and Whisper
    mishearing that word. The word is intact: QA may warn, never fail."""
    p, cat, sources, tl, outs, eng = golden
    w = sources["A"]["words"][24]  # "distributed": first word of c2, which starts ~0.35 s before it
    noisy = tmp_path / "noisy.mp4"
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", G / "footage" / "golden.mp4", "-filter_complex",
                    f"anoisesrc=color=brown:amplitude=0.3:r=48000:d=43.0,"
                    f"volume=0:enable='not(between(t,{w['s'] - 0.45:.2f},{w['s'] - 0.03:.2f}))'[n];"
                    "[0:a][n]amix=inputs=2:normalize=0:duration=first[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", noisy], check=True)
    p2, _, sources2, tl2, outs2 = _project(tmp_path / "proj", noisy)
    _whisper_misses(monkeypatch, tl2, sources2, 24)
    res, st = qa(p2, tl2, sources2, outs2, eng)
    assert res["status"] != "fail" and st["clipped_words"] != "fail", res
    assert st["asr_diff"] == "warn", "the mishearing is still reported, as a warning"


@pytest.mark.models
def test_misheard_short_word_mid_piece_is_not_a_fail(golden, monkeypatch):
    """Another false fail seen on real footage: a short, low-confidence word in the middle of a piece ("es" in "la verdad es que sí")
    that Whisper doesn't hear on the render. Not next to a cut edge, so alignment doesn't judge it."""
    p, cat, sources, tl, outs, eng = golden
    weak = copy.deepcopy(sources)
    weak["A"]["words"][9]["p"] = 0.1  # "is" in "it is how services": unclear in the source too
    _whisper_misses(monkeypatch, tl, weak, 9)
    res, st = qa(p, tl, weak, outs, eng)
    assert res["status"] != "fail" and st["clipped_words"] == "pass", res


@pytest.mark.models
def test_golden_cut_passes(golden):
    p, cat, sources, tl, outs, eng = golden
    res, st = qa(p, tl, sources, outs, eng)
    assert res["status"] == "pass" and res["summary"] == "QA: pass", res
    assert Path(outs["review"]).exists() and Path(outs["srt"]).read_text().count("-->") >= 8


def _frame(path, t: float, vf: str = "") -> np.ndarray:
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                          f"{vf}scale=32:18,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).astype(float)


def test_broll_layer_replaces_picture_not_dialogue(golden):
    p, cat, sources, tl, outs, eng = golden
    (ov,) = tl["overlays"]
    w, h = tl["size"]
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"

    def aroll_at(t):  # what the A-roll would show at timeline time t
        s = next(s for s in tl["segments"] if s["rec"] <= t * 25 < s["rec"] + s["n"])
        return _frame(G / "footage" / "golden.mp4", s["in"] + t - s["rec"] / 25)

    mid = (ov["rec"] + ov["n"] / 2) / 25
    shown = _frame(outs["master"], mid)
    broll = _frame(G / "footage" / "phone_broll.mp4", ov["in"] + ov["n"] / 2 / 25, fill)
    assert np.abs(shown - broll).mean() < 12 < np.abs(shown - aroll_at(mid)).mean()
    after = (ov["rec"] + ov["n"]) / 25 + 0.5
    assert np.abs(_frame(outs["master"], after) - aroll_at(after)).mean() < 12, "A-roll is back after the overlay"
    # dialogue keeps playing under the B-roll: QA's re-transcription (in test_golden_cut_passes) covers every word
    W = sources["A"]["words"]
    assert any(29 in s["kept"] for s in tl["segments"]) and W[29]["t"].lower().startswith("every")


def test_contact_sheet(golden):
    p, cat, *_ = golden
    img, n = sheet(p, "B", cat["sources"]["B"])
    size = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", img],
                          capture_output=True, text=True).stdout.strip()
    w, h = map(int, size.split(","))
    assert n == 16 and w > 4 * 170 and h > 4 * 300, (n, w, h)  # 4x4 grid of vertical thumbs


@pytest.mark.models
def test_vision_model_describes_broll(golden):
    p, cat, *_ = golden
    res = describe(p, "B", cat["sources"]["B"], Vision(p.cfg["broll"]["vlm_model"]))
    assert all(k in res["text"] for k in ("SHOWS:", "BEST:", "ISSUES:")), res["text"]


@pytest.mark.models
def test_cut_inside_a_word_fails(golden):
    p, cat, sources, tl, outs, eng = golden
    bad = copy.deepcopy(tl)
    seg = next(s for s in bad["segments"] if s["clip"] == "c2")  # starts on "distributed" (0.8 s long)
    seg["in"] = sources["A"]["words"][24]["s"] + 0.3  # a broken plan: the piece now starts mid-word
    res, st = qa(p, bad, sources, render(p, bad, cat, sources, 2), eng)
    assert res["status"] == "fail" and st["edges"] == "fail" and st["clipped_words"] == "fail", res
    assert any(i["clip"] == "c2" and i["severity"] == "fail" for i in res["issues"])


@pytest.mark.models
def test_audio_offset_fails(golden):
    p, cat, sources, tl, outs, eng = golden
    shifted = Path(outs["master"]).with_name("shifted.mp4")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", outs["master"], "-itsoffset", "0.12", "-i", outs["master"],
                    "-map", "0:v", "-map", "1:a", "-c", "copy", shifted], check=True)
    res, st = qa(p, tl, sources, {**outs, "master": shifted}, eng)
    assert "fail" in (st["av_offset"], st["av_sync"]), res
