#!/usr/bin/env python3
"""Image converter that accepts uploaded image bytes (body/base64), multipart files, or a GCS pointer and returns the requested format."""

from __future__ import annotations

import base64
import binascii
import io
import json
from typing import Dict, Optional, Tuple

from PIL import Image
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


def _load_image_bytes(request) -> bytes:
    if request.files:
        file_storage = next(iter(request.files.values()))
        return file_storage.read()

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
    raw_body = request.get_data(cache=False)
    if raw_body and "multipart/" not in (request.content_type or ""):
        return raw_body
    raise ValueError(
        "Send raw image bytes (body), multipart/form-data file, base64 `data`, or specify `bucket`/`object` or `gcs_uri=gs://bucket/path`."
    )


@functions_framework.http
def image_format_converter(request) -> tuple[str, int, Dict[str, str]]:
    """Convert uploaded image bytes into the requested format."""

    payload = request.get_json(silent=True) or {}
    target_format = payload.get("format", "WEBP").upper()
    quality = int(payload.get("quality", 80))
    quality = max(30, min(quality, 100))

    try:
        input_bytes = _load_image_bytes(request)
    except ValueError as error:
        return (
            json.dumps({"error": str(error)}),
            400,
            {"Content-Type": "application/json"},
        )

    image = Image.open(io.BytesIO(input_bytes))
    output_buffer = io.BytesIO()
    image.save(output_buffer, format=target_format, quality=quality)
    output_bytes = output_buffer.getvalue()

    response = {
        "scenario": "Short runtime + Large data",
        "input_size_mb": round(len(input_bytes) / (1024 * 1024), 2),
        "output_size_mb": round(len(output_bytes) / (1024 * 1024), 2),
        "format": target_format,
        "quality": quality,
        "converted_image": base64.b64encode(output_bytes).decode(),
    }
    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    # Create a small PNG for local sanity checks.
    buffer = io.BytesIO()
    Image.new("RGB", (128, 128), color=(120, 200, 150)).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()

    class DummyRequest:
        def get_json(self, silent=False):
            return {"format": "WEBP", "quality": 75, "data": encoded}

        def get_data(self, cache=False):
            return b""

    print(image_format_converter(DummyRequest()))
