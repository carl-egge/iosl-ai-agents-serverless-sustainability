# GCP Metrics Fetcher

A tool to fetch raw Cloud Run metrics from Google Cloud Platform (GCP) Cloud Monitoring API for GPS-UP evaluation experiments.

## Overview

This tool fetches real metrics from deployed Cloud Run functions over specified time windows. It supports comparing multiple deployment scenarios (e.g., fixed region vs. caller region vs. AI agent optimization) by collecting metrics for each scenario's functions.

**What it does:**
- Fetches 7 types of raw GCP Cloud Monitoring metrics for Cloud Run functions
- Automatically extracts service name and region from Cloud Run URLs
- Supports batch processing of multiple scenarios and functions
- Outputs structured JSON with raw metrics (no derived calculations)

**What it does NOT do:**
- Does NOT calculate energy consumption, carbon emissions, or GPS-UP scores
- Does NOT perform load testing (use loadgen for that)
- Does NOT measure client-side latency (use loadgen for that)

## Prerequisites

1. **GCP Project with Cloud Run services deployed**
2. **Authentication configured** - Use one of:
   - Application Default Credentials (ADC): `gcloud auth application-default login`
   - Service account key file (set in `.env`)

3. **Required GCP APIs enabled:**
   - Cloud Monitoring API
   - Cloud Run API

4. **Python 3.8+**

## Installation

No additional dependencies required - uses Python standard library and Google Cloud client libraries. All dependencies are managed through the project's `environment.yml`.

## Configuration

### Authentication

Create a `.env` file (optional - uses ADC by default):

```bash
# Optional: path to service account JSON key
# GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Optional: default GCP project ID
# GCP_PROJECT_ID=your-project-id
```

### Experiment Configuration

For batch fetching metrics from multiple functions in one project, create a configuration file (see `experiment_config.example.json`):

```json
{
  "experiment_name": "project-a-fixed-region",
  "project_id": "your-gcp-project-id",
  "description": "Project A - All functions deployed to us-central1 (fixed region strategy)",
  "functions": [
    {
      "url": "https://dispatcher-hash-uc.a.run.app",
      "label": "dispatcher"
    },
    {
      "url": "https://image-converter-hash-uc.a.run.app",
      "label": "image-converter"
    }
  ],
  "time_window": {
    "start": "2026-01-10T00:00:00Z",
    "end": "2026-01-10T23:59:59Z"
  }
}
```

**Note:** All functions must be in the same GCP project. To compare different projects (e.g., Project A vs. Project B vs. Project C), create separate config files and run the script multiple times.

## Usage

### Mode 1: Config File (Batch Processing)

Process multiple functions from a configuration file:

```bash
python fetch_metrics.py --config experiment_config.json
```

Output: `evaluation/data/gcp_metrics_{experiment_name}_{timestamp}.json`

Example: `evaluation/data/gcp_metrics_project-a-fixed-region_20260111_143022.json`

### Mode 2: CLI (Single Function Query)

Fetch metrics for a single Cloud Run function:

```bash
python fetch_metrics.py \
  --project-id your-gcp-project-id \
  --url https://your-service-hash-uc.a.run.app \
  --start "2026-01-10T00:00:00Z" \
  --end "2026-01-10T23:59:59Z"
```

Output: `evaluation/data/gcp_metrics_{service_name}_{timestamp}.json`

Example: `evaluation/data/gcp_metrics_dispatcher_20260111_143022.json`

**Note:** `--start` and `--end` are optional. If omitted, fetches last 30 days of data.

## Metrics Collected

### 1. Request Latencies (`run.googleapis.com/request_latencies`)
- **Type:** DISTRIBUTION
- **Description:** Actual execution time of function requests (excludes idle time)
- **Output Fields:**
  - `p50_ms`: Median latency
  - `p95_ms`: 95th percentile latency
  - `p99_ms`: 99th percentile latency
  - `mean_ms`: Average latency

### 2. CPU Utilization (`run.googleapis.com/container/cpu/utilizations`)
- **Type:** DISTRIBUTION
- **Description:** CPU utilization as a fraction (0.0 to 1.0)
- **Output Fields:**
  - `mean`: Average CPU utilization
  - `p95`: 95th percentile CPU utilization

### 3. Memory Utilization (`run.googleapis.com/container/memory/utilizations`)
- **Type:** DISTRIBUTION
- **Description:** Memory utilization as a fraction (0.0 to 1.0)
- **Output Fields:**
  - `mean`: Average memory utilization
  - `p95`: 95th percentile memory utilization

### 4. Billable Instance Time (`run.googleapis.com/container/billable_instance_time`)
- **Type:** DELTA
- **Description:** Total billable time in seconds (includes idle time)
- **Output Fields:**
  - `total_seconds`: Sum of billable time across all instances

### 5. Network Bytes
- **Received Bytes** (`run.googleapis.com/container/network/received_bytes_count`)
- **Sent Bytes** (`run.googleapis.com/container/network/sent_bytes_count`)
- **Type:** DELTA
- **Description:** Total bytes received/sent by the service
- **Output Fields:**
  - `total_bytes_received`: Total inbound network traffic
  - `total_bytes_sent`: Total outbound network traffic

