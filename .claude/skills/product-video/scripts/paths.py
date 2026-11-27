"""Where everything lives - shared by the recording, Blender and cutting scripts (stdlib only: Blender imports it too).

The work dir holds the demo app copy, recordings, frame sequences, renders and the cut - several GB, so it is on
disk under .render/ (git-ignored), never in /tmp, which is RAM here. VIDEO_WORK moves it.
"""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
WORK = Path(os.environ.get("VIDEO_WORK", REPO / ".render" / "video" / "work"))
MODEL = Path(os.environ.get("VIDEO_MODEL", REPO / ".render" / "render_modell" / "CoffeePy_komplett.obj"))
PY = REPO / ".venv" / "bin" / "python"
