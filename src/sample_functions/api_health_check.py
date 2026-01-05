#!/usr/bin/env python3
"""Minimal health-check endpoint mimicking high-volume lightweight traffic."""

import json
from datetime import datetime, timezone
from typing import Dict

import functions_framework


@functions_framework.http

def api_health_check(request) -> tuple[str, int, Dict[str, str]]:
    """Return a compact health snapshot that shows the invocation scenario."""

    payload = request.get_json(silent=True) or {}
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "status": "ok",
        "timestamp": now,
        "scenario": "Short runtime + Little data",
        "payload": payload,
    }
    return json.dumps(result), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    class DummyRequest:
        def get_json(self, silent=False):
            return {"check": "ping"}

    print(api_health_check(DummyRequest()))
