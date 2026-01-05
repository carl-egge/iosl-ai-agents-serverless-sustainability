#!/usr/bin/env python3
"""Simulated transcoder that spins through large byte buffers to mimic multi-resolution processing."""

import hashlib
import io
import json
import os
import time
import zlib
from typing import Dict

import functions_framework


@functions_framework.http

def video_transcoder(request) -> tuple[str, int, Dict[str, str]]:
    """Create synthetic video data and 'transcode' it by compressing each chunk."""

    payload = request.get_json(silent=True) or {}
    chunk_mb = int(payload.get("chunk_mb", 20))
    chunk_mb = max(5, min(chunk_mb, 50))
    passes = int(payload.get("passes", 3))

    raw_bytes = os.urandom(chunk_mb * 1024 * 1024)
    start = time.perf_counter()
    digests = []
    for _ in range(passes):
        compressor = zlib.compressobj(level=6)
        compressed = compressor.compress(raw_bytes) + compressor.flush()
        digests.append(hashlib.sha256(compressed).hexdigest())
    duration = round(time.perf_counter() - start, 3)

    response = {
        "scenario": "Long runtime + Large data",
        "chunks_mb": chunk_mb,
        "passes": passes,
        "duration_seconds": duration,
        "digest": digests[-1],
        "compressed_ratio": round(len(compressed) / len(raw_bytes), 3),
    }

    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    class DummyRequest:
        def get_json(self, silent=False):
            return {}

    print(video_transcoder(DummyRequest()))
