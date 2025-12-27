#!/usr/bin/env python3
"""Simple Cloud Run endpoint that adds two numbers from an HTTP JSON payload."""

from __future__ import annotations

import json
from typing import Dict

import functions_framework


@functions_framework.http
def simple_addition(request) -> tuple[str, int, Dict[str, str]]:
    """Add `num1` and `num2` from the request body and return the sum."""

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:  # pragma: no cover - HTTP body parsing may raise
        payload = {}

    num1 = payload.get("num1", 0)
    num2 = payload.get("num2", 0)

    try:
        result = float(num1) + float(num2)
    except (TypeError, ValueError):
        response = {"error": "num1 and num2 must be numeric"}
        return json.dumps(response), 400, {"Content-Type": "application/json"}

    response = {"result": result}
    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    # Quick local sanity check.
    from types import SimpleNamespace

    class DummyRequest(SimpleNamespace):
        def get_json(self, silent=False):
            return {"num1": 5, "num2": 7}

    print(simple_addition(DummyRequest()))
