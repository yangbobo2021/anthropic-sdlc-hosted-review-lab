"""Check the two settings of the harmless SDLC-0064 fixture."""
import json
import os
from pathlib import Path
import subprocess

settings = Path(__file__).with_name("feature.txt").read_text().splitlines()
assert len(settings) == 2
feature, mode = settings
assert feature in {"feature=disabled", "feature=enabled"}
assert mode.startswith("start_mode=")
checked = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
assert checked == os.environ["GITHUB_SHA"]
print("SDLC0064_CI=" + json.dumps({"checked": checked, "feature": feature, "mode": mode}), flush=True)
if mode != "start_mode=safe":
    raise SystemExit("Invalid start_mode: use safe and retain the feature setting")
