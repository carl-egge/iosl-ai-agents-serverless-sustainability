#!/usr/bin/env python3
"""Image converter that accepts uploaded image bytes and returns the requested format."""

from __future__ import annotations

import base64
import binascii
import io
import json
from typing import Dict

from PIL import Image
import functions_framework


def _load_image_bytes(request) -> bytes:
    raw_body = request.get_data(cache=False)
    if raw_body:
        return raw_body
    payload = request.get_json(silent=True) or {}
    encoded = payload.get("data")
    if isinstance(encoded, str):
        try:
            return base64.b64decode(encoded)
        except (ValueError, binascii.Error):
            pass
    raise ValueError("Send raw image bytes in the body or base64-encoded `data` field.")


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
