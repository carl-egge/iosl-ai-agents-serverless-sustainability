#!/usr/bin/env python3
"""
Cloud Run deployment entrypoint.
This file imports and exposes the Flask app from agent.py

In Cloud Run, all files (main.py, agent.py, prompts.py) are deployed flat in /workspace/
so we can import directly without path manipulation.
"""

import os
import sys
from pathlib import Path

# Add current directory to path (for Cloud Run's /workspace/)
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Import the Flask app creator from agent.py
from agent import create_flask_app

# Create the Flask app at module level for gunicorn to find
app = create_flask_app()

if __name__ == "__main__":
    # This runs when executing main.py directly (not used by Cloud Run)
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
