# GCP Metrics Fetcher

Fetches raw Cloud Run metrics from GCP Cloud Monitoring API.

For methodology and evaluation context, see [../EVALUATION.md](../EVALUATION.md).

## Prerequisites

- GCP authentication: `gcloud auth application-default login`
- Cloud Monitoring API enabled

## Usage

### Single Function (CLI)

```bash
python fetch_gcp_metrics.py \
  --project-id your-project-id \
  --url https://your-service.run.app
```

Optional: `--start` and `--end` for time window (defaults to last 30 days).

### Batch Mode (Config File)

```bash
python fetch_gcp_metrics.py --config experiment_config.json
```

## Output

Files saved to `evaluation/results/{project_id}/`:
- `gcp_metrics_{project_id}_{name}_{timestamp}.json`

## Metrics Collected

- `request_count` - total requests
- `request_latencies_ms` - mean, p50, p95, p99
- `cpu_utilization` - mean, p95
- `memory_utilization` - mean, p95
- `billable_instance_time_s` - total billable seconds
- `network` - received_gb, sent_gb
