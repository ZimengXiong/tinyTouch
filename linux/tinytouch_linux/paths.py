"""Per-user Linux paths; no system image changes are required."""
import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "tinyTouch"
