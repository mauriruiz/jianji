"""scripts/night.sh with stand-ins for jian and claude: the steps in order, and the editor's lock-down exactly as
the README promises (the recording is untrusted: a public chat, whatever was on screen)."""
import os
import subprocess
from pathlib import Path

import pytest

NIGHT = Path(__file__).parent.parent / "scripts" / "night.sh"
JIAN = '''#!/bin/sh
echo "jian $*" >> "$CALLS"
[ "$1" = status ] && printf '%s  profile=stream  outline_checkpoint=False\\nwork folder: %s  style guide: none\\n' "$VIDEO" "$VIDEO/jianji"
[ "$1" = status ] && [ -s "$VIDEO/jianji/shorts.txt" ] && echo "shorts: $(cat "$VIDEO/jianji/shorts.txt")  (jian status x --short <n>)"
[ "$1" = "${JIAN_FAILS:-}" ] && exit 3
exit 0
'''
CLAUDE = '''#!/bin/sh
echo "claude in $(pwd)" >> "$CALLS"
for a in "$@"; do printf '%s\\n---arg---\\n' "$a"; done > "$CLAUDE_ARGS"
n=$(($(cat "$VIDEO/jianji/passes" 2>/dev/null || echo 0) + 1)); echo $n > "$VIDEO/jianji/passes"
case "$2" in *jian-shorts*) printf '%s\\n' "$MAKES" | cut -d'|' -f$n > "$VIDEO/jianji/shorts.txt" ;; esac  # where the shorts stand after this pass
exit "${CLAUDE_EXIT:-0}"
'''


@pytest.fixture
def night(tmp_path):
    video = tmp_path / "2026-03-01 live"  # a space in the name: nothing may split on it
    (video / "jianji").mkdir(parents=True)
    for name, text in (("jian", JIAN), ("claude", CLAUDE)):
        (tmp_path / name).write_text(text)
        (tmp_path / name).chmod(0o755)
    env = {**os.environ, "JIAN": str(tmp_path / "jian"), "CLAUDE": str(tmp_path / "claude"), "VIDEO": str(video),
           "CALLS": str(tmp_path / "calls.txt"), "CLAUDE_ARGS": str(tmp_path / "args.txt"),
           "MAKES": "1 pending, 2 pending, 3 pending"}

    def run(*args, **more):
        r = subprocess.run(["sh", str(NIGHT), str(video), *args], env={**env, **more}, capture_output=True, text=True)
        calls = (tmp_path / "calls.txt").read_text().splitlines()
        (tmp_path / "calls.txt").unlink()
        argv = (tmp_path / "args.txt").read_text().split("\n---arg---\n")[:-1] if (tmp_path / "args.txt").exists() else []
        return r, [c.replace(str(video), "V") for c in calls], argv
    return video, run


def test_the_steps_run_in_order_and_the_editor_is_locked_down(night):
    video, run = night
    r, calls, argv = run("2")
    assert r.returncode == 0, r.stderr
    assert calls == ["jian status V", "jian prep V", "jian map V", "jian status V", "claude in V", "jian status V",
                     "jian queue --announce"]
    assert argv[0] == "-p" and argv[1].startswith("\n# /jian-shorts") and "argument-hint" not in argv[1]
    assert f"video folder {video}" in argv[1] and "At most 2 shorts" in argv[1]
    flags = argv[2:]
    for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in flags
    assert flags[flags.index("--permission-mode") + 1] == "dontAsk"  # what isn't allowed is denied, never asked
    assert flags[flags.index("--tools") + 1] == "Bash,Read,Write,Edit,Glob,Grep"  # no web, no agents
    allowed = flags[flags.index("--allowedTools") + 1:]
    bash = [a for a in allowed if a.startswith("Bash")]
    assert bash and all(a.startswith(f"Bash({video.parent / 'jian'} ") for a in bash)  # only jian, never plain Bash
    assert {a.split()[1] for a in bash} == {"status", "prep", "map", "words", "sheet", "compile", "cut", "qa", "at", "cuts", "texts"}
    writes = [a for a in allowed if a.startswith(("Edit", "Write"))]
    assert writes == [f"Edit(/{video}/jianji/**)"]  # //path: absolute; Edit rules cover every file-writing tool
    assert not any("dangerously" in a or a in ("Bash", "Bash(*)", "Edit", "Write", "WebFetch", "WebSearch") for a in flags)
    log = (video / "jianji" / "night.log").read_text()
    assert "shorts: done" in log and "night: finished" in log and not (video / "jianji" / "night.lock").exists()


