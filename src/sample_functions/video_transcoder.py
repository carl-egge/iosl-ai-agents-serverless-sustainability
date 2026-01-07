#!/usr/bin/env python3
"""Transcoder-like workload for evaluation runs.

Evaluation improvements:
- Default behavior writes processed output to GCS and returns only metadata + output URI.
- Inline (base64) output is still available via `return_inline: true` for debugging.
- Runtime is tunable via `passes` and `target_ms` (minimum wall time).

Inputs supported (JSON only):
- base64 field `data`
- pointer: `gcs_uri=gs://bucket/path` or (`bucket`, `object`)

Request JSON (selected fields):
- passes: 1..10 (default: 3)
- target_ms: minimum wall time (default: 0 => no minimum)
- return_inline: bool (default: false)
- output_gcs_uri: gs://bucket/path (optional)
- output_bucket / output_object: alternative output location (optional)
- output_prefix: if output not specified and input came from GCS or DEFAULT_OUTPUT_BUCKET is set,
  write to {bucket}/{output_prefix}/<uuid>.bin (default prefix: eval_outputs/video_transcoder)

Environment variables (optional):
- DEFAULT_OUTPUT_BUCKET: bucket used if output location is not provided and input is not from GCS
- DEFAULT_OUTPUT_PREFIX_VIDEO: default output prefix (default: eval_outputs/video_transcoder)
- MAX_INLINE_MB: inline response limit in MB (default: 16)
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import time
import uuid
import zlib
from typing import Dict, Optional, Tuple

import functions_framework
from google.cloud import storage

_STORAGE_CLIENT: Optional[storage.Client] = None
_MAX_INLINE_BYTES: int = 16 * 1024 * 1024

try:
    _MAX_INLINE_BYTES = max(1, int(os.getenv("MAX_INLINE_MB", "16"))) * 1024 * 1024
except Exception:
    _MAX_INLINE_BYTES = 16 * 1024 * 1024

_DEFAULT_OUTPUT_BUCKET = os.getenv("DEFAULT_OUTPUT_BUCKET", "").strip() or None
_DEFAULT_OUTPUT_PREFIX = os.getenv(
    "DEFAULT_OUTPUT_PREFIX_VIDEO", "eval_outputs/video_transcoder"
).strip()


def _parse_gcs_uri(uri: Optional[str]) -> Optional[Tuple[str, str]]:
    if not uri:
        return None
    if uri.startswith("gs://"):
        uri = uri[5:]
    parts = uri.split("/", 1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0], parts[1]
    return None


def _get_storage_client() -> storage.Client:
    global _STORAGE_CLIENT
    if _STORAGE_CLIENT is None:
        _STORAGE_CLIENT = storage.Client()
    return _STORAGE_CLIENT


def _download_from_bucket(bucket_name: str, object_path: str) -> bytes:
    bucket = _get_storage_client().bucket(bucket_name)
    blob = bucket.blob(object_path)
    return blob.download_as_bytes()


def _upload_to_bucket(bucket_name: str, object_path: str, data: bytes, content_type: str) -> str:
    bucket = _get_storage_client().bucket(bucket_name)
    blob = bucket.blob(object_path)
    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket_name}/{object_path}"


def _extract_payload(payload: dict) -> Tuple[bytes, Optional[str]]:
    """Return (bytes, input_gcs_uri_if_any)."""
    encoded = payload.get("data")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded, validate=True), None
        except (ValueError, binascii.Error):
            raise ValueError("Invalid base64 in `data`.")

    gcs_location = _parse_gcs_uri(payload.get("gcs_uri"))
    bucket = payload.get("bucket")
    object_path = payload.get("object")
    if gcs_location:
        bucket, object_path = gcs_location
    if bucket and object_path:
        return _download_from_bucket(str(bucket), str(object_path)), f"gs://{bucket}/{object_path}"

    raise ValueError(
        "Send JSON with base64 `data`, or specify `bucket`/`object` or `gcs_uri=gs://bucket/path`."
    )


def _pick_output_location(payload: dict, input_gcs_uri: Optional[str]) -> Tuple[str, str]:
    # Explicit output_gcs_uri
    out_gcs = payload.get("output_gcs_uri") or payload.get("output_gcs")
    parsed = _parse_gcs_uri(out_gcs) if isinstance(out_gcs, str) else None
    if parsed:
        return parsed

    # Explicit output_bucket/object
    out_bucket = payload.get("output_bucket")
    out_object = payload.get("output_object")
    if out_bucket and out_object:
        return str(out_bucket), str(out_object)

    # Derive from input bucket if possible
    derived_bucket: Optional[str] = None
    if input_gcs_uri:
        parsed_in = _parse_gcs_uri(input_gcs_uri)
        if parsed_in:
            derived_bucket = parsed_in[0]
    if not derived_bucket:
        derived_bucket = _DEFAULT_OUTPUT_BUCKET

    if not derived_bucket:
        raise ValueError(
            "No output location specified. Provide `output_gcs_uri` or (`output_bucket`,`output_object`), "
            "or set DEFAULT_OUTPUT_BUCKET env var, or provide input via GCS so output can be derived."
        )

    prefix = str(payload.get("output_prefix") or _DEFAULT_OUTPUT_PREFIX).strip().strip("/")
    object_path = f"{prefix}/{uuid.uuid4().hex}.bin"
    return derived_bucket, object_path


def _compress_pass(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=6)
    return compressor.compress(data) + compressor.flush()


@functions_framework.http
def video_transcoder(request) -> tuple[str, int, Dict[str, str]]:
    """Transcode whichever payload you upload by compressing it multiple times."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json.dumps({"error": "Expected a JSON object payload."}), 400, {"Content-Type": "application/json"}

    passes = int(payload.get("passes", 3))
    passes = max(1, min(passes, 10))

    target_ms = int(payload.get("target_ms", 0) or 0)
    target_ms = max(0, min(target_ms, 60 * 60 * 1000))  # cap at 60 min

    return_inline = bool(payload.get("return_inline", False))

    try:
        raw_bytes, input_gcs_uri = _extract_payload(payload)
    except ValueError as error:
        return json.dumps({"error": str(error)}), 400, {"Content-Type": "application/json"}

    start = time.perf_counter()
    deadline = start + (target_ms / 1000.0) if target_ms > 0 else None

    # Work loop:
    # - Always do at least one cycle.
    # - If target_ms is set, repeat cycles until deadline.
    # - Each cycle starts from original bytes to keep workload stable.
    cycles = 0
    total_passes = 0
    digests: list[str] = []

    processed = raw_bytes
    while True:
        cycles += 1
        processed = raw_bytes

        for _ in range(passes):
            processed = _compress_pass(processed)
            total_passes += 1

        digests.append(hashlib.sha256(processed).hexdigest())

        if deadline is None:
            break
        if time.perf_counter() >= deadline:
            break
        # Safety valve: prevents pathological loops if target_ms is huge and input is tiny.
        if cycles >= 200:
            break

    duration_s = round(time.perf_counter() - start, 6)

    response: dict = {
        "scenario": "Long runtime + Large data",
        "passes": passes,
        "target_ms": target_ms,
        "cycles": cycles,
        "total_passes": total_passes,
        "duration_seconds": duration_s,
        "input_bytes": len(raw_bytes),
        "output_bytes": len(processed),
        "input_size_mb": round(len(raw_bytes) / (1024 * 1024), 4),
        "output_size_mb": round(len(processed) / (1024 * 1024), 4),
        "digest": digests[-1] if digests else None,
        "input_gcs_uri": input_gcs_uri,
        "return_inline": return_inline,
    }

    if return_inline:
        if len(processed) > _MAX_INLINE_BYTES:
            return (
                json.dumps(
                    {
                        "error": "Inline response too large.",
                        "hint": "Use GCS output or lower the input size.",
                        "max_inline_bytes": _MAX_INLINE_BYTES,
                    }
                ),
                413,
                {"Content-Type": "application/json"},
            )
        # Debug mode only (can be large).
        response["processed_data_base64"] = base64.b64encode(processed).decode("utf-8")
        return json.dumps(response), 200, {"Content-Type": "application/json"}

    # Evaluation mode: write output to GCS and return URI + metadata.
    try:
        out_bucket, out_object = _pick_output_location(payload, input_gcs_uri)
        output_gcs_uri = _upload_to_bucket(
            out_bucket, out_object, processed, content_type="application/octet-stream"
        )
        response["output_gcs_uri"] = output_gcs_uri
        response["output_bucket"] = out_bucket
        response["output_object"] = out_object
    except ValueError as error:
        return (
            json.dumps(
                {
                    "error": str(error),
                    "hint": "Set output_gcs_uri or output_bucket/output_object or DEFAULT_OUTPUT_BUCKET.",
                }
            ),
            400,
            {"Content-Type": "application/json"},
        )
    except Exception as error:
        return json.dumps({"error": f"Failed to upload output to GCS: {error}"}), 500, {"Content-Type": "application/json"}

    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    sample = os.urandom(5 * 1024 * 1024)
    encoded = base64.b64encode(sample).decode()

    class DummyRequest:
        def get_json(self, silent=False):
            return {"passes": 2, "data": encoded, "return_inline": True}

    print(video_transcoder(DummyRequest()))
