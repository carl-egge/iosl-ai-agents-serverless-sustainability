#!/usr/bin/env python3
"""
Load generator for Cloud Run function endpoints.

- Configure with a .env file (see .env.example).
- Supports multiple URLs per function (comma-separated), round-robin.
- Writes:
  - results.jsonl: one JSON object per request
  - summary.json: aggregated stats per function + url

Four stable scenarios that create clear scheduling tradeoffs:
 - latency-critical/high-QPS (health check)
 - data-heavy but short compute (image)
 - CPU-heavy with tiny payload (crypto)
 - CPU + data heavy batch-like (video)

Usage:
  python loadgen.py
"""

from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import os
import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv


# -----------------------------
# Helpers
# -----------------------------

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_int(env_val: str, default: int) -> int:
    try:
        return int(env_val)
    except Exception:
        return default

def parse_float(env_val: str, default: float) -> float:
    try:
        return float(env_val)
    except Exception:
        return default

def split_urls(s: str) -> List[str]:
    urls = [u.strip() for u in s.split(",") if u.strip()]
    return urls

def percentile(values: List[float], p: float) -> Optional[float]:
    """
    Simple percentile on sorted values.
    p in [0, 100]
    """
    if not values:
        return None
    if p <= 0:
        return min(values)
    if p >= 100:
        return max(values)
    vs = sorted(values)
    k = (len(vs) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(vs) - 1)
    if f == c:
        return vs[f]
    d0 = vs[f] * (c - k)
    d1 = vs[c] * (k - f)
    return d0 + d1

def safe_json_loads(s: str) -> Optional[dict]:
    try:
        return json.loads(s)
    except Exception:
        return None

# -----------------------------
# Configuration
# -----------------------------

@dataclass
class Config:
    run_id: str
    out_dir: str

    total_requests_per_url: int
    warmup_requests_per_url: int
    concurrency: int
    timeout_s: float
    verify_tls: bool

    sleep_between_requests_s: float
    jitter_s: float

    # Optional auth
    auth_bearer_token: Optional[str]
    extra_headers_json: Optional[str]

    # Function URLs (comma-separated per function)
    api_health_check_urls: List[str]
    image_format_converter_urls: List[str]
    crypto_key_gen_urls: List[str]
    video_transcoder_urls: List[str]

    # Payload configuration
    # image
    image_mode: str                  # "gcs" or "inline"
    image_gcs_uri: str
    image_inline_kb: int             # used if mode=inline
    image_return_inline: bool

    # crypto
    crypto_key_size: int
    crypto_public_exponent: int
    crypto_target_ms: int
    crypto_iterations: int

    # video
    video_mode: str                  # "gcs" or "inline"
    video_gcs_uri: str
    video_inline_kb: int
    video_passes: int
    video_target_ms: int
    video_return_inline: bool

    # Request path per service (optional)
    request_path: str                # default "/"

