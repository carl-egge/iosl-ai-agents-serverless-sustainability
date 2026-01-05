#!/usr/bin/env python3
"""Transcoder that consumes the provided payload, compresses it multiple times, and returns the processed data."""

import base64
import binascii
import hashlib
import json
import os
import time
import zlib
from typing import Dict

import functions_framework

def _extract_payload(request) -> bytes:
    payload = request.get_json(silent=True) or {}
    raw_body = request.get_data(cache=False)
    if raw_body:
        return raw_body
    encoded = payload.get("data")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded)
        except (ValueError, binascii.Error):
            pass
    raise ValueError("Send binary payload either as the request body or base64-encoded `data` field.")


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
