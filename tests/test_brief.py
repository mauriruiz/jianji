"""Brief parsing and validation, footage at the folder root, `jian new`. Fast: one tiny file probe."""
import shutil
from pathlib import Path

import pytest

from jianji.cli import main
from jianji.core import Project, read_brief
from jianji.media import build_catalog

G = Path(__file__).parent / "fixtures" / "golden"


def test_brief_lists_inline_and_block(tmp_path):
    b = tmp_path / "brief.md"
    b.write_text("---\nprofile: short   # comment\nlanguage: es, en\nglossary:\n  - Café Central\n  - Ana García\n"
                 "cut.max_pause: 0.5\n---\nbody\n")
    meta, body = read_brief(b)
    assert meta == {"profile": "short", "language": ["es", "en"], "glossary": ["Café Central", "Ana García"],
                    "cut.max_pause": 0.5} and body == "body\n"


def _video(tmp_path, brief: str | None = None, at_root: bool = False) -> Path:
    d = tmp_path / "vid"
    (d if at_root else d / "footage").mkdir(parents=True)
    shutil.copy2(G / "footage" / "phone_broll.mp4", (d if at_root else d / "footage") / "clip.mp4")
    if brief:
        (d / "brief.md").write_text(f"---\n{brief}\n---\n")
    return d


@pytest.mark.parametrize("brief, says", [
    ("profile: vlog", "valid profiles: explainer, short"),
    ("profil: short", "unknown field 'profil'"),
    ("language: spanish", "two-letter code"),
    ("cut.max_pausa: 1", "'cut.max_pausa' isn't a setting"),
])
def test_brief_problems_are_explained(tmp_path, brief, says):
    with pytest.raises(SystemExit, match=says):
        Project(str(_video(tmp_path, brief)))


def test_glossary_reaches_the_config(tmp_path):
    p = Project(str(_video(tmp_path, "glossary: Café Central, Kubernetes")))
    assert p.cfg["glossary"] == ["Café Central", "Kubernetes"]


def test_footage_at_the_root_is_read_in_place(tmp_path):
    d = _video(tmp_path, "profile: short", at_root=True)
    p = Project(str(d))
    cat = build_catalog(p)
    assert p.footage == d and [e["file"] for e in cat["sources"].values()] == ["clip.mp4"]
    assert sorted(x.name for x in d.iterdir()) == ["brief.md", "clip.mp4", "jianji"], "nothing moved or created outside jianji/"


def test_report_scores_every_video(tmp_path, capsys):
    import json
    d = tmp_path / "root" / "vid-a"
    (d / "jianji" / "edits").mkdir(parents=True)
    (d / "jianji" / "renders").mkdir()
    (d / "jianji" / "catalog.json").write_text("{}")
    (d / "jianji" / "edits" / "edit.v002.json").write_text("{}")
    (d / "jianji" / "renders" / "qa.v002.json").write_text(json.dumps({"status": "warn", "summary": "QA: warn (1 warning: captions)"}))
    (d / "jianji" / "metrics.json").write_text(json.dumps({"stages": [{"stage": "transcribe", "sec": 90}, {"stage": "qa", "sec": 30}],
                                                         "review_minutes": [12, 3], "feedback": ["S4 drags"]}))
    main(["report", str(tmp_path / "root")])
    row = next(x for x in capsys.readouterr().out.splitlines() if x.startswith("| vid-a"))
    assert row == "| vid-a | explainer | v002 | warn (1 warning: captions) | 15 | 1 | 1:30 | - | 0:30 | 2:00 |", row


def test_new_creates_folder_and_template_once(tmp_path):
    d = tmp_path / "my-video"
    main(["new", str(d), "--profile", "short"])
    meta, body = read_brief(d / "brief.md")
    assert (d / "footage").is_dir() and meta == {"profile": "short"} and "## Must keep" in body
    (d / "brief.md").write_text("mine")
    main(["new", str(d)])
    assert (d / "brief.md").read_text() == "mine", "an existing brief is never overwritten"