def load_config() -> Config:
    load_dotenv()

    run_id = os.getenv("RUN_ID", "").strip() or datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.getenv("OUT_DIR", "./out").strip()

    total_requests_per_url = parse_int(os.getenv("TOTAL_REQUESTS_PER_URL", "50"), 50)
    warmup_requests_per_url = parse_int(os.getenv("WARMUP_REQUESTS_PER_URL", "5"), 5)
    concurrency = parse_int(os.getenv("CONCURRENCY", "10"), 10)
    timeout_s = parse_float(os.getenv("TIMEOUT_S", "60"), 60.0)
    verify_tls = os.getenv("VERIFY_TLS", "true").lower() in ("1", "true", "yes", "y")

    sleep_between_requests_s = parse_float(os.getenv("SLEEP_BETWEEN_REQUESTS_S", "0"), 0.0)
    jitter_s = parse_float(os.getenv("JITTER_S", "0"), 0.0)

    auth_bearer_token = os.getenv("AUTH_BEARER_TOKEN", "").strip() or None
    extra_headers_json = os.getenv("EXTRA_HEADERS_JSON", "").strip() or None

    api_health_check_urls = split_urls(os.getenv("API_HEALTH_CHECK_URLS", ""))
    image_format_converter_urls = split_urls(os.getenv("IMAGE_FORMAT_CONVERTER_URLS", ""))
    crypto_key_gen_urls = split_urls(os.getenv("CRYPTO_KEY_GEN_URLS", ""))
    video_transcoder_urls = split_urls(os.getenv("VIDEO_TRANSCODER_URLS", ""))

    # image payload
    image_mode = os.getenv("IMAGE_MODE", "gcs").strip().lower()
    image_gcs_uri = os.getenv("IMAGE_GCS_URI", "").strip()
    image_inline_kb = parse_int(os.getenv("IMAGE_INLINE_KB", "256"), 256)
    image_return_inline = os.getenv("IMAGE_RETURN_INLINE", "false").lower() in ("1", "true", "yes", "y")

    # crypto payload
    crypto_key_size = parse_int(os.getenv("CRYPTO_KEY_SIZE", "2048"), 2048)
    crypto_public_exponent = parse_int(os.getenv("CRYPTO_PUBLIC_EXPONENT", "65537"), 65537)
    crypto_target_ms = parse_int(os.getenv("CRYPTO_TARGET_MS", "60000"), 60000)
    crypto_iterations = parse_int(os.getenv("CRYPTO_ITERATIONS", "1"), 1)

    # video payload
    video_mode = os.getenv("VIDEO_MODE", "gcs").strip().lower()
    video_gcs_uri = os.getenv("VIDEO_GCS_URI", "").strip()
    video_inline_kb = parse_int(os.getenv("VIDEO_INLINE_KB", "1024"), 1024)
    video_passes = parse_int(os.getenv("VIDEO_PASSES", "3"), 3)
    video_target_ms = parse_int(os.getenv("VIDEO_TARGET_MS", "0"), 0)  # 0 -> no target, just passes
    video_return_inline = os.getenv("VIDEO_RETURN_INLINE", "false").lower() in ("1", "true", "yes", "y")

    request_path = os.getenv("REQUEST_PATH", "/").strip() or "/"

    # Basic validation (non-fatal; will skip functions without URLs)
    return Config(
        run_id=run_id,
        out_dir=out_dir,
        total_requests_per_url=total_requests_per_url,
        warmup_requests_per_url=warmup_requests_per_url,
        concurrency=concurrency,
        timeout_s=timeout_s,
        verify_tls=verify_tls,
        sleep_between_requests_s=sleep_between_requests_s,
        jitter_s=jitter_s,
        auth_bearer_token=auth_bearer_token,
        extra_headers_json=extra_headers_json,
        api_health_check_urls=api_health_check_urls,
        image_format_converter_urls=image_format_converter_urls,
        crypto_key_gen_urls=crypto_key_gen_urls,
        video_transcoder_urls=video_transcoder_urls,
        image_mode=image_mode,
        image_gcs_uri=image_gcs_uri,
        image_inline_kb=image_inline_kb,
        image_return_inline=image_return_inline,
        crypto_key_size=crypto_key_size,
        crypto_public_exponent=crypto_public_exponent,
        crypto_target_ms=crypto_target_ms,
        crypto_iterations=crypto_iterations,
        video_mode=video_mode,
        video_gcs_uri=video_gcs_uri,
        video_inline_kb=video_inline_kb,
        video_passes=video_passes,
        video_target_ms=video_target_ms,
        video_return_inline=video_return_inline,
        request_path=request_path,
    )


# -----------------------------
# Payload builders
# -----------------------------

def make_inline_bytes(kb: int) -> bytes:
    # Deterministic-ish payload; change seed if you want
    rnd = random.Random(1337)
    return bytes(rnd.getrandbits(8) for _ in range(kb * 1024))

def build_payload_health() -> Dict[str, Any]:
    # api_health_check.py reads JSON but doesn't require it
    return {"ping": "ok", "ts": now_utc_iso()}

def build_payload_image(cfg: Config) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "output_format": "webp",
        "return_inline": cfg.image_return_inline,
    }
    if cfg.image_mode == "gcs":
        if not cfg.image_gcs_uri:
            raise ValueError("IMAGE_MODE=gcs but IMAGE_GCS_URI is empty")
        payload["gcs_uri"] = cfg.image_gcs_uri
    elif cfg.image_mode == "inline":
        # Keep inline limited (Cloud Run HTTP request limits apply)
        raw = make_inline_bytes(cfg.image_inline_kb)
        payload["data"] = base64.b64encode(raw).decode("utf-8")
    else:
        raise ValueError(f"Unknown IMAGE_MODE: {cfg.image_mode}")
    return payload

def build_payload_crypto(cfg: Config) -> Dict[str, Any]:
    return {
        "bits": cfg.crypto_key_size,
        "public_exponent": cfg.crypto_public_exponent,
        "target_ms": cfg.crypto_target_ms,
        "iterations": cfg.crypto_iterations,
        "return_public_key_pem": False,
    }

