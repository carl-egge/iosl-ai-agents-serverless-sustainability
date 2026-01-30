#!/usr/bin/env python3
"""
GCP Cloud Run Metrics Fetcher for GPS-UP Evaluation

Fetches actual resource metrics from GCP Cloud Monitoring API
for calculating energy consumption, costs, and emissions.

Usage:
  # Using config file
  python fetch_gcp_metrics.py --config experiment_config.json

  # Using CLI arguments
  python fetch_gcp_metrics.py \
    --project-id project-123 \
    --url https://service-name-xyz.run.app \
    --start "2024-01-10T00:00:00Z" \
    --end "2024-01-10T23:59:59Z"
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from google.cloud import monitoring_v3
from google.cloud import run_v2


# Load environment variables
load_dotenv()


# -----------------------------------------------------------------------------
# GCP Client Initialization
# -----------------------------------------------------------------------------

def init_monitoring_client(project_id: str) -> monitoring_v3.MetricServiceClient:
    """Initialize GCP Cloud Monitoring client with authentication."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    return monitoring_v3.MetricServiceClient()


def init_run_client(project_id: str) -> run_v2.ServicesClient:
    """Initialize GCP Cloud Run client for service info lookup."""
    os.environ.setdefault("GOOGLE_CLOUD_PROJECT", project_id)
    return run_v2.ServicesClient()


# -----------------------------------------------------------------------------
# URL Parsing and Service Discovery
# -----------------------------------------------------------------------------

def extract_service_info_from_url(url: str, project_id: str) -> Dict[str, str]:
    """
    Extract service name from Cloud Run URL and query GCP for region.

    Args:
        url: Cloud Run URL (e.g., https://crypto-key-gen-abc123.run.app)
        project_id: GCP project ID

    Returns:
        {
            'service_name': str,  # e.g., 'crypto-key-gen'
            'region': str          # e.g., 'us-east1'
        }
    """
    # Parse URL to extract hostname
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise ValueError(f"Invalid URL: {url}")

    # Cloud Run URLs follow pattern: https://<service>-<hash>-<region-abbreviation>.a.run.app
    # or: https://<service>-<hash>.run.app
    # We need to query GCP to find the service

    # Extract potential service name (everything before first hyphen followed by hash)
    # This is a heuristic - we'll verify by listing services
    parts = hostname.split('-')
    if len(parts) < 2:
        raise ValueError(f"Cannot extract service name from URL: {url}")

    # Try to find service by listing all services in project
    run_client = init_run_client(project_id)

    # Regions used in this project (from function_metadata.json allowed_regions)
    locations = [
        "us-east1",
        "us-central1",
        "europe-west1",
        "europe-north2",
        "northamerica-northeast1"
    ]

    for location in locations:
        try:
            parent = f"projects/{project_id}/locations/{location}"
            services = run_client.list_services(parent=parent)

            for service in services:
                # Get service URL
                if hasattr(service, 'uri') and service.uri:
                    service_hostname = urlparse(service.uri).hostname
                    if service_hostname == hostname:
                        # Extract service name from resource name
                        # Format: projects/{project}/locations/{location}/services/{service}
                        service_name = service.name.split('/')[-1]
                        return {
                            'service_name': service_name,
                            'region': location
                        }
        except Exception:
            # Location might not exist or no permission
            continue

    raise ValueError(f"Could not find Cloud Run service for URL: {url}")


def extract_service_name_from_url(url: str) -> str:
    """
    Extract service name from Cloud Run URL without GCP API calls.

    Cloud Run URLs follow patterns:
    - https://<service>-<hash>-<region>.a.run.app
    - https://<service>-<hash>.run.app

    This is a heuristic based on URL structure. Use when region is already known.

    Args:
        url: Cloud Run URL

    Returns:
        Service name (e.g., "dispatcher", "crypto-key-gen")
    """
    parsed = urlparse(url)
    hostname = parsed.hostname

    if not hostname:
        raise ValueError(f"Invalid URL: {url}")

    # Remove the .a.run.app or .run.app suffix
    # Hostname: <service>-<hash>-<region>.a.run.app or <service>-<hash>.run.app
    parts = hostname.split('.')
    service_part = parts[0]  # e.g., "dispatcher-rtwa6zd2ma-ue"

    # Split by hyphen and try to identify service name
    # Pattern: service-name-hash-regioncode
    # The hash is typically 10-12 random chars, regioncode is 2-3 chars
    segments = service_part.split('-')

    if len(segments) >= 2:
        # Try to find where the hash starts (looking for typical hash pattern)
        # Hashes are lowercase alphanumeric, typically 10+ chars
        for i in range(1, len(segments)):
            # If this segment looks like a hash (long alphanumeric), service name is everything before
            if len(segments[i]) >= 8 and segments[i].isalnum():
                return '-'.join(segments[:i])

        # Fallback: assume last 1-2 segments are hash/region
        if len(segments) >= 3:
            return '-'.join(segments[:-2])
        else:
            return segments[0]

    return service_part


