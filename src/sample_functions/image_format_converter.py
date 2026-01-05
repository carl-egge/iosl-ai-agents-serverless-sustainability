#!/usr/bin/env python3
"""Simulated image converter that creates a large PNG and exports WebP to mimic heavy transfer loads."""

from __future__ import annotations

import io
import json
import os
from typing import Dict

from PIL import Image
import functions_framework


@functions_framework.http

def image_format_converter(request) -> tuple[str, int, Dict[str, str]]:
    """Generate a synthetic image in PNG format and transcode it to WebP in memory."""

    payload = request.get_json(silent=True) or {}
    width = int(payload.get("width", 4000))
    height = int(payload.get("height", 2500))
    width = max(1000, min(width, 5000))
    height = max(1000, min(height, 4000))

    # Generate pseudo-random RGB canvas so the data volume is real.
    canvas = os.urandom(width * height * 3)
    image = Image.frombytes("RGB", (width, height), canvas)

    png_bytes = io.BytesIO()
    image.save(png_bytes, format="PNG")
    png_data = png_bytes.getvalue()

    webp_bytes = io.BytesIO()
    image.save(webp_bytes, format="WEBP", quality=80)
    webp_data = webp_bytes.getvalue()

    response = {
        "scenario": "Short runtime + Large data",
        "input_size_mb": round(len(png_data) / (1024 * 1024), 2),
        "output_size_mb": round(len(webp_data) / (1024 * 1024), 2),
        "compression_ratio": round(len(webp_data) / len(png_data), 3),
        "frames": payload.get("frames", 1),
    }

    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    class DummyRequest:
        def get_json(self, silent=False):
            return {}

    print(image_format_converter(DummyRequest()))
