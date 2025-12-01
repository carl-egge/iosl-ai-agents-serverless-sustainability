#!/usr/bin/env python3
"""Local entrypoint delegating to the shared planner logic."""

import sys
from pathlib import Path

# Make sure src/ is importable when running from repository root or this folder
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
for path in (SRC_DIR, PROJECT_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from agent.planner import run_planner


if __name__ == "__main__":
    run_planner()

