"""After make_golden.sh: transcribe the test footage once and freeze what the tests read instead of running the
models (transcript.A.json, env.A.npy). Needs the local models. Run: uv run python tests/fixtures/freeze.py"""
import os
import shutil
import tempfile
from pathlib import Path

import numpy as np

_home = Path(tempfile.mkdtemp())  # hermetic like the tests: built-in profiles, and a disk floor for tiny clips
(_home / "config.toml").write_text("schema = 1\n\n[defaults.disk]\nmin_free_gb = 1\n")
os.environ["JIANJI_HOME"] = str(_home)

from jianji.core import Project, write_json
from jianji.media import build_catalog, envelope, load_wav, wav16k
from jianji.transcribe import Engine, transcribe_audio

F = Path(__file__).parent

for name in ("golden", "runon"):
    d = Path(tempfile.mkdtemp()) / name
    (d / "footage").mkdir(parents=True)
    shutil.copy2(F / name / "footage" / f"{name}.mp4", d / "footage" / f"{name}.mp4")
    (d / "brief.md").write_text("---\nlanguage: en, es\n---\n")
    p = Project(str(d))
    cat = build_catalog(p)
    sid, e = next((k, v) for k, v in cat["sources"].items() if v["file"] == f"{name}.mp4")
    audio = load_wav(wav16k(p, sid, e))
    tr = transcribe_audio(audio, Engine(p.cfg["asr"]))
    write_json(F / name / "transcript.A.json", tr)
    np.save(F / name / "env.A.npy", envelope(audio))
    print(f"{name}: {e['dur']:.2f} s, {len(tr['words'])} words: {' '.join(w['t'] for w in tr['words'])}")
    shutil.rmtree(d.parent)