# -----------------------------------------------------------------------------
# Metric Fetching Functions
# -----------------------------------------------------------------------------

def fetch_request_latencies(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, float]:
    """
    Fetch request latency distribution.

    Returns:
        {
            'p50': float (ms),
            'p95': float (ms),
            'p99': float (ms),
            'mean': float (ms)
        }
    """
    metric_type = "run.googleapis.com/request_latencies"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    # Aggregate distribution values
    all_values = []
    for result in results:
        for point in result.points:
            if point.value.distribution_value:
                dist = point.value.distribution_value
                # Extract percentiles from distribution
                # GCP provides distribution as buckets, we need to calculate percentiles
                # For simplicity, we'll use mean and approximate percentiles
                if dist.mean:
                    all_values.append(dist.mean)

    if not all_values:
        return {'p50': None, 'p95': None, 'p99': None, 'mean': None}

    all_values.sort()
    n = len(all_values)

    def percentile(values, p):
        if not values:
            return None
        k = (len(values) - 1) * p / 100.0
        f = int(k)
        c = min(f + 1, len(values) - 1)
        if f == c:
            return values[f]
        return values[f] * (c - k) + values[c] * (k - f)

    return {
        'p50': percentile(all_values, 50),
        'p95': percentile(all_values, 95),
        'p99': percentile(all_values, 99),
        'mean': sum(all_values) / len(all_values) if all_values else None
    }


def fetch_cpu_utilization(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, float]:
    """
    Fetch CPU utilization distribution.

    Returns: {'mean': float, 'p95': float}
    """
    metric_type = "run.googleapis.com/container/cpu/utilizations"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    values = []
    for result in results:
        for point in result.points:
            if point.value.distribution_value:
                if point.value.distribution_value.mean:
                    values.append(point.value.distribution_value.mean)

    if not values:
        return {'mean': None, 'p95': None}

    values.sort()

    def percentile(vals, p):
        if not vals:
            return None
        k = (len(vals) - 1) * p / 100.0
        f = int(k)
        c = min(f + 1, len(vals) - 1)
        if f == c:
            return vals[f]
        return vals[f] * (c - k) + vals[c] * (k - f)

    return {
        'mean': sum(values) / len(values),
        'p95': percentile(values, 95)
    }


def fetch_memory_utilization(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, float]:
    """
    Fetch memory utilization distribution.

    Returns: {'mean': float, 'p95': float}
    """
    metric_type = "run.googleapis.com/container/memory/utilizations"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
        }
    )

    values = []
    for result in results:
        for point in result.points:
            if point.value.distribution_value:
                if point.value.distribution_value.mean:
                    values.append(point.value.distribution_value.mean)

    if not values:
        return {'mean': None, 'p95': None}

    values.sort()

    def percentile(vals, p):
        if not vals:
            return None
        k = (len(vals) - 1) * p / 100.0
        f = int(k)
        c = min(f + 1, len(vals) - 1)
        if f == c:
            return vals[f]
        return vals[f] * (c - k) + vals[c] * (k - f)

    return {
        'mean': sum(values) / len(values),
        'p95': percentile(values, 95)
    }


def fetch_billable_time(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> float:
    """
    Fetch total billable instance time.

    Returns: total billable seconds
    """
    metric_type = "run.googleapis.com/container/billable_instance_time"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
        }
    )

    total = 0.0
    for result in results:
        for point in result.points:
            if point.value.double_value:
                total += point.value.double_value

    return total


