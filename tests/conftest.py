import os
import tempfile
from pathlib import Path

import pytest

# hermetic: never read your ~/.config/jianji during tests. Test renders are a few MB, so the disk guard (meant for
# real footage) mustn't fail the suite just because the machine's disk is nearly full.
_home = Path(tempfile.mkdtemp(prefix="jian-test-"))
(_home / "config.toml").write_text("schema = 1\n\n[defaults.disk]\nmin_free_gb = 1\n\n"
                                   '[defaults.asr]\nlanguages = ["en", "es"]\n')  # the golden clip is bilingual
os.environ["JIANJI_HOME"] = str(_home)
os.environ.pop("JIANJI_VIDEOS_ROOT", None)


def pytest_collection_modifyitems(config, items):
    """Tests marked `models` load Whisper, the aligners or the vision model (~10 GB, Apple GPU): opt-in."""
    if os.environ.get("JIANJI_SLOW"):
        return
    skip = pytest.mark.skip(reason="needs the local models: JIANJI_SLOW=1 to run")
    for it in items:
        if "models" in it.keywords:
            it.add_marker(skip)


def quadrants(path, secs: int = 4):
    """A synthetic recording: four coloured quadrants (blue, red / green, yellow), 1280x720, and a tone."""
    import subprocess
    from jianji.core import FFMPEG
    quad = ";".join(f"color=c={c}:s=640x360:r=30[{k}]" for k, c in zip("abcd", ("blue", "red", "green", "yellow")))
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"{quad};[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", str(secs), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", path], check=True)
    return path


def cut_short(d, config: str, edit: dict):
    """Short 1 of the video folder `d`, cut and rendered without QA (no models). -> (the video, the short)."""
    import json
    from jianji import cli
    from jianji.core import Project
    from jianji.media import build_catalog
    video = Project(str(d))
    build_catalog(video)
    out = Project(str(d), short="1").out
    (out / "config.toml").write_text(config)
    (out / "edits").mkdir()
    (out / "edits" / "edit.v001.json").write_text(json.dumps(edit))
    p = Project(str(d), short="1")
    cli.cut(p, None, qa=False)
    return video, p
