#!/usr/bin/env python3
"""Minimal API example: fetch Electricity Maps forecast and return simple metrics."""
import json
import os
from typing import Any, Dict

import requests


def _fetch_forecast(zone: str, token: str, horizon_hours: int = 24) -> Dict[str, Any]:
    url = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
    headers = {"auth-token": token}
    params = {"zone": zone, "horizonHours": horizon_hours}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"API failed: {resp.status_code} - {resp.text}")
    return resp.json().get("forecast", [])


def _aggregate(forecast: list) -> Dict[str, Any]:
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


def simple_api_call(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """
    Fetch forecast for a zone (default 'DE') using ELECTRICITYMAPS_TOKEN and return basic stats.
    Lambda-style response shape.
    """

    try:
        from dotenv import load_dotenv
    except ImportError:  # Optional dependency for local runs
        load_dotenv = None

    if load_dotenv:
        load_dotenv()

    zone = event.get("zone", "DE")
    horizon = int(event.get("horizonHours", 24))
    token = os.environ.get("ELECTRICITYMAPS_TOKEN")
    if not token:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "ELECTRICITYMAPS_TOKEN not set"}),
        }

    try:
        forecast = _fetch_forecast(zone, token, horizon)
        metrics = _aggregate(forecast)
        return {
            "statusCode": 200,
            "body": json.dumps({"zone": zone, "metrics": metrics}),
        }
    except Exception as exc:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(exc)}),
        }


def handler(event: Dict[str, Any], context: Any = None) -> Dict[str, Any]:
    """Wrapper to mirror serverless handler signatures."""
    return simple_api_call(event, context)


if __name__ == "__main__":
    # Tiny self-test: requires ELECTRICITYMAPS_TOKEN in the environment.
    sample_event = {"zone": "DE", "horizonHours": 6}
    print(simple_api_call(sample_event))