def fetch_network_bytes(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, int]:
    """
    Fetch network ingress and egress bytes.

    Returns: {'received_bytes': int, 'sent_bytes': int}
    """
    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    # Fetch received bytes
    received_type = "run.googleapis.com/container/network/received_bytes_count"
    received_results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{received_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
        }
    )

    received_total = 0
    for result in received_results:
        for point in result.points:
            if point.value.int64_value:
                received_total += point.value.int64_value

    # Fetch sent bytes
    sent_type = "run.googleapis.com/container/network/sent_bytes_count"
    sent_results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{sent_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
        }
    )

    sent_total = 0
    for result in sent_results:
        for point in result.points:
            if point.value.int64_value:
                sent_total += point.value.int64_value

    return {
        'received_bytes': received_total,
        'sent_bytes': sent_total
    }


def fetch_request_count(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> int:
    """
    Fetch total request count.

    Returns: total request count
    """
    metric_type = "run.googleapis.com/request_count"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
        }
    )

    total = 0
    for result in results:
        for point in result.points:
            if point.value.int64_value:
                total += point.value.int64_value

    return total


def fetch_request_count_hourly(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict[str, int]:
    """
    Fetch request counts broken down by hour.

    Uses GCP Cloud Monitoring aggregation with 1-hour alignment period.
    Only returns hours with at least 1 request.

    Returns:
        Dict mapping UTC hour (ISO format) to request count.
        Example: {"2026-01-29T13:00:00Z": 5, "2026-01-29T14:00:00Z": 3}
    """
    metric_type = "run.googleapis.com/request_count"

    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": int(end_time.timestamp())},
            "start_time": {"seconds": int(start_time.timestamp())},
        }
    )

    # Aggregate by hour
    aggregation = monitoring_v3.Aggregation(
        {
            "alignment_period": {"seconds": 3600},  # 1 hour
            "per_series_aligner": monitoring_v3.Aggregation.Aligner.ALIGN_SUM,
        }
    )

    results = client.list_time_series(
        request={
            "name": f"projects/{project_id}",
            "filter": f'metric.type="{metric_type}" AND resource.labels.service_name="{service_name}" AND resource.labels.location="{region}"',
            "interval": interval,
            "aggregation": aggregation,
        }
    )

    hourly_counts = {}
    for result in results:
        for point in result.points:
            if point.value.int64_value and point.value.int64_value > 0:
                # Convert end_time to UTC ISO format (truncated to hour)
                # GCP returns end_time of the interval
                ts = datetime.fromtimestamp(
                    point.interval.end_time.seconds,
                    tz=timezone.utc
                )
                # Truncate to hour start
                hour_start = ts.replace(minute=0, second=0, microsecond=0)
                hour_key = hour_start.strftime('%Y-%m-%dT%H:%M:%SZ')

                # Accumulate (in case multiple series)
                hourly_counts[hour_key] = hourly_counts.get(hour_key, 0) + point.value.int64_value

    return hourly_counts


# -----------------------------------------------------------------------------
# Carbon Intensity Functions
# -----------------------------------------------------------------------------

def load_carbon_forecast(experiment_date: datetime) -> Optional[Dict[str, Dict[str, int]]]:
    """
    Load carbon forecast from results/carbon_forecasts/ matching the experiment date.

    Args:
        experiment_date: Date of the experiment (used to find matching forecast file)

    Returns:
        Dict mapping region to {hour_iso: intensity} or None if no forecast found.
        Example: {"us-east1": {"2026-01-29T13:00:00Z": 632, ...}, ...}
    """
    import glob

    # Construct path to carbon_forecasts directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    forecasts_dir = os.path.join(project_root, 'evaluation', 'results', 'carbon_forecasts')

    if not os.path.exists(forecasts_dir):
        print(f"  Warning: Carbon forecasts directory not found: {forecasts_dir}")
        return None

    # List all forecast files
    forecast_files = glob.glob(os.path.join(forecasts_dir, 'carbon_forecasts_*.json'))

    if not forecast_files:
        print(f"  Warning: No carbon forecast files found in {forecasts_dir}")
        return None

    # Find file matching experiment date (YYYYMMDD)
    target_date = experiment_date.strftime('%Y%m%d')
    matching_file = None

    for filepath in forecast_files:
        filename = os.path.basename(filepath)
        # Parse date from filename: carbon_forecasts_YYYYMMDD_HHMMSS.json
        try:
            parts = filename.replace('.json', '').split('_')
            file_date = parts[2]  # YYYYMMDD
            if file_date == target_date:
                matching_file = filepath
                break
        except (IndexError, ValueError):
            continue

    if not matching_file:
        print(f"  Warning: No carbon forecast file found for date {target_date}")
        return None

    print(f"  Loading carbon forecast from: {os.path.basename(matching_file)}")

    # Load and parse forecast
    with open(matching_file, 'r') as f:
        data = json.load(f)

    # Build lookup: {region: {hour_iso: intensity}}
    result = {}
    regions = data.get('regions', {})

    for region_name, region_data in regions.items():
        forecast = region_data.get('forecast', [])
        hour_lookup = {}

        for entry in forecast:
            intensity = entry.get('carbonIntensity')
            dt_str = entry.get('datetime', '')

            if intensity is not None and dt_str:
                # Normalize datetime format (remove milliseconds)
                # Input: "2026-01-29T18:00:00.000Z" -> "2026-01-29T18:00:00Z"
                normalized = dt_str.replace('.000Z', 'Z')
                hour_lookup[normalized] = intensity

        if hour_lookup:
            result[region_name] = hour_lookup

    return result if result else None


