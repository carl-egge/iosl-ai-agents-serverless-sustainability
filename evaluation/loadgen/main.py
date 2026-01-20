#!/usr/bin/env python3
"""
Cloud Run Job load generator aligned with evaluation/EVALUATION.md.

Generates the fixed hourly invocation mix with deterministic minute slots and
stable jitter, then dispatches per the selected scenario (A/B/C).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests


FUNCTION_INVOCATIONS_PER_HOUR: Dict[str, int] = {
    "api_health_check": 20,
    "crypto_key_gen": 3,
    "image_format_converter": 2,
    "video_transcoder": 1,
}

MINUTE_OFFSETS: Dict[str, int] = {
    "api_health_check": 0,
    "crypto_key_gen": 7,
    "image_format_converter": 13,
    "video_transcoder": 29,
}


@dataclass
class Config:
    scenario: str
    experiment_id: str
    trace_hour: datetime
    jitter_s: float
    request_timeout_s: float
    verify_tls: bool
    dry_run: bool
    fixed_region: Optional[str]
    dispatcher_url: Optional[str]
    function_urls: Dict[str, Dict[str, str]]
    hourly_region_map: Dict[int, str]
    hourly_region_map_source: str
    image_gcs_uri: str
    video_gcs_uri: str
    image_format: str
    image_quality: int
    crypto_bits: int
    video_passes: int
    auth_bearer_token: Optional[str]
    extra_headers: Dict[str, str]
    dispatcher_auth_bearer_token: Optional[str]
    dispatcher_extra_headers: Optional[Dict[str, str]]


@dataclass
class Invocation:
    function_id: str
    index: int
    event_id: str
    scheduled_time: datetime
    payload: Dict[str, Any]
    dispatch_result: Optional[Dict[str, Any]] = None
    dispatch_sent_time: Optional[datetime] = None
    target_time: Optional[datetime] = None
    target_region: Optional[str] = None
    target_url: Optional[str] = None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def format_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bool(val: Optional[str], default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "y")


def parse_int(val: Optional[str], default: int) -> int:
    try:
        return int(val) if val is not None else default
    except Exception:
        return default


def parse_float(val: Optional[str], default: float) -> float:
    try:
        return float(val) if val is not None else default
    except Exception:
        return default

def parse_optional_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "y"):
            return True
        if text in ("0", "false", "no", "n"):
            return False
    return None


def parse_optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_trace_hour(value: Optional[str]) -> datetime:
    if value:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        dt = now_utc()
    return dt.replace(minute=0, second=0, microsecond=0)


def resolve_path(path: str) -> str:
    if os.path.isabs(path):
        return path
    return os.path.join(os.path.dirname(__file__), path)


def load_json_from_env(env_key: str) -> Optional[dict]:
    raw = os.getenv(env_key)
    if raw:
        return json.loads(raw)
    return None


def load_json_from_env_or_path(env_key: str, path_key: str) -> Optional[dict]:
    raw = os.getenv(env_key)
    if raw:
        return json.loads(raw)
    path = os.getenv(path_key)
    if path:
        with open(resolve_path(path), "r", encoding="utf-8") as handle:
            return json.load(handle)
    return None


def parse_headers_json(value: Optional[str]) -> Dict[str, str]:
    if not value:
        return {}
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Header JSON must be an object.")
    return {str(k): str(v) for k, v in data.items()}


def deterministic_jitter_seconds(event_id: str, jitter_s: float, seconds_into_hour: int) -> float:
    if jitter_s <= 0:
        return 0.0
    max_ms = max(1, int(jitter_s * 1000))
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    jitter_ms = int(digest[:8], 16) % (max_ms + 1)
    jitter = jitter_ms / 1000.0
    max_allowed = max(0.0, (3600 - 1) - seconds_into_hour)
    return min(jitter, max_allowed)


def evenly_spaced_minutes(count: int, offset: int) -> List[int]:
    if count <= 0:
        return []
    step = 60 / count
    minutes = [int(i * step) for i in range(count)]
    return [int((m + offset) % 60) for m in minutes]


def safe_json_loads(text: Optional[str]) -> Optional[Any]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def build_headers(token: Optional[str], extra_headers: Dict[str, str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra_headers)
    return headers


def post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout_s: float,
    verify_tls: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    body_bytes = json.dumps(payload).encode("utf-8")
    request_bytes = len(body_bytes)
    started = time.perf_counter()

    if dry_run:
        return {
            "url": url,
            "status_code": None,
            "latency_ms": 0.0,
            "request_bytes": request_bytes,
            "response_bytes": 0,
            "error": None,
            "response_json": None,
            "response_snippet": None,
        }

    status_code: Optional[int] = None
    response_text: Optional[str] = None
    response_bytes: int = 0
    error: Optional[str] = None

    try:
        resp = requests.post(
            url,
            data=body_bytes,
            headers=headers,
            timeout=timeout_s,
            verify=verify_tls,
        )
        status_code = resp.status_code
        response_text = resp.text
        response_bytes = len(resp.content) if resp.content is not None else 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    latency_ms = (time.perf_counter() - started) * 1000.0
    response_json = safe_json_loads(response_text)
    response_snippet = None
    if response_json is None and response_text:
        response_snippet = response_text[:1000]

    return {
        "url": url,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "request_bytes": request_bytes,
        "response_bytes": response_bytes,
        "error": error,
        "response_json": response_json,
        "response_snippet": response_snippet,
    }


def parse_hourly_region_map(raw: Optional[dict]) -> Dict[int, str]:
    if raw is None:
        return {}
    out: Dict[int, str] = {}
    for key, value in raw.items():
        hour = int(key)
        if hour < 0 or hour > 23:
            raise ValueError(f"Invalid hour in hourly_region_map: {hour}")
        out[hour] = str(value)
    return out


def validate_function_urls(raw: Optional[dict]) -> Dict[str, Dict[str, str]]:
    if raw is None:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for fn, mapping in raw.items():
        if not isinstance(mapping, dict):
            raise ValueError(f"Function URL mapping for {fn} must be an object.")
        out[fn] = {str(region): str(url) for region, url in mapping.items()}
    return out


def load_carbon_forecast() -> Optional[dict]:
    raw = os.getenv("CARBON_FORECAST_JSON")
    if raw:
        return json.loads(raw)

    path = os.getenv("CARBON_FORECAST_PATH")
    if path:
        with open(resolve_path(path), "r", encoding="utf-8") as handle:
            return json.load(handle)

    bucket_name = os.getenv("CARBON_FORECAST_GCS_BUCKET")
    if bucket_name:
        object_name = os.getenv("CARBON_FORECAST_GCS_OBJECT", "carbon_forecasts.json")
        from google.cloud import storage

        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        return json.loads(blob.download_as_bytes().decode("utf-8"))

    return None


def build_hourly_region_map_from_forecast(forecast: dict, target_date: date) -> Dict[int, str]:
    regions = forecast.get("regions", {})
    best_by_hour: Dict[int, Dict[str, Any]] = {}

    for region_id, region in regions.items():
        region_name = region.get("gcloud_region") or region_id
        entries = region.get("forecast", [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            intensity = entry.get("carbonIntensity")
            try:
                intensity_val = float(intensity)
            except Exception:
                continue
            dt = parse_datetime(entry.get("datetime"))
            if dt is None or dt.date() != target_date:
                continue
            hour = dt.hour
            best = best_by_hour.get(hour)
            if best is None or intensity_val < best["carbon_intensity"]:
                best_by_hour[hour] = {
                    "region": str(region_name),
                    "carbon_intensity": intensity_val,
                }

    return {hour: info["region"] for hour, info in best_by_hour.items()}


def load_config() -> Config:
    scenario = os.getenv("SCENARIO", "").strip().upper()
    if scenario not in ("A", "B", "C"):
        raise ValueError("SCENARIO must be one of A, B, C.")

    experiment_id = os.getenv("EXPERIMENT_ID", "").strip()
    if not experiment_id:
        raise ValueError("EXPERIMENT_ID is required.")

    trace_hour = parse_trace_hour(os.getenv("TRACE_HOUR_UTC"))

    jitter_s = max(0.0, parse_float(os.getenv("JITTER_S"), 15.0))
    request_timeout_s = max(1.0, parse_float(os.getenv("TIMEOUT_S"), 120.0))
    verify_tls = parse_bool(os.getenv("VERIFY_TLS"), True)
    dry_run = parse_bool(os.getenv("DRY_RUN"), False)

    fixed_region = os.getenv("FIXED_REGION")
    dispatcher_url = os.getenv("DISPATCHER_URL")

    function_urls = validate_function_urls(load_json_from_env("FUNCTION_URLS_JSON"))
    hourly_region_map = parse_hourly_region_map(
        load_json_from_env_or_path("HOURLY_REGION_MAP_JSON", "HOURLY_REGION_MAP_PATH")
    )
    hourly_region_map_source = "hourly_region_map"

    if scenario == "B":
        forecast = load_carbon_forecast()
        if forecast:
            derived_map = build_hourly_region_map_from_forecast(forecast, trace_hour.date())
            if derived_map:
                hourly_region_map = derived_map
                hourly_region_map_source = "carbon_forecast"

    image_gcs_uri = os.getenv("IMAGE_GCS_URI", "").strip()
    video_gcs_uri = os.getenv("VIDEO_GCS_URI", "").strip()
    if not image_gcs_uri or not video_gcs_uri:
        raise ValueError("IMAGE_GCS_URI and VIDEO_GCS_URI are required.")

    image_format = os.getenv("IMAGE_FORMAT", "WEBP").strip().upper()
    image_quality = parse_int(os.getenv("IMAGE_QUALITY"), 85)
    image_quality = max(1, min(image_quality, 100))

    crypto_bits = parse_int(os.getenv("CRYPTO_BITS"), 4096)
    video_passes = parse_int(os.getenv("VIDEO_PASSES"), 2)
    video_passes = max(1, min(video_passes, 10))

    auth_bearer_token = os.getenv("AUTH_BEARER_TOKEN", "").strip() or None
    extra_headers = parse_headers_json(os.getenv("EXTRA_HEADERS_JSON", ""))
    dispatcher_auth_bearer_token = os.getenv("DISPATCHER_AUTH_BEARER_TOKEN", "").strip() or None
    dispatcher_extra_headers_raw = os.getenv("DISPATCHER_EXTRA_HEADERS_JSON")
    dispatcher_extra_headers = (
        parse_headers_json(dispatcher_extra_headers_raw)
        if dispatcher_extra_headers_raw
        else None
    )

    return Config(
        scenario=scenario,
        experiment_id=experiment_id,
        trace_hour=trace_hour,
        jitter_s=jitter_s,
        request_timeout_s=request_timeout_s,
        verify_tls=verify_tls,
        dry_run=dry_run,
        fixed_region=fixed_region,
        dispatcher_url=dispatcher_url,
        function_urls=function_urls,
        hourly_region_map=hourly_region_map,
        hourly_region_map_source=hourly_region_map_source,
        image_gcs_uri=image_gcs_uri,
        video_gcs_uri=video_gcs_uri,
        image_format=image_format,
        image_quality=image_quality,
        crypto_bits=crypto_bits,
        video_passes=video_passes,
        auth_bearer_token=auth_bearer_token,
        extra_headers=extra_headers,
        dispatcher_auth_bearer_token=dispatcher_auth_bearer_token,
        dispatcher_extra_headers=dispatcher_extra_headers,
    )


def select_region(cfg: Config, hour: int) -> str:
    if cfg.scenario == "A":
        if not cfg.fixed_region:
            raise ValueError("FIXED_REGION is required for scenario A.")
        return cfg.fixed_region
    if cfg.scenario == "B":
        if hour not in cfg.hourly_region_map:
            raise ValueError(f"No hourly region mapping for hour {hour}.")
        return cfg.hourly_region_map[hour]
    raise ValueError("select_region called for scenario C.")


def lookup_function_url(cfg: Config, function_id: str, region: str) -> str:
    mapping = cfg.function_urls.get(function_id, {})
    if not mapping:
        raise ValueError(f"No URL mapping configured for function {function_id}.")
    if region in mapping:
        return mapping[region]
    raise ValueError(f"No URL for function {function_id} in region {region}.")


def build_payload(cfg: Config, function_id: str, event_id: str, trace_hour: str) -> Dict[str, Any]:
    metadata = {
        "experiment_id": cfg.experiment_id,
        "scenario": cfg.scenario,
        "event_id": event_id,
        "trace_hour": trace_hour,
    }

    if function_id == "api_health_check":
        payload = {"check": "ping"}
    elif function_id == "image_format_converter":
        payload = {
            "gcs_uri": cfg.image_gcs_uri,
            "format": cfg.image_format,
            "quality": cfg.image_quality,
            "return_inline": False,
        }
    elif function_id == "crypto_key_gen":
        payload = {"bits": cfg.crypto_bits}
    elif function_id == "video_transcoder":
        payload = {
            "gcs_uri": cfg.video_gcs_uri,
            "passes": cfg.video_passes,
            "return_inline": False,
        }
    else:
        raise ValueError(f"Unknown function_id: {function_id}")

    payload.update(metadata)
    return payload


def generate_invocations(cfg: Config) -> List[Invocation]:
    trace_hour_str = format_dt(cfg.trace_hour)
    invocations: List[Invocation] = []

    for function_id, count in FUNCTION_INVOCATIONS_PER_HOUR.items():
        offset = MINUTE_OFFSETS.get(function_id, 0)
        minutes = evenly_spaced_minutes(count, offset)
        for idx, minute in enumerate(minutes, start=1):
            event_id = f"{cfg.experiment_id}:{trace_hour_str}:{function_id}:{idx:02d}"
            seconds_into_hour = minute * 60
            jitter = deterministic_jitter_seconds(event_id, cfg.jitter_s, seconds_into_hour)
            scheduled_time = cfg.trace_hour + timedelta(minutes=minute, seconds=jitter)
            payload = build_payload(cfg, function_id, event_id, trace_hour_str)
            invocations.append(
                Invocation(
                    function_id=function_id,
                    index=idx,
                    event_id=event_id,
                    scheduled_time=scheduled_time,
                    payload=payload,
                )
            )

    invocations.sort(key=lambda inv: inv.scheduled_time)
    return invocations


def sleep_until(target_time: datetime) -> None:
    while True:
        remaining = (target_time - now_utc()).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 30))


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def log_record(record: Dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=True))


def extract_dispatch_latency_ms(response_json: Dict[str, Any]) -> Optional[float]:
    for key in ("end_to_end_latency_ms", "execution_latency_ms", "execution_time_ms", "latency_ms"):
        val = parse_optional_float(response_json.get(key))
        if val is not None:
            return val
    return None


def run_scenario_a_b(cfg: Config, invocations: List[Invocation]) -> None:
    headers = build_headers(cfg.auth_bearer_token, cfg.extra_headers)
    trace_hour_str = format_dt(cfg.trace_hour)

    for inv in invocations:
        sleep_until(inv.scheduled_time)
        region = select_region(cfg, cfg.trace_hour.hour)
        url = lookup_function_url(cfg, inv.function_id, region)
        sent_time = now_utc()
        payload = dict(inv.payload)
        payload["dispatch_sent_time_utc"] = format_dt(sent_time)

        result = post_json(
            url=url,
            payload=payload,
            headers=headers,
            timeout_s=cfg.request_timeout_s,
            verify_tls=cfg.verify_tls,
            dry_run=cfg.dry_run,
        )

        end_to_end_latency_ms = None if cfg.dry_run else result.get("latency_ms")
        end_to_end_latency_source = "direct_invoke" if end_to_end_latency_ms is not None else "not_measured"
        end_to_end_latency_note = "dry_run" if cfg.dry_run else None

        log_record(
            {
                "ts_utc": format_dt(now_utc()),
                "experiment_id": cfg.experiment_id,
                "scenario": cfg.scenario,
                "event_id": inv.event_id,
                "trace_hour": trace_hour_str,
                "function_id": inv.function_id,
                "invocation_index": inv.index,
                "scheduled_time_utc": format_dt(inv.scheduled_time),
                "policy_region": region,
                "policy_region_source": "fixed_region" if cfg.scenario == "A" else cfg.hourly_region_map_source,
                "target_region": region,
                "target_url": url,
                "sent_time_utc": format_dt(sent_time),
                "end_to_end_latency_ms": end_to_end_latency_ms,
                "end_to_end_latency_source": end_to_end_latency_source,
                "end_to_end_latency_note": end_to_end_latency_note,
                "invoke": result,
            }
        )


def run_scenario_c(cfg: Config, invocations: List[Invocation]) -> None:
    if not cfg.dispatcher_url:
        raise ValueError("DISPATCHER_URL is required for scenario C.")

    dispatch_headers = build_headers(
        cfg.dispatcher_auth_bearer_token or cfg.auth_bearer_token,
        cfg.dispatcher_extra_headers if cfg.dispatcher_extra_headers is not None else cfg.extra_headers,
    )
    trace_hour_str = format_dt(cfg.trace_hour)
    deadline = cfg.trace_hour + timedelta(hours=1)
    deadline_str = format_dt(deadline)

    for inv in invocations:
        sleep_until(inv.scheduled_time)
        function_payload = dict(inv.payload)
        dispatch_payload = {
            "function_name": inv.function_id,
            "deadline": deadline_str,
            "experiment_id": cfg.experiment_id,
            "scenario": cfg.scenario,
            "event_id": inv.event_id,
            "trace_hour": trace_hour_str,
            "invocation_index": inv.index,
            "function_payload": function_payload,
        }
        dispatch_sent = now_utc()
        function_payload["dispatch_sent_time_utc"] = format_dt(dispatch_sent)
        dispatch_result = post_json(
            url=cfg.dispatcher_url,
            payload=dispatch_payload,
            headers=dispatch_headers,
            timeout_s=cfg.request_timeout_s,
            verify_tls=cfg.verify_tls,
            dry_run=cfg.dry_run,
        )
        response_json = dispatch_result.get("response_json") or {}

        target_time = parse_datetime(response_json.get("target_time") or response_json.get("datetime"))
        target_region = response_json.get("target_region") or response_json.get("region")
        target_url = response_json.get("url")
        delay_flag = parse_optional_bool(response_json.get("delay"))
        lookup_error = None
        if not target_url and target_region and cfg.function_urls.get(inv.function_id):
            try:
                target_url = lookup_function_url(cfg, inv.function_id, str(target_region))
            except Exception as exc:
                lookup_error = str(exc)

        time_shifted = None
        if delay_flag is not None:
            time_shifted = delay_flag
        elif target_time:
            time_shifted = target_time > (dispatch_sent + timedelta(seconds=1))

        end_to_end_latency_ms = None
        end_to_end_latency_source = "not_available"
        end_to_end_latency_note = None

        if cfg.dry_run:
            end_to_end_latency_note = "dry_run"
        else:
            end_to_end_latency_ms = extract_dispatch_latency_ms(response_json)
            if end_to_end_latency_ms is not None:
                end_to_end_latency_source = "dispatcher_response"
            elif time_shifted:
                end_to_end_latency_source = "scheduled"
                end_to_end_latency_note = "time_shifted"
            else:
                end_to_end_latency_note = "not_returned_by_dispatcher"


        log_record(
            {
                "ts_utc": format_dt(now_utc()),
                "experiment_id": cfg.experiment_id,
                "scenario": cfg.scenario,
                "event_id": inv.event_id,
                "trace_hour": trace_hour_str,
                "function_id": inv.function_id,
                "invocation_index": inv.index,
                "scheduled_time_utc": format_dt(inv.scheduled_time),
                "deadline_utc": deadline_str,
                "dispatcher_url": cfg.dispatcher_url,
                "dispatcher_sent_time_utc": format_dt(dispatch_sent),
                "dispatch": dispatch_result,
                "target_region": target_region,
                "target_url": target_url,
                "target_time_utc": format_dt(target_time) if target_time else None,
                "dispatcher_delay": delay_flag,
                "time_shifted": time_shifted,
                "end_to_end_latency_ms": end_to_end_latency_ms,
                "end_to_end_latency_source": end_to_end_latency_source,
                "end_to_end_latency_note": end_to_end_latency_note,
                "lookup_error": lookup_error,
                "invoke": None,
            }
        )


def validate_config(cfg: Config) -> None:
    if cfg.scenario in ("A", "B") and not cfg.function_urls:
        raise ValueError("FUNCTION_URLS_JSON is required for scenario A/B.")

    if cfg.scenario == "B" and not cfg.hourly_region_map:
        raise ValueError(
            "Scenario B requires a carbon forecast (CARBON_FORECAST_*) "
            "or an hourly region map (HOURLY_REGION_MAP_*)."
        )

    for fn in FUNCTION_INVOCATIONS_PER_HOUR.keys():
        if fn not in cfg.function_urls and cfg.scenario in ("A", "B"):
            raise ValueError(f"Missing function URL mapping for {fn}.")


def main() -> None:
    print("BOOT SCENARIO=", os.getenv("SCENARIO"), flush=True)
    cfg = load_config()
    validate_config(cfg)

    invocations = generate_invocations(cfg)

    log_record(
        {
            "ts_utc": format_dt(now_utc()),
            "message": "loadgen_start",
            "experiment_id": cfg.experiment_id,
            "scenario": cfg.scenario,
            "trace_hour": format_dt(cfg.trace_hour),
            "invocations": len(invocations),
            "dry_run": cfg.dry_run,
        }
    )

    if cfg.scenario in ("A", "B"):
        run_scenario_a_b(cfg, invocations)
    else:
        run_scenario_c(cfg, invocations)

    log_record(
        {
            "ts_utc": format_dt(now_utc()),
            "message": "loadgen_complete",
            "experiment_id": cfg.experiment_id,
            "scenario": cfg.scenario,
            "trace_hour": format_dt(cfg.trace_hour),
            "invocations": len(invocations),
        }
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        raise
