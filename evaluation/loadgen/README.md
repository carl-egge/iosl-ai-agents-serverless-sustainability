# Loadgen Job (Cloud Run)

This directory contains a Cloud Run Job implementation that follows
`evaluation/EVALUATION.md` and drives the hourly workload trace used for
scenario A/B/C experiments.

## What it does

- Generates the fixed hourly invocation mix: 20 health, 3 crypto, 2 image, 1 video.
- Uses fixed minute slots and deterministic jitter derived from each event ID.
- Supports scenario A (fixed region), B (hourly lowest-carbon), C (AI dispatcher).
- Logs one JSON line per invocation with `experiment_id`, `scenario`, `event_id`,
  and `trace_hour` so you can correlate runner, dispatcher, and function logs.

## Files

- `loadgen_job.py`: Cloud Run Job entrypoint.
- `requirements.txt`: Python dependencies.
- `env.example.yaml`: example env vars for `gcloud run jobs deploy`.
- `function_urls.example.json`: sample mapping of function -> region -> URL.
- `hourly_region_map.example.json`: sample hourly region mapping for scenario B.

## Payloads (aligned to the protocol)

- Health check: `{"check":"ping"}`
- Image converter: `{"gcs_uri":"<IMAGE_URI>","format":"WEBP","quality":85}`
- Crypto key gen: `{"bits":4096}`
- Video transcoder: `{"gcs_uri":"<VIDEO_URI>","passes":2}`

The runner injects `experiment_id`, `scenario`, `event_id`, and `trace_hour` into
every payload for correlation.

Function IDs used in the runner and dispatcher payloads:

- `api_health_check`
- `crypto_key_gen`
- `image_format_converter`
- `video_transcoder`

## Configuration

Required for all scenarios:

- `EXPERIMENT_ID`: Stable ID for the scenario run.
- `SCENARIO`: `A`, `B`, or `C`.
- `IMAGE_GCS_URI`: GCS URI for the image input.
- `VIDEO_GCS_URI`: GCS URI for the video-like input.

Function URL mapping (required for scenario A/B, optional for scenario C logging):

- `FUNCTION_URLS_JSON`: JSON mapping of function -> region -> URL.
- or `FUNCTION_URLS_PATH`: Path to a JSON file (for example `/app/function_urls.json`).

Scenario A (fixed region):

- `FIXED_REGION`: Region name used for all functions (for example `us-east1`).

Scenario B (hourly lowest-carbon):

- `HOURLY_REGION_MAP_JSON`: JSON mapping of hour (0-23) to region.
- or `HOURLY_REGION_MAP_PATH`: Path to the mapping file.

Scenario C (AI dispatcher):

- `DISPATCHER_URL`: Dispatcher endpoint.

Optional:

- `TRACE_HOUR_UTC`: ISO timestamp to override the trace hour.
- `JITTER_S`: Max deterministic jitter in seconds (default `15`).
- `TIMEOUT_S`: HTTP timeout in seconds (default `120`).
- `VERIFY_TLS`: `true` or `false` (default `true`).
- `DRY_RUN`: `true` to validate scheduling without HTTP calls.
- `AUTH_BEARER_TOKEN`: Bearer token for authenticated services.
- `EXTRA_HEADERS_JSON`: Extra headers as JSON (for example `{"X-Experiment":"A"}`).
- `DISPATCHER_AUTH_BEARER_TOKEN`: Optional dispatcher-specific token override.
- `DISPATCHER_EXTRA_HEADERS_JSON`: Optional dispatcher-specific headers.

## Deploy (no Dockerfile required)

1) Copy `env.example.yaml` to `env.yaml` and fill in real values.

2) Deploy the job:

```bash
gcloud run jobs deploy loadgen-job \
  --source evaluation/loadgen \
  --region us-east1 \
  --tasks 1 \
  --max-retries 0 \
  --task-timeout 3600 \
  --command python \
  --args loadgen_job.py \
  --env-vars-file evaluation/loadgen/env.yaml
```

3) Test a manual run:

```bash
gcloud run jobs execute loadgen-job --region us-east1 --wait
```

## Hourly scheduling

Use Cloud Scheduler to trigger the job each hour:

```bash
gcloud scheduler jobs create http loadgen-hourly \
  --schedule "0 * * * *" \
  --uri "https://REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT_ID/jobs/loadgen-job:run" \
  --http-method POST \
  --oauth-service-account-email SCHEDULER_SA@PROJECT_ID.iam.gserviceaccount.com
```

## Notes for scenario C

The load generator sends the full function payload to the dispatcher and does
not invoke the target functions directly. The dispatcher is expected to forward
the payload to the chosen region/time.