def build_payload_video(cfg: Config) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "passes": cfg.video_passes,
        "return_inline": cfg.video_return_inline,
    }
    if cfg.video_target_ms and cfg.video_target_ms > 0:
        payload["target_ms"] = cfg.video_target_ms

    if cfg.video_mode == "gcs":
        if not cfg.video_gcs_uri:
            raise ValueError("VIDEO_MODE=gcs but VIDEO_GCS_URI is empty")
        payload["gcs_uri"] = cfg.video_gcs_uri
    elif cfg.video_mode == "inline":
        raw = make_inline_bytes(cfg.video_inline_kb)
        payload["data"] = base64.b64encode(raw).decode("utf-8")
    else:
        raise ValueError(f"Unknown VIDEO_MODE: {cfg.video_mode}")
    return payload


# -----------------------------
# Request execution
# -----------------------------

def build_headers(cfg: Config) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if cfg.auth_bearer_token:
        headers["Authorization"] = f"Bearer {cfg.auth_bearer_token}"
    if cfg.extra_headers_json:
        extra = safe_json_loads(cfg.extra_headers_json)
        if isinstance(extra, dict):
            for k, v in extra.items():
                headers[str(k)] = str(v)
    return headers

def join_url(base: str, path: str) -> str:
    base = base.rstrip("/")
    path = "/" + path.lstrip("/")
    return base + path

def do_one_request(
    cfg: Config,
    function_id: str,
    base_url: str,
    payload: Dict[str, Any],
    request_index: int,
    phase: str,
) -> Dict[str, Any]:
    url = join_url(base_url, cfg.request_path)
    headers = build_headers(cfg)

    body_bytes = json.dumps(payload).encode("utf-8")
    request_bytes = len(body_bytes)

    # optional pacing
    if cfg.sleep_between_requests_s > 0:
        time.sleep(cfg.sleep_between_requests_s)
    if cfg.jitter_s > 0:
        time.sleep(random.random() * cfg.jitter_s)

    t0 = time.perf_counter()
    err: Optional[str] = None
    resp_text: Optional[str] = None
    resp_json: Optional[dict] = None
    status_code: Optional[int] = None
    resp_bytes: int = 0

    try:
        r = requests.post(
            url,
            data=body_bytes,
            headers=headers,
            timeout=cfg.timeout_s,
            verify=cfg.verify_tls,
        )
        status_code = r.status_code
        resp_text = r.text
        resp_bytes = len(r.content) if r.content is not None else 0
        # Try parse JSON if possible
        resp_json = safe_json_loads(resp_text) if resp_text else None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    t1 = time.perf_counter()

    record: Dict[str, Any] = {
        "ts_utc": now_utc_iso(),
        "run_id": cfg.run_id,
        "phase": phase,  # warmup | measured
        "function_id": function_id,
        "base_url": base_url,
        "url": url,
        "request_index": request_index,
        "latency_ms": (t1 - t0) * 1000.0,
        "status_code": status_code,
        "request_bytes": request_bytes,
        "response_bytes": resp_bytes,
        "error": err,
    }

    # Include response JSON if it exists, but keep it bounded
    if resp_json is not None:
        record["response_json"] = resp_json
    else:
        # keep only a short snippet to avoid huge logs
        if resp_text:
            record["response_snippet"] = resp_text[:5000]

    return record


def run_for_function(cfg: Config, function_id: str, urls: List[str], payload_builder) -> List[Dict[str, Any]]:
    if not urls:
        print(f"[SKIP] {function_id}: no URLs configured.")
        return []

    results: List[Dict[str, Any]] = []

    def run_phase(phase: str, n_per_url: int):
        nonlocal results
        jobs: List[Tuple[str, int]] = []
        for u in urls:
            for i in range(n_per_url):
                jobs.append((u, i))

        random.shuffle(jobs)

        with cf.ThreadPoolExecutor(max_workers=cfg.concurrency) as ex:
            futs = []
            for (u, i) in jobs:
                payload = payload_builder(cfg) if callable(payload_builder) else payload_builder
                futs.append(ex.submit(do_one_request, cfg, function_id, u, payload, i, phase))
            for f in cf.as_completed(futs):
                results.append(f.result())

    # Warmup
    if cfg.warmup_requests_per_url > 0:
        print(f"[WARMUP] {function_id}: {cfg.warmup_requests_per_url} per URL x {len(urls)} URL(s)")
        run_phase("warmup", cfg.warmup_requests_per_url)

    # Measured
    print(f"[MEASURED] {function_id}: {cfg.total_requests_per_url} per URL x {len(urls)} URL(s)")
    run_phase("measured", cfg.total_requests_per_url)

    return results


