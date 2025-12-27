#!/usr/bin/env python3
"""Minimal HTTP Carbon Intensity helper built for Cloud Run."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

import functions_framework
import requests


def _fetch_forecast(zone: str, token: str, horizon_hours: int) -> list[Dict[str, Any]]:
    url = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
    headers = {"auth-token": token}
    params = {"zone": zone, "horizonHours": horizon_hours}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("forecast", [])


def _aggregate(forecast: list[Dict[str, Any]]) -> Dict[str, Any]:
    if not forecast:
        return {"count": 0, "avg": None, "min": None, "max": None}
    values = [p.get("carbonIntensity") for p in forecast if "carbonIntensity" in p]
    if not values:
        return {"count": 0, "avg": None, "min": None, "max": None}
    return {
        "count": len(values),
        "avg": sum(values) / len(values),
        "min": min(values),
        "max": max(values),
        "first_datetime": forecast[0].get("datetime"),
        "last_datetime": forecast[-1].get("datetime"),
    }


@functions_framework.http
def simple_api_call(request) -> tuple[str, int, Dict[str, str]]:
    """Fetch a basic carbon intensity forecast summary for the requested zone."""

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:  # pragma: no cover
        payload = {}

    zone = payload.get("zone", "DE")
    horizon = int(payload.get("horizonHours", 24))
    token = os.environ.get("ELECTRICITYMAPS_TOKEN")
    if not token:
        return (
            json.dumps({"error": "ELECTRICITYMAPS_TOKEN environment variable is required."}),
            500,
            {"Content-Type": "application/json"},
        )

    try:
        forecast = _fetch_forecast(zone, token, horizon)
        metrics = _aggregate(forecast)
        return (
            json.dumps({"zone": zone, "metrics": metrics}),
            200,
            {"Content-Type": "application/json"},
        )
    except requests.HTTPError as error:
        return (
            json.dumps({"error": str(error)}),
            502,
            {"Content-Type": "application/json"},
        )
    except Exception as error:
        return (
            json.dumps({"error": str(error)}),
            500,
            {"Content-Type": "application/json"},
        )


if __name__ == "__main__":
    from types import SimpleNamespace

    class DummyRequest(SimpleNamespace):
        def get_json(self, silent=False):
            return {"zone": "DE", "horizonHours": 6}

    # This will raise if the token is missing.
    print(simple_api_call(DummyRequest()))
