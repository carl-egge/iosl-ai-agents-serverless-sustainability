#!/usr/bin/env python3
"""Generate RSA key pairs to exercise long-running CPU work."""

import json
from typing import Dict

import functions_framework
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@functions_framework.http

def crypto_key_gen(request) -> tuple[str, int, Dict[str, str]]:
    """Return metadata from generating an RSA private/public pair."""

    payload = request.get_json(silent=True) or {}
    bits = int(payload.get("bits", 3072))
    if bits not in (2048, 3072, 4096, 8192):
        bits = 3072

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=bits)
    pub = private_key.public_key()
    public_bytes = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    response = {
        "scenario": "Long runtime + Little data",
        "key_bits": bits,
        "public_key_snippet": public_bytes[:80].decode("utf-8", errors="ignore"),
    }

    return json.dumps(response), 200, {"Content-Type": "application/json"}


if __name__ == "__main__":
    class DummyRequest:
        def get_json(self, silent=False):
            return {"bits": 4096}

    print(crypto_key_gen(DummyRequest()))
