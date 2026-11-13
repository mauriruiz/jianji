"""How long a short cut out of a long video may run: `[shorts]` in your config.toml. What you aim for is a warning,
your limits are a QA fail, and compile says it before anything renders. Renders 4 s once, ~6 s."""
import pytest
from conftest import cut_short, quadrants
from test_stack import CONFIG, EDIT

from jianji import cli, core
from jianji.compile import EditError, judge_length
from jianji.core import Project
from jianji.qa import HEAVY, run_qa

RULE = {"length": [35, 60], "min_length": 25, "max_length": 60}


def test_what_you_aim_for_warns_and_your_limits_fail():
    assert judge_length(47.0, RULE) is None and judge_length(35.0, RULE) is None and judge_length(60.0, RULE) is None
    sev, msg = judge_length(31.2, RULE)
    assert sev == "warn" and msg.startswith("31.2 s, under the 35-60 s you aim for") and "among the best" in msg
    sev, msg = judge_length(19.83, RULE)
    assert sev == "fail" and "under the 25 s you never go below" in msg and "after the hook" in msg
    assert judge_length(61.0, RULE)[0] == "fail" and "never go above" in judge_length(61.0, RULE)[1]
    assert judge_length(64.0, {"length": [20, 45], "max_length": 90})[0] == "warn"  # limits and aim are yours to set
    assert judge_length(5.0, {"length": [35, 60]})[0] == "warn" and judge_length(5.0, {"min_length": 10})[0] == "fail"
    with pytest.raises(EditError, match="shortest, longest"):
        judge_length(40.0, {"length": 45})


@pytest.fixture(scope="module")
def short(tmp_path_factory):
    d = tmp_path_factory.mktemp("length")
    (d / "footage").mkdir()
    quadrants(d / "footage" / "rec.mp4")
    (d / "brief.md").write_text("---\nprofile: stream\n---\n")
    return cut_short(d, CONFIG, EDIT)  # a 4 s short, cut before anyone said how long they want them


def _mine(monkeypatch, rule):
    cfg = {"schema": core.SCHEMA, "defaults": {"disk": {"min_free_gb": 1}}, "shorts": rule}
    monkeypatch.setattr(core, "global_config", lambda: cfg)


def test_compile_says_it_before_rendering_and_status_shows_your_rule(short, monkeypatch, capsys):
    video, p = short
    _mine(monkeypatch, RULE)
    p = Project(str(video.dir), short="1")
    tl, _ = cli.compile_edit(p, None, cli.load_sources(p)[1])
    assert [w for w in tl["warnings"] if w.startswith("length:")] == [
        "length: 4.0 s, under the 25 s you never go below: build the idea out after the hook (the setup it needs, "
        "the point, a line that closes it), or take another moment (QA fails it)"]
    capsys.readouterr()
    cli.status(Project(str(video.dir)))
    assert "short length: aim for 35-60 s; never under 25 s; never over 60 s" in capsys.readouterr().out
    _mine(monkeypatch, {})  # nobody said: no rule, no warning, no line
    p = Project(str(video.dir), short="1")
    assert not any(w.startswith("length:") for w in cli.compile_edit(p, None, cli.load_sources(p)[1])[0]["warnings"])
    cli.status(Project(str(video.dir)))
    assert "short length" not in capsys.readouterr().out


def test_qa_fails_a_short_past_your_limits_and_warns_off_your_aim(short):
    video, p = short
    d = p.out / "renders"
    tl = core.read_json(d / "timeline.v001.json")
    _, sources = cli.load_sources(p)
    outs = {"master": d / "master.v001.mp4", "srt": d / "captions.v001.srt", "final": d / "final.v001.mp4"}
    heavy = {"checks": [{"name": h, "status": "pass", "detail": ""} for h in HEAVY], "issues": []}  # no models here
    qa = lambda rule: run_qa(tl, sources, outs, None, p.cfg["captions"], p.cfg["render"], reuse=heavy, length=rule)
    res = qa(RULE)
    (c,) = [c for c in res["checks"] if c["name"] == "length"]
    assert c["status"] == "fail" and c["detail"] == "4.0 s (you aim for 35-60 s)" and res["status"] == "fail"
    assert [i["msg"] for i in res["issues"] if i["check"] == "length"][0].startswith("4.0 s, under the 25 s")
    assert [c["status"] for c in qa({"length": [35, 60]})["checks"] if c["name"] == "length"] == ["warn"]
    assert [c["status"] for c in qa({"length": [3, 60], "min_length": 2})["checks"] if c["name"] == "length"] == ["pass"]
    assert not [c for c in qa(None)["checks"] if c["name"] == "length"]
