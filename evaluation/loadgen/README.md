# Loadgen Job (Cloud Run)

This directory contains a Cloud Run Job implementation that follows
`evaluation/EVALUATION.md` and drives the hourly workload trace used for
scenario A/B/C experiments.

## What it does

- Generates the fixed hourly invocation mix: 1 health, 1 crypto, 1 image, 1 video.
- Uses fixed minute slots and deterministic jitter derived from each event ID.
- Supports scenario A (fixed region), B (fixed per-function regions), C (AI dispatcher).
- Logs one JSON line per invocation with `experiment_id`, `scenario`, `event_id`,
  and `trace_hour` so you can correlate runner, dispatcher, and function logs.
- Logs `end_to_end_latency_ms` for direct invocations (scenario A/B) and leaves it
  `null` when time-shifting makes end-to-end latency irrelevant (scenario C).

## Files

- `main.py`: Cloud Run Job entrypoint.
- `requirements.txt`: Python dependencies.
- `env.example.yaml`: example env vars for `gcloud run jobs deploy`.
- `hourly_region_map.example.json`: legacy hourly mapping (no longer used by scenario B).

## Payloads (aligned to the protocol)

- Health check: `{"check":"ping"}`
- Image converter: `{"gcs_uri":"<IMAGE_URI>","format":"WEBP","quality":85}`
- Crypto key gen: `{"bits":4096}`
- Video transcoder: `{"gcs_uri":"<VIDEO_URI>","passes":2}`

The runner injects `experiment_id`, `scenario`, `event_id`, `dispatch_sent_time_utc` and `trace_hour` into
every payload for correlation.

## Latency logging

Every invocation log line includes:

- `end_to_end_latency_ms`: HTTP round-trip time from the loadgen client to the
  function when it is invoked directly (scenario A/B).
- `time_shifted`: `true` when the dispatcher schedules execution for a later time
  (scenario C). In this case `end_to_end_latency_ms` is `null`.
- `end_to_end_latency_source` and `end_to_end_latency_note` to clarify whether
  latency was measured, deferred, or unavailable.


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

- `FUNCTION_URLS_JSON`: JSON mapping of function -> region -> URL (inline JSON string).

Scenario A (fixed region):

- `FIXED_REGION`: Region name used for all functions (for example `us-east1`).

Scenario B (fixed per-function regions):

- `FUNCTION_URLS_JSON`: Provide one target region per function (CPU -> `europe-north2`,
  GPU -> `europe-west1`). If multiple regions are provided for a function, the loader
  prefers `europe-west1` for GPU functions and `europe-north2` for CPU functions.

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
- `LOG_GCS_BUCKET`: GCS bucket to write JSONL logs (one object per job run).
- `LOG_GCS_PREFIX`: GCS object prefix (default `loadgen-logs`).
- `LOG_GCS_OBJECT`: Full object name override for the log file. If no template placeholders
  are present, the runner appends a run timestamp to avoid overwriting prior runs.
- `LOG_GCS_ALLOW_OVERWRITE`: `true` to keep `LOG_GCS_OBJECT` unchanged (may overwrite).
- `LOG_GCS_OBJECT` placeholders: `{run_id}`, `{timestamp}`, `{trace_hour}`,
  `{experiment_id}`, `{scenario}`.

## Deploy

> Always deploy Cloud Run Jobs from an explicit container image (Dockerfile + Artifact Registry).
> Avoid `--source` / buildpacks for Jobs. They are optimized for services and can lead to silent execution failures.

## Prerequisites (one-time)

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com
```

Ensure Docker auth is configured:

```bash
gcloud auth configure-docker us-east1-docker.pkg.dev
```


## Dockerfile (required)

```dockerfile
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENTRYPOINT ["python", "-u", "main.py"]
```

**Notes**

* `PYTHONUNBUFFERED=1` guarantees logs are flushed immediately
* No buildpacks, no implicit web server, no hidden entrypoints

---

## Environment configuration

1. Copy the example file:

```bash
cp env.example.yaml env.A.yaml
```

2. Fill in real values.

## Build the container image

Images are stored in Artifact Registry.

Create the repository once (if not already present):

```bash
gcloud artifacts repositories create loadgen \
  --repository-format=docker \
  --location=us-east1 \
  --description="Images for load generator jobs" || true
```

Build and push the image:

```bash
gcloud builds submit \
  --tag us-east1-docker.pkg.dev/PROJECT_ID/loadgen/loadgen-job:latest \
  .
```

---

## Deploy the Cloud Run Job (image-based)

```bash
gcloud run jobs deploy loadgen-job \
  --image us-east1-docker.pkg.dev/PROJECT_ID/loadgen/loadgen-job:latest \
  --region us-east1 \
  --tasks 1 \
  --max-retries 0 \
  --task-timeout 3600 \
  --env-vars-file env.A.yaml
```

### Verify the job points to the correct image

```bash
gcloud run jobs describe loadgen-job \
  --region us-east1 \
  --format="value(spec.template.spec.template.spec.containers[0].image)"
```

---

## Manual execution (required test)

Always validate manually before scheduling:

```bash
gcloud run jobs execute loadgen-job --region us-east1 --wait
```

View logs:

```bash
gcloud run jobs logs read loadgen-job --region us-east1 --limit 2000
```

You should see:

* A clear startup log
* Load generation progress
* A clean exit

---

## Hourly scheduling (Cloud Scheduler)

Once the job runs successfully, schedule it hourly.

### Create a scheduler service account (once)

```bash
gcloud iam service-accounts create scheduler-sa
```

Grant permissions:

```bash
gcloud run jobs add-iam-policy-binding loadgen-job \
  --member="serviceAccount:scheduler-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region us-east1
```

### Create the scheduler job

```bash
gcloud scheduler jobs create http loadgen-hourly \
  --schedule "0 8-18 * * *" \
  --time-zone "America/New_York" \
  --uri "https://us-east1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/PROJECT_ID/jobs/loadgen-job:run" \
  --http-method POST \
  --oauth-service-account-email scheduler-sa@PROJECT_ID.iam.gserviceaccount.com
```

---

## Debugging checklist

If a job fails:

1. **Check logs first**

   ```bash
   gcloud run jobs logs read loadgen-job --region us-east1 --limit 2000
   ```

2. **Confirm image**

   ```bash
   gcloud run jobs describe loadgen-job --region us-east1
   ```

3. **Run DRY_RUN=true** to isolate startup vs runtime failures

4. **If stdout/stderr are empty**, the process did not start → check Dockerfile and ENTRYPOINT

5. **Avoid `--source` for jobs**


## Notes for scenario B

Scenario B always invokes functions in fixed regions: CPU functions in
`europe-north2` and GPU functions in `europe-west1`.

## Notes for scenario C

The load generator sends the full function payload to the dispatcher and does
not invoke the target functions directly. The dispatcher is expected to forward
the payload to the chosen region/time.
