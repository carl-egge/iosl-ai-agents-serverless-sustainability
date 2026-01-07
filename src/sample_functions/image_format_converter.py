#!/usr/bin/env python3
"""Image converter for evaluation runs.

Evaluation improvements:
- Default behavior writes converted output to GCS and returns only metadata + output URI.
- Inline (base64) output is still available via `return_inline: true` for debugging.

Inputs supported (JSON only):
- base64 field `data`
- pointer: `gcs_uri=gs://bucket/path` or (`bucket`, `object`)

Request JSON (selected fields):
- format / output_format: e.g. WEBP, PNG, JPEG (default: WEBP)
- quality: 30..100 (default: 80)
- return_inline: bool (default: false)
- output_gcs_uri: gs://bucket/path (optional)
- output_bucket / output_object: alternative output location (optional)
- output_prefix: if output not specified and input came from GCS or DEFAULT_OUTPUT_BUCKET is set,
  write to {bucket}/{output_prefix}/<uuid>.<ext> (default prefix: eval_outputs/image_format_converter)

Environment variables (optional):
- DEFAULT_OUTPUT_BUCKET: bucket used if output location is not provided and input is not from GCS
- DEFAULT_OUTPUT_PREFIX_IMAGE: default output prefix (default: eval_outputs/image_format_converter)
- MAX_INLINE_MB: inline response limit in MB (default: 16)
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import uuid
from typing import Dict, Optional, Tuple

import functions_framework
from google.cloud import storage
from PIL import Image

_STORAGE_CLIENT: Optional[storage.Client] = None
_MAX_INLINE_BYTES: int = 16 * 1024 * 1024

try:
    _MAX_INLINE_BYTES = max(1, int(os.getenv("MAX_INLINE_MB", "16"))) * 1024 * 1024
except Exception:
    _MAX_INLINE_BYTES = 16 * 1024 * 1024

_DEFAULT_OUTPUT_BUCKET = os.getenv("DEFAULT_OUTPUT_BUCKET", "").strip() or None
_DEFAULT_OUTPUT_PREFIX = os.getenv(
    "DEFAULT_OUTPUT_PREFIX_IMAGE", "eval_outputs/image_format_converter"
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


def _load_image_bytes(payload: dict) -> Tuple[bytes, Optional[str]]:
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


def _pick_output_location(payload: dict, input_gcs_uri: Optional[str], ext: str) -> Tuple[str, str]:
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
    object_path = f"{prefix}/{uuid.uuid4().hex}.{ext}"
    return derived_bucket, object_path


@functions_framework.http
def image_format_converter(request) -> tuple[str, int, Dict[str, str]]:
    """Convert uploaded image bytes into the requested format."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return json.dumps({"error": "Expected a JSON object payload."}), 400, {"Content-Type": "application/json"}

    # Accept both keys for compatibility.
    target_format = str(payload.get("format", payload.get("output_format", "WEBP"))).upper()
    quality = int(payload.get("quality", 80))
    quality = max(30, min(quality, 100))

    return_inline = bool(payload.get("return_inline", False))

    try:
        input_bytes, input_gcs_uri = _load_image_bytes(payload)
    except ValueError as error:
        return json.dumps({"error": str(error)}), 400, {"Content-Type": "application/json"}

    try:
        image = Image.open(io.BytesIO(input_bytes))
    except Exception as error:
        return json.dumps({"error": f"Failed to decode image: {error}"}), 400, {"Content-Type": "application/json"}

    output_buffer = io.BytesIO()
    try:
        image.save(output_buffer, format=target_format, quality=quality)
    except Exception as error:
        return (
            json.dumps({"error": f"Failed to encode image as {target_format}: {error}"}),
            400,
            {"Content-Type": "application/json"},
        )

    output_bytes = output_buffer.getvalue()

    response: dict = {
        "scenario": "Short runtime + Large data",
        "format": target_format,
        "quality": quality,
        "input_bytes": len(input_bytes),
        "output_bytes": len(output_bytes),
        "input_size_mb": round(len(input_bytes) / (1024 * 1024), 4),
        "output_size_mb": round(len(output_bytes) / (1024 * 1024), 4),
        "input_gcs_uri": input_gcs_uri,
        "return_inline": return_inline,
    }

    if return_inline:
        if len(output_bytes) > _MAX_INLINE_BYTES:
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
        response["converted_image_base64"] = base64.b64encode(output_bytes).decode("utf-8")
        return json.dumps(response), 200, {"Content-Type": "application/json"}

    # Evaluation mode: write output to GCS and return URI + metadata.
    ext = target_format.lower()
    if ext == "jpeg":
        ext = "jpg"

    try:
        out_bucket, out_object = _pick_output_location(payload, input_gcs_uri, ext)
        content_type = f"image/{'jpeg' if ext == 'jpg' else ext}"
        output_gcs_uri = _upload_to_bucket(out_bucket, out_object, output_bytes, content_type=content_type)
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
    # Create a small PNG for local sanity checks.
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), color=(120, 200, 150)).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    class DummyRequest:
        def get_json(self, silent=False):
            return {"format": "WEBP", "quality": 75, "data": encoded, "return_inline": True}

    print(image_format_converter(DummyRequest()))
