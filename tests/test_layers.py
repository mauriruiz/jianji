"""Config layers (built-in < your ~/.config/jianji < the video), folders from before the rename, `jian doctor`."""
import shutil
from pathlib import Path

import pytest

from jianji import core
from jianji.cli import main
from jianji.core import Project
from jianji.doctor import check

G = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / "profiles").mkdir(parents=True)
    (h / "config.toml").write_text("schema = 1\n\n[defaults.disk]\nmin_free_gb = 1\n")
    monkeypatch.setattr(core, "HOME", h)
    monkeypatch.setattr(core, "GLOBAL_CONFIG", h / "config.toml")
    return h


def _video(tmp_path, brief: str = "", work: str | None = None) -> Path:
    d = tmp_path / "vid"
    (d / "footage").mkdir(parents=True)
    shutil.copy2(G / "footage" / "phone_broll.mp4", d / "footage" / "clip.mp4")
    (d / "brief.md").write_text(f"---\n{brief}\n---\n")
    if work:
        (d / work).mkdir()
    return d


def test_your_layer_overrides_only_what_it_sets(tmp_path, home):
    (home / "profiles" / "short.toml").write_text("[cut]\nmax_pause = 0.42\n")
    (home / "brand.toml").write_text('[captions]\nfont = "My Font"\n')
    (home / "glossary.txt").write_text("# names I say a lot\nKubernetes\n\nAna García\n")
    (home / "extensions").mkdir()
    (home / "extensions" / "series.toml").write_text('glossary = ["Café Central"]\n[cut]\nmax_pause = 0.5\n')
    built_in = core._toml(core.PKG / "profiles" / "short.toml")
    p = Project(str(_video(tmp_path, "profile: short")))
    assert p.cfg["cut"]["max_pause"] == 0.42 and p.cfg["captions"]["font"] == "My Font"
    assert p.cfg["cut"]["tail_margin"] == built_in["cut"]["tail_margin"], "keys you don't set stay built-in"
    assert p.cfg["glossary"] == ["Kubernetes", "Ana García"]
    shutil.rmtree(tmp_path / "vid")
    p = Project(str(_video(tmp_path, "profile: short\nextends: series\nglossary: Lisboa\ncut.max_pause: 0.6")))
    assert p.cfg["glossary"] == ["Kubernetes", "Ana García", "Café Central", "Lisboa"]
    assert p.cfg["cut"]["max_pause"] == 0.6, "the brief has the last word"


def test_your_profiles_styles_and_brief_template(tmp_path, home, capsys):
    (home / "profiles" / "podcast.toml").write_text("[cut]\nmax_pause = 1.2\n")
    (home / "styles").mkdir()
    (home / "styles" / "podcast.md").write_text("# my podcast rules\n")
    (home / "briefs").mkdir()
    (home / "briefs" / "default.md").write_text("---\nprofile: {profile}\n---\n# {name}: my template\n")
    assert "podcast" in core.profiles() and core.style_path("podcast") == home / "styles" / "podcast.md"
    assert core.style_path("short") == core.PKG / "styles" / "short.md"
    main(["new", str(tmp_path / "ep1"), "--profile", "podcast"])
    assert (tmp_path / "ep1" / "brief.md").read_text() == "---\nprofile: podcast\n---\n# ep1: my template\n"


def test_folders_from_before_the_rename_keep_working(tmp_path, home, capsys):
    p = Project(str(_video(tmp_path, work="vedit")))
    assert p.out == tmp_path / "vid" / "vedit" and not (tmp_path / "vid" / "jianji").exists()
    (p.out / "catalog.json").write_text("{}")
    nested = tmp_path / "series" / "ep1" / "jianji"
    nested.mkdir(parents=True)
    (nested / "catalog.json").write_text("{}")
    main(["report", str(tmp_path)])
    rows = [x.split(" | ")[0] for x in capsys.readouterr().out.splitlines() if x.startswith("| ") and "video" not in x]
    assert rows == ["| series/ep1", "| vid"], rows


def test_doctor_explains_a_layer_that_doesnt_match(home):
    assert check()[0] == []
    (home / "config.toml").write_text("schema = 99\n")
    (home / "profiles" / "short.toml").write_text("[cut]\nmax_pausa = 0.4\n")
    (home / "profiles" / "podcast.toml").write_text("[cut]\nmax_pause = 1.2\n")
    bad = "\n".join(check()[0])
    assert "schema 99" in bad and "update jian" in bad
    assert "'cut.max_pausa' isn't a setting" in bad
    assert "profile 'podcast' has no style guide" in bad
