"""Hooks: your command runs on an event with the facts in JIAN_* variables; text from a video never becomes shell,
and a broken hook never breaks a run."""
import shutil
from pathlib import Path

import pytest

from jianji import cli, core, doctor

G = Path(__file__).parent / "fixtures" / "golden"
DUMP = ["sh", "-c", 'env | grep "^JIAN_" | sort > "$HOOK_OUT"']


@pytest.fixture
def hooks(tmp_path, monkeypatch):
    cfg = {"schema": core.SCHEMA, "hooks": {}}
    monkeypatch.setattr(core, "global_config", lambda: cfg)
    monkeypatch.setattr(doctor, "global_config", lambda: cfg)  # doctor imported it by name
    monkeypatch.setenv("HOOK_OUT", str(tmp_path / "out.txt"))
    monkeypatch.setenv("JIANJI_VIDEOS_ROOT", str(tmp_path / "videos"))
    return cfg["hooks"], tmp_path / "out.txt"


def test_facts_arrive_as_variables_and_are_never_run(hooks, tmp_path):
    cfg, out = hooks
    cfg["on_queue_ready"] = 'sh -c \'printf "%s" "$JIAN_TITLE" > "$HOOK_OUT"\''  # a string is split, not given to a shell
    evil = f'$(touch {tmp_path}/pwned); `touch {tmp_path}/pwned`; " && touch {tmp_path}/pwned'
    core.hook("queue_ready", title=evil, count=None)
    assert out.read_text() == evil and not (tmp_path / "pwned").exists()


def test_ingest_fires_on_ingest(hooks, tmp_path):
    cfg, out = hooks
    cfg["on_ingest"] = DUMP
    rec = tmp_path / "2026-03-01 22-30-05.mp4"
    shutil.copy2(G / "footage" / "phone_broll.mp4", rec)
    cli.main(["ingest", str(rec)])
    got = dict(x.split("=", 1) for x in out.read_text().splitlines())
    assert got == {"JIAN_EVENT": "ingest", "JIAN_VIDEO": "2026-03-01-22-30-05", "JIAN_SOURCE": str(rec.resolve()),
                   "JIAN_DIR": str((tmp_path / "videos").resolve() / "2026-03-01-22-30-05")}


def test_a_failed_run_fires_on_error_and_still_fails(hooks):
    cfg, out = hooks
    cfg["on_error"] = DUMP
    with pytest.raises(SystemExit, match="video folder not found"):
        cli.main(["status", "no-such-video"])
    got = dict(x.split("=", 1) for x in out.read_text().splitlines())
    assert got["JIAN_COMMAND"] == "status" and got["JIAN_VIDEO"] == "no-such-video" and "not found" in got["JIAN_ERROR"]


def test_a_broken_hook_warns_and_the_run_carries_on(hooks, capsys):
    cfg, out = hooks
    for cmd, why in (("false", "exit 1"), ("/no/such/command", "No such file"), (["sleep", "5"], "timed out")):
        cfg.update(on_ingest=cmd, timeout=0.2)
        core.hook("ingest", video="x")
        assert why in capsys.readouterr().err
    cfg["on_ingest"] = DUMP
    cli.main(["hook", "ingest", "VIDEO=by-hand"])  # `jian hook`: the same hook, fired by hand
    assert "JIAN_VIDEO=by-hand" in out.read_text()
    core.hook("approve", id="x:1")  # nothing configured for it: nothing happens


def test_doctor_flags_a_hook_that_cant_run(hooks):
    cfg, _ = hooks
    cfg.update(on_ingest="sh -c true", on_error="~/no/such/hook.sh", timeout=5)
    bad = [b for b in doctor.check()[0] if "[hooks]" in b]
    assert len(bad) == 1 and "on_error" in bad[0]