def calculate_weighted_carbon_intensity(
    hourly_requests: Dict[str, int],
    region: str,
    carbon_forecast: Optional[Dict[str, Dict[str, int]]],
    fallback_intensity: float = 400.0
) -> Dict:
    """
    Calculate weighted average carbon intensity based on execution hours.

    Formula: weighted_avg = Σ(requests_hour × intensity_hour) / total_requests

    Args:
        hourly_requests: Dict from fetch_request_count_hourly()
        region: GCP region (e.g., "us-east1")
        carbon_forecast: Dict from load_carbon_forecast() (can be None)
        fallback_intensity: Default intensity when no forecast available (gCO2/kWh)

    Returns:
        {
            "weighted_average_gco2_kwh": float,
            "total_requests": int,
            "hours_matched": int,
            "hours_with_fallback": int,
            "source": "forecast" | "fallback",
            "fallback_value": float
        }
    """
    if not hourly_requests:
        return {
            "weighted_average_gco2_kwh": fallback_intensity,
            "total_requests": 0,
            "hours_matched": 0,
            "hours_with_fallback": 0,
            "source": "fallback",
            "fallback_value": fallback_intensity,
            "note": "No hourly request data available"
        }

    # Check if we have forecast data for this region
    region_forecast = None
    if carbon_forecast and region in carbon_forecast:
        region_forecast = carbon_forecast[region]

    weighted_sum = 0.0
    total_requests = 0
    hours_matched = 0
    hours_with_fallback = 0

    for hour_key, request_count in hourly_requests.items():
        intensity = None

        # Try to get intensity from forecast
        if region_forecast and hour_key in region_forecast:
            intensity = region_forecast[hour_key]
            hours_matched += 1
        else:
            intensity = fallback_intensity
            hours_with_fallback += 1

        weighted_sum += request_count * intensity
        total_requests += request_count

    weighted_avg = weighted_sum / total_requests if total_requests > 0 else fallback_intensity

    # Determine source
    if hours_matched > 0 and hours_with_fallback == 0:
        source = "forecast"
    elif hours_matched > 0:
        source = "mixed"
    else:
        source = "fallback"

    return {
        "weighted_average_gco2_kwh": round(weighted_avg, 2),
        "total_requests": total_requests,
        "hours_matched": hours_matched,
        "hours_with_fallback": hours_with_fallback,
        "source": source,
        "fallback_value": fallback_intensity
    }


# -----------------------------------------------------------------------------
# Aggregation
# -----------------------------------------------------------------------------