### 6. Request Count (`run.googleapis.com/request_count`)
- **Type:** DELTA
- **Description:** Total number of requests received
- **Output Fields:**
  - `total_requests`: Count of all requests

## Output Format

### Config File Mode Output

```json
{
  "experiment_name": "project-a-fixed-region",
  "project_id": "your-gcp-project-id",
  "description": "Project A - All functions deployed to us-central1",
  "generated_at": "2026-01-11T14:30:22.123456+00:00",
  "time_window": {
    "start": "2026-01-10T00:00:00Z",
    "end": "2026-01-10T23:59:59Z"
  },
  "functions": {
    "dispatcher": {
      "service_name": "dispatcher",
      "region": "us-central1",
      "url": "https://dispatcher-hash-uc.a.run.app",
      "gcp_metrics": {
        "request_count": 10000,
        "request_latencies_ms": {
          "p50": 120.5,
          "p95": 350.2,
          "p99": 500.8,
          "mean": 150.3
        },
        "cpu_utilization": {
          "mean": 0.45,
          "p95": 0.78
        },
        "memory_utilization": {
          "mean": 0.32,
          "p95": 0.65
        },
        "billable_instance_time_s": 3600.5,
        "network": {
          "received_bytes_total": 1048576000,
          "sent_bytes_total": 524288000,
          "received_gb": 0.977,
          "sent_gb": 0.488
        }
      }
    },
    "image-converter": {
      "service_name": "image-converter",
      "region": "us-central1",
      "url": "https://image-converter-hash-uc.a.run.app",
      "gcp_metrics": { ... }
    }
  }
}
```

### CLI Mode Output

```json
{
  "experiment_id": "single_query",
  "generated_at": "2026-01-11T14:30:22.123456+00:00",
  "time_window": {
    "start": "2026-01-10T00:00:00Z",
    "end": "2026-01-10T23:59:59Z"
  },
  "function": {
    "service_name": "dispatcher",
    "region": "us-east1",
    "url": "https://dispatcher-hash-ue.a.run.app",
    "gcp_metrics": {
      "request_count": 5,
      "request_latencies_ms": {
        "p50": 2974.57,
        "p95": 4243.88,
        "p99": 4398.38,
        "mean": 2753.32
      },
      "cpu_utilization": {
        "mean": 0.0075,
        "p95": 0.0244
      },
      "memory_utilization": {
        "mean": 0.2234,
        "p95": 0.3095
      },
      "billable_instance_time_s": 120.5,
      "network": {
        "received_bytes_total": 1024,
        "sent_bytes_total": 2048,
        "received_gb": 0.000001,
        "sent_gb": 0.000002
      }
    }
  }
}
```

## Time Window Considerations

- **Metrics are aggregated** over the specified time window (not per individual execution)
- **Use ISO 8601 format** for timestamps: `YYYY-MM-DDTHH:MM:SSZ`
- **Choose appropriate windows** based on your experiment:
  - Short windows (1-2 hours) for focused load testing
  - Daily windows (24 hours) for comparing daily patterns
  - Multi-day windows for long-term trends

## Integration with GPS-UP Evaluation

This tool provides **raw metrics** that will be used in GPS-UP calculations:

- **Greenup (G):** Uses `cpu_utilization`, `billable_instance_time`, and carbon intensity data
- **Powerup (P):** Uses `cpu_utilization`, `memory_utilization`, and `billable_instance_time`
- **Speedup (S):** Uses `request_latencies` (p50, p95, p99)

The actual GPS-UP calculations will be implemented in separate tooling.

## Relationship to Loadgen

**Loadgen** (`evaluation/loadgen/`) and this tool serve different purposes:

| Tool | Purpose | What it measures | Where it runs |
|------|---------|------------------|---------------|
| **loadgen** | Generate load and measure end-to-end latency | Client-side latency (network + execution + network) | Local machine |
| **gcp_metrics** | Fetch actual GCP metrics | Server-side execution time, resource utilization | Queries GCP APIs |

**Use both together:**
1. Run loadgen to generate traffic to your Cloud Run functions
2. Use gcp_metrics to fetch actual resource usage and performance metrics from GCP
3. Compare scenarios using the collected metrics

## Troubleshooting

### Authentication Errors

```
Error: Could not automatically determine credentials
```

**Solution:** Run `gcloud auth application-default login` or set `GOOGLE_APPLICATION_CREDENTIALS` in `.env`

### Service Not Found

```
Error: Could not find service for URL
```

**Solution:**
- Verify the URL is correct and the service is deployed
- Ensure you have permissions to access the service
- Check that the project ID is correct

### No Metrics Data

```
Warning: No data returned for metric X
```

**Possible causes:**
- Function hasn't received traffic in the time window
- Time window is too narrow
- Metric type not supported for your service configuration

**Solution:**
- Verify the service received requests during the time window
- Expand the time window
- Check GCP Console Cloud Monitoring for metric availability

## References

- [GCP Cloud Run Metrics](https://cloud.google.com/run/docs/monitoring)
- [GCP Cloud Monitoring API](https://cloud.google.com/monitoring/api/v3)
- [GPS-UP Methodology](https://www.usenix.org/conference/hotcarbon23/presentation/wiesner)
