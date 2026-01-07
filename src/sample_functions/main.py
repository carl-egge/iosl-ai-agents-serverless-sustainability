#!/usr/bin/env python3
"""Entrypoint that re-exports each sample handler for Buildpack discovery.

This variant adds a lightweight metrics wrapper around each handler so that
invocation-level telemetry can be harvested from Cloud Logging.
"""

from __future__ import annotations

import json
import os
import time
from importlib import import_module
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "api_health_check",
    "carbon_api_call",
    "crypto_key_gen",
    "image_format_converter",
    "simple_addition",
    "video_transcoder",
    "write_to_bucket",
    "available_functions",
]

FunctionCallable = Callable[..., Any]

# Process-level constants for "cold start" heuristics.
_PROCESS_START_UNIX = time.time()
_FIRST_INVOKE: dict[str, bool] = {}  # function_id -> seen?


def _safe_get_max_rss_kb() -> Optional[int]:
    """Best-effort max RSS in KB (Linux: ru_maxrss is KB)."""
    try:
        import resource  # stdlib on Linux

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except Exception:
        return None


def _estimate_request_bytes(request: Any) -> Optional[int]:
    """Estimate incoming request bytes without breaking downstream handlers."""
    try:
        cl = getattr(request, "content_length", None)
        if isinstance(cl, int) and cl >= 0:
            return cl
    except Exception:
        pass

    # Fall back to reading request body with caching enabled, so handlers can still read it.
    try:
        if hasattr(request, "get_data"):
            data = request.get_data(cache=True)  # important: cache=True
            if data is not None:
                return int(len(data))
    except Exception:
        pass

    return None


def _normalize_handler_return(rv: Any) -> Tuple[bytes, int, Dict[str, str]]:
    """
    Normalize common Functions Framework return shapes:
      - (body, status, headers)
      - (body, status)
      - body
      - flask.Response-like object
    """
    body: Any = rv
    status: int = 200
    headers: Dict[str, str] = {}

    # Tuple/list return
    if isinstance(rv, (tuple, list)):
        if len(rv) >= 1:
            body = rv[0]
        if len(rv) >= 2 and rv[1] is not None:
            try:
                status = int(rv[1])
            except Exception:
                status = 200
        if len(rv) >= 3 and isinstance(rv[2], dict):
            headers = {str(k): str(v) for k, v in rv[2].items()}

    # flask.Response-like
    if hasattr(body, "get_data") and callable(getattr(body, "get_data")):
        try:
            data = body.get_data()  # bytes
            st = getattr(body, "status_code", None)
            if isinstance(st, int):
                status = st
            hdrs = getattr(body, "headers", None)
            if hdrs is not None:
                try:
                    headers = {str(k): str(v) for k, v in dict(hdrs).items()}
                except Exception:
                    pass
            return bytes(data), status, headers
        except Exception:
            pass

    # Body encoding
    if body is None:
        body_bytes = b""
    elif isinstance(body, (bytes, bytearray)):
        body_bytes = bytes(body)
    elif isinstance(body, str):
        body_bytes = body.encode("utf-8", errors="replace")
    elif isinstance(body, (dict, list)):
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    else:
        body_bytes = str(body).encode("utf-8", errors="replace")

    return body_bytes, status, headers


def _sanitize_response_json_for_logs(parsed: Any) -> Optional[dict]:
    """
    If response is JSON, keep small metadata fields but avoid logging huge blobs (base64, etc.).
    """
    if not isinstance(parsed, dict):
        return None

    drop_keys = {
        "converted_image",
        "processed_data",
        "converted_image_base64",
        "processed_data_base64",
    }  # large base64 fields in your samples
    out: dict[str, Any] = {}

    for k, v in parsed.items():
        if k in drop_keys:
            continue
        # Avoid very large strings in logs
        if isinstance(v, str) and len(v) > 512:
            out[k] = v[:512] + "...(truncated)"
        else:
            out[k] = v
    return out


def _emit_metrics_log(line: dict) -> None:
    """
    Emit one structured JSON log line to stdout.
    Cloud Run/Cloud Logging will ingest it as a structured payload.
    """
    try:
        print(json.dumps(line, ensure_ascii=False))
    except Exception:
        # Best-effort fallback
        print(str(line))