def fetch_all_metrics(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
    service_name: str,
    region: str,
    start_time: datetime,
    end_time: datetime
) -> Dict:
    """
    Fetch all metrics for a single function.

    Returns complete metrics dict.
    """
    print(f"  Fetching metrics for {service_name} in {region}...")

    # Fetch each metric with error handling
    try:
        request_latencies = fetch_request_latencies(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch request_latencies: {e}")
        request_latencies = {'p50_ms': None, 'p95_ms': None, 'p99_ms': None, 'mean_ms': None}

    try:
        cpu_util = fetch_cpu_utilization(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch cpu_utilization: {e}")
        cpu_util = {'mean': None, 'p95': None}

    try:
        memory_util = fetch_memory_utilization(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch memory_utilization: {e}")
        memory_util = {'mean': None, 'p95': None}

    try:
        billable_time = fetch_billable_time(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch billable_time: {e}")
        billable_time = None

    try:
        network = fetch_network_bytes(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch network_bytes: {e}")
        network = {'received_bytes': 0, 'sent_bytes': 0}

    try:
        request_count = fetch_request_count(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch request_count: {e}")
        request_count = None

    try:
        hourly_request_counts = fetch_request_count_hourly(client, project_id, service_name, region, start_time, end_time)
    except Exception as e:
        print(f"    Warning: Could not fetch hourly_request_counts: {e}")
        hourly_request_counts = {}

    return {
        'request_count': request_count,
        'hourly_request_counts': hourly_request_counts,
        'request_latencies_ms': request_latencies,
        'cpu_utilization': cpu_util,
        'memory_utilization': memory_util,
        'billable_instance_time_s': billable_time,
        'network': {
            'received_bytes_total': network['received_bytes'],
            'sent_bytes_total': network['sent_bytes'],
            'received_gb': network['received_bytes'] / (1024 ** 3),
            'sent_gb': network['sent_bytes'] / (1024 ** 3)
        }
    }


# -----------------------------------------------------------------------------
# Configuration & CLI
# -----------------------------------------------------------------------------

def load_config_file(config_path: str) -> Dict:
    """Load experiment configuration from JSON file."""
    with open(config_path, 'r') as f:
        return json.load(f)


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Fetch GCP Cloud Run metrics for GPS-UP evaluation'
    )

    # Config file mode
    parser.add_argument(
        '--config',
        help='Path to experiment configuration JSON file'
    )

    # CLI mode
    parser.add_argument(
        '--project-id',
        help='GCP project ID'
    )
    parser.add_argument(
        '--url',
        help='Cloud Run function URL'
    )
    parser.add_argument(
        '--start',
        help='Start time (ISO 8601 format). If omitted, defaults to 30 days ago.'
    )
    parser.add_argument(
        '--end',
        help='End time (ISO 8601 format). If omitted, defaults to now.'
    )

    # Output
    parser.add_argument(
        '--output',
        default=None,
        help='Output JSON file path (default: auto-generated in evaluation/results/{project_id}/)'
    )

    return parser.parse_args()


# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

def main():
    """Main execution logic."""
    args = parse_args()

    # Determine mode
    if args.config:
        # Config file mode
        print(f"Loading configuration from {args.config}...")
        config = load_config_file(args.config)

        experiment_name = config.get('experiment_name', 'unknown')
        project_id = config['project_id']
        description = config.get('description', '')
        time_window = config['time_window']
        start_time = datetime.fromisoformat(time_window['start'].replace('Z', '+00:00'))
        end_time = datetime.fromisoformat(time_window['end'].replace('Z', '+00:00'))

        print(f"Experiment: {experiment_name}")
        print(f"Description: {description}")
        print(f"Project: {project_id}")
        print(f"Time window: {time_window['start']} to {time_window['end']}")

        # Initialize client
        client = init_monitoring_client(project_id)

        # Load carbon forecast once for all functions
        print("\nLoading carbon forecast...")
        carbon_forecast = load_carbon_forecast(start_time)
        if carbon_forecast:
            print(f"  Loaded forecast for {len(carbon_forecast)} region(s)")
        else:
            print("  No matching carbon forecast found - will use fallback value (400)")

        # Process all functions
        output = {
            'experiment_name': experiment_name,
            'project_id': project_id,
            'description': description,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'time_window': time_window,
            'functions': {}
        }

        for func in config['functions']:
            func_label = func['label']
            func_url = func['url']

            try:
                print(f"\nProcessing function: {func_label}")
                print(f"  URL: {func_url}")

                # Check if region is provided in config (faster) or needs URL lookup (slower)
                if 'region' in func:
                    region = func['region']
                    service_name = extract_service_name_from_url(func_url)
                    print(f"  Using config: service={service_name}, region={region}")
                else:
                    # Fall back to slow URL lookup via GCP API
                    service_info = extract_service_info_from_url(func_url, project_id)
                    service_name = service_info['service_name']
                    region = service_info['region']
                    print(f"  Detected via GCP: service={service_name}, region={region}")

                # Fetch metrics
                metrics = fetch_all_metrics(
                    client, project_id, service_name, region,
                    start_time, end_time
                )

                # Calculate weighted carbon intensity
                hourly_counts = metrics.get('hourly_request_counts', {})
                carbon_info = calculate_weighted_carbon_intensity(
                    hourly_requests=hourly_counts,
                    region=region,
                    carbon_forecast=carbon_forecast,
                    fallback_intensity=400.0
                )
                metrics['carbon_intensity'] = carbon_info

                if hourly_counts:
                    print(f"  Carbon intensity: {carbon_info['weighted_average_gco2_kwh']} gCO2/kWh ({carbon_info['source']})")

                output['functions'][func_label] = {
                    'service_name': service_name,
                    'region': region,
                    'url': func_url,
                    'gcp_metrics': metrics
                }

                print(f"  ✓ Successfully fetched metrics")

            except Exception as e:
                print(f"  ✗ Error fetching metrics: {e}")
                output['functions'][func_label] = {
                    'url': func_url,
                    'error': str(e)
                }

        # Generate output filename
        if args.output:
            output_path = args.output
        else:
            # Create evaluation/results/{project_id}/ directory if it doesn't exist
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(script_dir))
            results_dir = os.path.join(project_root, 'evaluation', 'results', project_id)
            os.makedirs(results_dir, exist_ok=True)

            # Generate filename: gcp_metrics_projectid_experimentname_timestamp.json
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f"gcp_metrics_{project_id}_{experiment_name}_{timestamp}.json"
            output_path = os.path.join(results_dir, filename)

    else:
        # CLI mode
        if not all([args.project_id, args.url]):
            print("Error: CLI mode requires --project-id and --url")
            sys.exit(1)

        print(f"Fetching metrics for single function...")
        print(f"  URL: {args.url}")
        print(f"  Project: {args.project_id}")

        # Handle optional start/end times
        if args.start:
            start_time = datetime.fromisoformat(args.start.replace('Z', '+00:00'))
        else:
            # Default to 30 days ago (GCP metrics retention period)
            start_time = datetime.now(timezone.utc) - timedelta(days=30)
            print(f"  No start time provided, using 30 days ago: {start_time.isoformat()}")

        if args.end:
            end_time = datetime.fromisoformat(args.end.replace('Z', '+00:00'))
        else:
            # Default to now
            end_time = datetime.now(timezone.utc)
            print(f"  No end time provided, using now: {end_time.isoformat()}")

        # Extract service info
        service_info = extract_service_info_from_url(args.url, args.project_id)
        service_name = service_info['service_name']
        region = service_info['region']

        print(f"  Detected: service={service_name}, region={region}")

        # Load carbon forecast
        print("\nLoading carbon forecast...")
        carbon_forecast = load_carbon_forecast(start_time)
        if carbon_forecast:
            print(f"  Loaded forecast for {len(carbon_forecast)} region(s)")
        else:
            print("  No matching carbon forecast found - will use fallback value (400)")

        # Fetch metrics
        client = init_monitoring_client(args.project_id)
        metrics = fetch_all_metrics(
            client, args.project_id, service_name, region,
            start_time, end_time
        )

        # Calculate weighted carbon intensity
        hourly_counts = metrics.get('hourly_request_counts', {})
        carbon_info = calculate_weighted_carbon_intensity(
            hourly_requests=hourly_counts,
            region=region,
            carbon_forecast=carbon_forecast,
            fallback_intensity=400.0
        )
        metrics['carbon_intensity'] = carbon_info

        if hourly_counts:
            print(f"  Carbon intensity: {carbon_info['weighted_average_gco2_kwh']} gCO2/kWh ({carbon_info['source']})")

        output = {
            'experiment_id': 'single_query',
            'project_id': args.project_id,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'time_window': {
                'start': args.start,
                'end': args.end
            },
            'function': {
                'service_name': service_name,
                'region': region,
                'url': args.url,
                'gcp_metrics': metrics
            }
        }

        # Generate output filename if not provided
        if args.output:
            output_path = args.output
        else:
            # Create evaluation/results/{project_id}/ directory if it doesn't exist
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(script_dir))
            results_dir = os.path.join(project_root, 'evaluation', 'results', args.project_id)
            os.makedirs(results_dir, exist_ok=True)

            # Generate filename: gcp_metrics_projectid_servicename_timestamp.json
            timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            filename = f"gcp_metrics_{args.project_id}_{service_name}_{timestamp}.json"
            output_path = os.path.join(results_dir, filename)

    # Write output
    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ Metrics saved to: {output_path}")


if __name__ == '__main__':
    main()
