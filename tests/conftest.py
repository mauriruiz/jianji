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