def test_a_failed_editor_tells_your_hook_and_the_queue_still_announces(night):
    video, run = night
    r, calls, argv = run(CLAUDE_EXIT="1", NIGHT_RECAP="1")
    assert r.returncode == 1 and argv[1].startswith("\n# /jian-edit")  # the recap still gets its try, after the shorts
    assert calls[4:] == ["claude in V", "jian hook error COMMAND=night VIDEO=2026-03-01 live ERROR=the shorts step failed: see night.log",
                         "claude in V", "jian queue --announce"]
    assert "shorts: FAILED (1)" in (video / "jianji" / "night.log").read_text()


def test_a_failed_prep_stops_the_night(night):
    video, run = night
    r, calls, argv = run(JIAN_FAILS="prep")
    assert r.returncode == 1 and calls == ["jian status V", "jian prep V"] and argv == []


def test_one_night_per_video_and_a_step_has_a_time_limit(night):
    video, run = night
    lock = video / "jianji" / "night.lock"
    lock.mkdir()
    (lock / "pid").write_text(str(os.getpid()))  # a live process holds it
    r, calls, _ = run()
    assert r.returncode == 1 and "already running" in r.stderr and calls == ["jian status V"] and lock.exists()
    (lock / "pid").write_text("999999")  # a night that died: its lock is taken over
    slow = video.parent / "slow"
    slow.write_text("#!/bin/sh\nexec sleep 30\n")
    slow.chmod(0o755)
    r, calls, _ = run(NIGHT_SHORTS_TIMEOUT="1", CLAUDE=str(slow))
    assert r.returncode == 1 and "already running" not in r.stderr and calls[-1] == "jian queue --announce"
    assert "shorts: FAILED (143)" in (video / "jianji" / "night.log").read_text()  # stopped at its limit, not after 30 s


def test_a_short_that_failed_qa_gets_a_second_pass_and_no_more(night):
    video, run = night
    r, calls, argv = run("2", MAKES="1 pending, 2 failed|1 pending, 2 failed, 3 pending")
    assert r.returncode == 0 and calls[3:] == ["jian status V", "claude in V", "jian status V", "claude in V",
                                               "jian status V", "jian queue --announce"]
    assert "1 of the 2 shorts wanted are already there" in argv[1] and "1 more at most" in argv[1]
    (video / "jianji" / "passes").unlink()
    r, calls, argv = run(MAKES="1 pending|1 pending, 2 failed|never asked for")  # the stream had one good moment
    assert r.returncode == 0 and calls.count("claude in V") == 2 and calls[-1] == "jian queue --announce"
    assert "shorts: 1 of 3 got through QA" in (video / "jianji" / "night.log").read_text()


def test_a_second_night_cuts_only_what_is_missing(night):
    video, run = night
    (video / "jianji" / "shorts.txt").write_text("1 approved, 2 rejected, 3 pending, 4 failed\n")  # three made it; you judged two
    r, calls, argv = run(NIGHT_RECAP="1")
    assert r.returncode == 0 and calls[3:] == ["jian status V", "claude in V", "jian queue --announce"]
    assert argv[1].startswith("\n# /jian-edit")  # just the recap
    assert "shorts: 3 already cut, 3 wanted: nothing to do" in (video / "jianji" / "night.log").read_text()
    r, calls, argv = run("4", MAKES="1 approved, 2 rejected, 3 pending, 4 failed, 5 pending")
    assert calls.count("claude in V") == 1 and "3 of the 4 shorts wanted are already there" in argv[1]
