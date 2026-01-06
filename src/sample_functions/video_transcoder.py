#!/usr/bin/env python3
"""Transcoder that consumes the provided payload (body/base64/GCS/multipart), compresses it multiple times, and returns the processed data."""

import base64
import binascii
import hashlib
import json
import os
import time
import zlib
from typing import Dict, Optional, Tuple

import functions_framework
from google.cloud import storage

storage_client = storage.Client()
def _parse_gcs_uri(uri: Optional[str]) -> Optional[Tuple[str, str]]:
    if not uri:
        return None
    if uri.startswith("gs://"):
        uri = uri[5:]
    parts = uri.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def _download_from_bucket(bucket_name: str, object_path: str) -> bytes:
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(object_path)
    return blob.download_as_bytes()


def _value_from_sources(request, key: str) -> Optional[str]:
    payload = request.get_json(silent=True) or {}
    form_payload = request.form or {}
    for source in (payload, form_payload):
        value = source.get(key)
        if value:
            return value
    return None


def _extract_payload(request) -> bytes:
    if request.files:
        file_storage = next(iter(request.files.values()))
        return file_storage.read()

    raw_body = request.get_data(cache=False)
    if raw_body and "multipart/" not in (request.content_type or ""):
        return raw_body

    encoded = _value_from_sources(request, "data")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded)
        except (ValueError, binascii.Error):
            pass

    gcs_location = _parse_gcs_uri(_value_from_sources(request, "gcs_uri"))
    bucket = _value_from_sources(request, "bucket")
    object_path = _value_from_sources(request, "object")
    if gcs_location:
        bucket, object_path = gcs_location
    if bucket and object_path:
        return _download_from_bucket(bucket, object_path)

    raise ValueError(
        "Send binary payload (body/multipart/base64) or specify `bucket`/`object` or `gcs_uri=gs://bucket/path`."
    )


@functions_framework.http
def video_transcoder(request) -> tuple[str, int, Dict[str, str]]:
    """Transcode whichever payload you upload by compressing it multiple times."""

    payload = request.get_json(silent=True) or {}
    passes = int(payload.get("passes", 3))
    passes = max(1, min(passes, 10))

    try:
        raw_bytes = _extract_payload(request)
    except ValueError as error:
        return (
            json.dumps({"error": str(error)}),
            400,
            {"Content-Type": "application/json"},
        )

    start = time.perf_counter()
    digests = []
    compressed = raw_bytes
    for _ in range(passes):
        compressor = zlib.compressobj(level=6)
        compressed = compressor.compress(compressed) + compressor.flush()
        digests.append(hashlib.sha256(compressed).hexdigest())
    duration = round(time.perf_counter() - start, 3)

    response = {
        "scenario": "Long runtime + Large data",
        "input_size_mb": round(len(raw_bytes) / (1024 * 1024), 2),
        "output_size_mb": round(len(compressed) / (1024 * 1024), 2),
        "passes": passes,
        "duration_seconds": duration,
        "digest": digests[-1],
        "processed_data": base64.b64encode(compressed).decode(),
    }

    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    sample = os.urandom(5 * 1024 * 1024)
    encoded = base64.b64encode(sample).decode()

    class DummyRequest:
        def get_json(self, silent=False):
            return {"passes": 2, "data": encoded}

        def get_data(self, cache=False):
            return b""

    print(video_transcoder(DummyRequest()))