# -----------------------------
# Aggregation
# -----------------------------

def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Aggregates only phase=="measured".
    Groups by (function_id, base_url).
    """
    measured = [r for r in records if r.get("phase") == "measured"]
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in measured:
        key = (r.get("function_id", "unknown"), r.get("base_url", ""))
        groups.setdefault(key, []).append(r)

    out: Dict[str, Any] = {
        "run_id": records[0]["run_id"] if records else None,
        "generated_at_utc": now_utc_iso(),
        "total_records": len(records),
        "measured_records": len(measured),
        "by_function_url": {},
    }

    for (function_id, base_url), rs in groups.items():
        latencies = [float(r["latency_ms"]) for r in rs if r.get("latency_ms") is not None and r.get("error") is None]
        errors = [r for r in rs if r.get("error") is not None or (r.get("status_code") is not None and int(r["status_code"]) >= 400)]
        ok = [r for r in rs if r not in errors]

        req_bytes = [int(r.get("request_bytes", 0)) for r in ok]
        resp_bytes = [int(r.get("response_bytes", 0)) for r in ok]

        entry = {
            "count": len(rs),
            "ok_count": len(ok),
            "error_count": len(errors),
            "http_status_counts": {},
            "latency_ms": {
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
                "p99": percentile(latencies, 99),
                "min": min(latencies) if latencies else None,
                "max": max(latencies) if latencies else None,
                "mean": statistics.mean(latencies) if latencies else None,
            },
            "bytes": {
                "request_mean": statistics.mean(req_bytes) if req_bytes else None,
                "response_mean": statistics.mean(resp_bytes) if resp_bytes else None,
            },
        }

        # Status distribution
        status_counts: Dict[str, int] = {}
        for r in rs:
            sc = r.get("status_code")
            k = str(sc) if sc is not None else "null"
            status_counts[k] = status_counts.get(k, 0) + 1
        entry["http_status_counts"] = status_counts

        out["by_function_url"].setdefault(function_id, {})
        out["by_function_url"][function_id][base_url] = entry

    return out


# -----------------------------
# Main
# -----------------------------

def main():
    cfg = load_config()
    os.makedirs(cfg.out_dir, exist_ok=True)

    print(f"Run ID: {cfg.run_id}")
    print(f"Output: {os.path.abspath(cfg.out_dir)}")
    print(f"Concurrency: {cfg.concurrency} | Timeout: {cfg.timeout_s}s | Verify TLS: {cfg.verify_tls}")

    all_records: List[Dict[str, Any]] = []

    # Run each function group
    all_records += run_for_function(cfg, "api_health_check", cfg.api_health_check_urls, lambda _cfg: build_payload_health())
    all_records += run_for_function(cfg, "image_format_converter", cfg.image_format_converter_urls, build_payload_image)
    all_records += run_for_function(cfg, "crypto_key_gen", cfg.crypto_key_gen_urls, build_payload_crypto)
    all_records += run_for_function(cfg, "video_transcoder", cfg.video_transcoder_urls, build_payload_video)

    # Write raw results (jsonl)
    results_path = os.path.join(cfg.out_dir, f"results_{cfg.run_id}.jsonl")
    with open(results_path, "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Write summary
    summary_obj = summarize(all_records)
    summary_path = os.path.join(cfg.out_dir, f"summary_{cfg.run_id}.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_obj, f, indent=2, ensure_ascii=False)

    print(f"\nWrote:\n  {results_path}\n  {summary_path}")

    # Print a small console summary
    by_func = summary_obj.get("by_function_url", {})
    for fn, by_url in by_func.items():
        for base_url, stats in by_url.items():
            p50 = stats["latency_ms"]["p50"]
            p95 = stats["latency_ms"]["p95"]
            ok = stats["ok_count"]
            err = stats["error_count"]
            print(f"{fn} @ {base_url} | ok={ok} err={err} | p50={p50:.1f}ms p95={p95:.1f}ms" if p50 and p95 else
                  f"{fn} @ {base_url} | ok={ok} err={err}")

if __name__ == "__main__":
    main()