def _with_metrics(function_id: str, handler: FunctionCallable) -> FunctionCallable:
    """Wrap a Functions Framework handler with timing/size/memory instrumentation."""

    def _wrapped(*args: Any, **kwargs: Any) -> Any:
        request = args[0] if args else kwargs.get("request")

        cold_start = not _FIRST_INVOKE.get(function_id, False)
        _FIRST_INVOKE[function_id] = True

        req_bytes = _estimate_request_bytes(request)

        wall_t0 = time.perf_counter()
        cpu_t0 = time.process_time()

        error: Optional[str] = None
        error_type: Optional[str] = None
        rv: Any = None

        try:
            rv = handler(*args, **kwargs)
        except Exception as exc:
            error_type = type(exc).__name__
            error = str(exc)
            raise
        finally:
            wall_t1 = time.perf_counter()
            cpu_t1 = time.process_time()

            body_bytes: bytes = b""
            status_code: Optional[int] = None
            resp_bytes: Optional[int] = None
            resp_meta: Optional[dict] = None

            try:
                body_bytes, status_code, _headers = _normalize_handler_return(rv)
                resp_bytes = len(body_bytes)

                # If body looks like JSON, parse and keep a sanitized subset for logs
                try:
                    parsed = json.loads(body_bytes.decode("utf-8", errors="replace"))
                    resp_meta = _sanitize_response_json_for_logs(parsed)
                except Exception:
                    resp_meta = None
            except Exception:
                # Never let metrics break the request path.
                pass

            log_line = {
                "type": "invocation_metrics",
                "function_id": function_id,
                "cold_start": cold_start,
                "process_uptime_s": round(time.time() - _PROCESS_START_UNIX, 3),
                "wall_ms": round((wall_t1 - wall_t0) * 1000.0, 3),
                "cpu_ms": round((cpu_t1 - cpu_t0) * 1000.0, 3),
                "max_rss_kb": _safe_get_max_rss_kb(),
                "request_bytes": req_bytes,
                "response_bytes": resp_bytes,
                "status_code": status_code,
                "error_type": error_type,
                "error": error,
                # Helpful Cloud Run env metadata (if present)
                "k_service": os.getenv("K_SERVICE"),
                "k_revision": os.getenv("K_REVISION"),
                "k_configuration": os.getenv("K_CONFIGURATION"),
                # Optional: request metadata (best-effort)
                "http_method": getattr(request, "method", None),
                "http_path": getattr(request, "path", None),
            }
            if resp_meta is not None:
                log_line["response_meta"] = resp_meta

            _emit_metrics_log(log_line)

        return rv

    _wrapped.__name__ = getattr(handler, "__name__", function_id)
    _wrapped.__doc__ = getattr(handler, "__doc__", None)
    return _wrapped


def _lazy_loader(module_name: str, attr_name: str, function_id: Optional[str] = None) -> FunctionCallable:
    """Return a callable that imports the handler the first time it is invoked, then wraps with metrics."""

    cached: FunctionCallable | None = None
    fid = function_id or attr_name

    def _caller(*args: Any, **kwargs: Any) -> Any:
        nonlocal cached
        if cached is None:
            module = import_module(module_name)
            raw = getattr(module, attr_name)
            cached = _with_metrics(fid, raw)
        return cached(*args, **kwargs)

    _caller.__name__ = attr_name
    _caller.__doc__ = _caller.__doc__ or f"Lazy proxy for {module_name}.{attr_name} with metrics"
    return _caller


# Exported handlers (targets for Cloud Run / Functions Framework)
api_health_check = _lazy_loader("api_health_check", "api_health_check")
carbon_api_call = _lazy_loader("carbon_api_call", "carbon_api_call")
crypto_key_gen = _lazy_loader("crypto_key_gen", "crypto_key_gen")
image_format_converter = _lazy_loader("image_format_converter", "image_format_converter")
simple_addition = _lazy_loader("simple_addition", "simple_addition")
video_transcoder = _lazy_loader("video_transcoder", "video_transcoder")
write_to_bucket = _lazy_loader("write_to_bucket", "write_to_bucket")

FUNCTION_REGISTRY: dict[str, FunctionCallable] = {
    "api_health_check": api_health_check,
    "carbon_api_call": carbon_api_call,
    "crypto_key_gen": crypto_key_gen,
    "image_format_converter": image_format_converter,
    "simple_addition": simple_addition,
    "video_transcoder": video_transcoder,
    "write_to_bucket": write_to_bucket,
}


def available_functions() -> list[str]:
    """List the handler names that gcloud Run can deploy from this directory."""
    return list(FUNCTION_REGISTRY)
