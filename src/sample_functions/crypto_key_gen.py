#!/usr/bin/env python3
"""Generate RSA key pairs to exercise sustained CPU work (tunable).

Evaluation-oriented behavior:
- Keeps response small (no private key material).
- Allows tuning runtime via `iterations` and/or `target_ms`.

Request JSON (all optional):
- bits / key_bits: one of {2048, 3072, 4096, 8192} (default: 3072)
- public_exponent: RSA public exponent (default: 65537)
- iterations: number of RSA keypairs to generate (default: 1)
- target_ms: minimum wall time to spend (default: 0 => no minimum)
- burn_chunk_kb: chunk size for CPU-burn hashing (default: 256)
- return_public_key_pem: whether to return full public key PEM (default: false)

Notes:
- If `target_ms` exceeds the time needed for RSA generation, we burn CPU time by hashing
  a rolling buffer until the minimum wall time is reached. This produces more stable
  runtimes across regions/CPU allocations than relying purely on RSA generation loops.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Dict, Tuple

import functions_framework
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_ALLOWED_BITS = (2048, 3072, 4096, 8192)


def _parse_bits(payload: dict) -> int:
    bits = payload.get("bits", payload.get("key_bits", 3072))
    try:
        bits = int(bits)
    except Exception:
        bits = 3072
    if bits not in _ALLOWED_BITS:
        bits = 3072
    return bits


def _parse_int(payload: dict, key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        val = int(payload.get(key, default))
    except Exception:
        val = default
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def _burn_cpu_until(deadline_s: float, chunk_kb: int) -> Tuple[int, str]:
    """Burn CPU by hashing a rolling buffer; returns (hash_iterations, final_digest)."""
    data = bytearray(os.urandom(max(1, chunk_kb) * 1024))
    digest = b""
    iters = 0
    while time.perf_counter() < deadline_s:
        digest = hashlib.sha256(data).digest()
        # mutate buffer to prevent trivial caching
        for i in range(min(32, len(data))):
            data[i] ^= digest[i % len(digest)]
        iters += 1
    return iters, digest.hex() if digest else ""


@functions_framework.http
def crypto_key_gen(request) -> tuple[str, int, Dict[str, str]]:
    """Generate RSA public keys and optionally burn CPU until a target duration is met."""
    payload = request.get_json(silent=True) or {}

    bits = _parse_bits(payload)
    public_exponent = _parse_int(payload, "public_exponent", 65537, minimum=3, maximum=2**31 - 1)

    iterations = _parse_int(payload, "iterations", 1, minimum=1, maximum=50)
    target_ms = _parse_int(payload, "target_ms", 0, minimum=0, maximum=15 * 60 * 1000)  # cap 15 min
    burn_chunk_kb = _parse_int(payload, "burn_chunk_kb", 256, minimum=1, maximum=4096)
    return_public_key_pem = bool(payload.get("return_public_key_pem", False))

    wall_start = time.perf_counter()
    cpu_start = time.process_time()

    public_keys: list[bytes] = []

    # Step 1: deterministic crypto work
    for _ in range(iterations):
        private_key = rsa.generate_private_key(public_exponent=public_exponent, key_size=bits)
        pub = private_key.public_key()
        public_bytes = pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_keys.append(public_bytes)

    # Step 2: optional CPU burn to reach target_ms
    burn_iters = 0
    burn_digest = ""
    if target_ms > 0:
        deadline_s = wall_start + (target_ms / 1000.0)
        if time.perf_counter() < deadline_s:
            burn_iters, burn_digest = _burn_cpu_until(deadline_s, burn_chunk_kb)

    wall_ms = round((time.perf_counter() - wall_start) * 1000.0, 3)
    cpu_ms = round((time.process_time() - cpu_start) * 1000.0, 3)

    response: dict = {
        "scenario": "Long runtime + Little data",
        "key_bits": bits,
        "public_exponent": public_exponent,
        "iterations": iterations,
        "target_ms": target_ms,
        "burn_hash_iterations": burn_iters,
        "burn_final_digest": burn_digest or None,
        # Optional internal timings (your main wrapper logs the authoritative ones)
        "wall_ms_internal": wall_ms,
        "cpu_ms_internal": cpu_ms,
    }

    if return_public_key_pem:
        # Keep response bounded: if multiple keys were generated, return only the last one.
        response["public_key_pem"] = public_keys[-1].decode("utf-8", errors="ignore") if public_keys else None

    return json.dumps(response), 200, {"Content-Type": "application/json"}

if __name__ == "__main__":
    class DummyRequest:
        def get_json(self, silent=False):
            return {"bits": 4096}

    print(crypto_key_gen(DummyRequest()))
