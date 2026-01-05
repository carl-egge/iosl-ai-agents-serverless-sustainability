#!/usr/bin/env python3
"""Cloud Run endpoint that archives its input payload into a shared GCS bucket."""

from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Dict

import functions_framework
from google.cloud import storage


BUCKET_NAME = os.environ.get("OUTPUT_BUCKET")
REGION = os.environ.get("REGION", "unknown")

storage_client = storage.Client()


def _build_path() -> str:
    now = dt.datetime.utcnow().replace(microsecond=0)
    safe_timestamp = now.isoformat().replace(":", "-")
    return f"runs/{safe_timestamp}-{REGION}/run-{uuid.uuid4()}"


@functions_framework.http
def write_to_bucket(request) -> tuple[str, int, Dict[str, str]]:
    """Write the received payload to a new object under OUTPUT_BUCKET."""

    if not BUCKET_NAME:
        return (
            json.dumps({"error": "OUTPUT_BUCKET must be set in the deployment."}),
            500,
            {"Content-Type": "application/json"},
        )

    try:
        payload = request.get_json(silent=True) or {}
    except Exception:
        payload = {}

    object_prefix = _build_path()
    object_name = f"{object_prefix}/result.json"

    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(object_name)
    blob.upload_from_string(
        data=json.dumps({"payload": payload, "region": REGION}, indent=2),
        content_type="application/json",
    )

    return (
        json.dumps({"message": f"Wrote gs://{BUCKET_NAME}/{object_name}"}),
        200,
        {"Content-Type": "application/json"},
    )


if __name__ == "__main__":
    from types import SimpleNamespace

    class DummyRequest(SimpleNamespace):
        def get_json(self, silent=False):
            return {"note": "test"}

    print(write_to_bucket(DummyRequest()))
