from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "skilllift", ROOT / "packages/bounded-edits/src"):
    sys.path.insert(0, str(path))
