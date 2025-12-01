#!/usr/bin/env python3
"""Thin wrapper to expose the Cloud Run Flask app."""

import os
import sys
from pathlib import Path

# Ensure the src package is on the path when running from the deployments directory
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = PROJECT_ROOT / "src"
for path in (SRC_DIR, PROJECT_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from agent.planner import create_gcp_app

app = create_gcp_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

