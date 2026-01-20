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

- `main.py`: Cloud Run Job entrypoint.
- `requirements.txt`: Python dependencies.
- `env.example.yaml`: example env vars for `gcloud run jobs deploy`.
- `hourly_region_map.example.json`: optional fallback mapping for scenario B.

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

- `FUNCTION_URLS_JSON`: JSON mapping of function -> region -> URL (inline JSON string).

Scenario A (fixed region):

- `FIXED_REGION`: Region name used for all functions (for example `us-east1`).

Scenario B (hourly lowest-carbon via carbon forecast):

- `CARBON_FORECAST_JSON`: Raw JSON string for the daily carbon forecast.
- or `CARBON_FORECAST_PATH`: Path to the forecast file (for local runs).
- or `CARBON_FORECAST_GCS_BUCKET`: GCS bucket that stores the forecast.
  - Optional `CARBON_FORECAST_GCS_OBJECT` (default: `carbon_forecasts.json`).

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
  --schedule "0 * * * *" \
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

The runner selects the lowest carbon-intensity region per hour from the daily
forecast. The expected local file is `local_bucket/carbon_forecasts.json` unless
you provide a different path via `CARBON_FORECAST_PATH`.

## Notes for scenario C

The load generator sends the full function payload to the dispatcher and does
not invoke the target functions directly. The dispatcher is expected to forward
the payload to the chosen region/time.
