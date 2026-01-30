# Cloud Run Sample Functions

A set of self-contained Python entrypoint scripts that can be deployed from `src/sample_functions` via `functions-framework`. Each handler focuses on one workload profile so you can explore different runtime/data scenarios without writing Flask apps.

## Files

- `simple_addition.py`: read `num1`/`num2` from a JSON POST payload and return the sum in JSON.
- `carbon_api_call.py`: call Electricity Maps, summarize carbon-intensity metrics, and load credentials from the repo root `.env` for local runs.
- `write_to_bucket.py`: write the received JSON payload into a new `runs/.../result.json` object under `OUTPUT_BUCKET`.
- `api_health_check.py`: lightweight API health check payload for high-throughput, low-data invocations.
- `image_format_converter.py`: convert an input image (base64 JSON or GCS) to another format. Defaults to writing output to GCS.
- `crypto_key_gen.py`: generate an RSA key pair to consume CPU time while keeping payload sizes small (long runtime + little data).
- `video_transcoder.py`: compress an input payload (base64 JSON or GCS) multiple times to emulate long runtime + large data. Defaults to writing output to GCS.
- `requirements.txt`: declares `functions-framework`, HTTP helpers, image/crypto dependencies, and the GCS client.
- `main.py`: re-exports every handler so Cloud Run Buildpacks find the functions without an explicit `GOOGLE_FUNCTION_SOURCE`.

## MCP-aligned deployment (recommended)

These commands produce **Cloud Run container services** that match the MCP deployer's
runtime shape (Flask wrapper + gunicorn + Dockerfile build).

1. (Optional) If you changed any sample handler, refresh the MCP code bundle:

```
python src/sample_functions/generate_mcp_bundle.py api_health_check
python src/sample_functions/generate_mcp_bundle.py image_format_converter
python src/sample_functions/generate_mcp_bundle.py crypto_key_gen
python src/sample_functions/generate_mcp_bundle.py video_transcoder
```

2. Export MCP-style container bundles:

```
python src/sample_functions/export_mcp_container.py --out out/manual_deploy
```

3. Deploy each function with explicit runtime flags (replace `<REGION>`):

```
cd out/manual_deploy/api_health_check
gcloud run deploy api-health-check \
  --source . \
  --region <REGION> \
  --allow-unauthenticated \
  --ingress all \
  --cpu 1 \
  --memory 512Mi \
  --timeout 180 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 20 \
  --cpu-throttling \
  --port 8080
```

```
cd out/manual_deploy/image_format_converter
gcloud run deploy image-format-converter \
  --source . \
  --region <REGION> \
  --allow-unauthenticated \
  --ingress all \
  --cpu 1 \
  --memory 2048Mi \
  --timeout 360 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 20 \
  --cpu-throttling \
  --port 8080
```

```
cd out/manual_deploy/crypto_key_gen
gcloud run deploy crypto-key-gen \
  --source . \
  --region <REGION> \
  --allow-unauthenticated \
  --ingress all \
  --cpu 1 \
  --memory 4096Mi \
  --timeout 360 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 20 \
  --cpu-throttling \
  --port 8080
```

```
cd out/manual_deploy/video_transcoder
gcloud run deploy video-transcoder \
  --source . \
  --region <REGION> \
  --allow-unauthenticated \
  --ingress all \
  --cpu 4 \
  --memory 8192Mi \
  --timeout 360 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 20 \
  --cpu-throttling \
  --port 8080
```

## Legacy buildpack deployment (not MCP-aligned)

The legacy approach uses Cloud Run Functions buildpacks (`--function`) and does
not match MCP's container wrapper/runtime. Keep only for quick experiments.

```
gcloud run deploy api-health-check \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function api_health_check
```

## Calling the functions

Use the URL that each `gcloud run deploy` command prints (replace `$URL` below):

- Simple addition:

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"num1": 7, "num2": 8}'
```

- API call (requires valid `ELECTRICITYMAPS_TOKEN`):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"zone": "DE", "horizonHours": 6}'
```

- Write to bucket (any JSON payload):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"note": "cloud run write"}'
```

- Health check:

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"check": "ping"}'
```

- Image converter (JSON only; by default writes output to GCS and returns metadata):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"gcs_uri":"gs://example-bucket/big.png","format":"WEBP","quality":85}'
```

If you need inline output (debug only), send base64 data and set `return_inline: true`.
The response includes `converted_image_base64` and is capped by `MAX_INLINE_MB` (default: 16).
```
curl -X POST $URL -H "Content-Type: application/json" -d '{"data":"<base64>","format":"WEBP","quality":85,"return_inline":true}'
```

- Crypto key generator (optional `bits`):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"bits": 4096}'
```

- Video transcoder (JSON only; by default writes output to GCS and returns metadata):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"gcs_uri":"gs://example-bucket/big-video.bin","passes":4}'
```

If you need inline output (debug only), send base64 data and set `return_inline: true`.
The response includes `processed_data_base64` and is capped by `MAX_INLINE_MB` (default: 16).
```
curl -X POST $URL -H "Content-Type: application/json" -d '{"data":"<base64>","passes":4,"return_inline":true}'
```

## Local sanity checks

Install dependencies with `pip install -r requirements.txt`, then run any module directly, for example:

```
python simple_addition.py
```

Each module prints its own sample response by invoking the decorated handler with a dummy request.
The latest video and image handlers already build a sample payload when executed this way, so running `python video_transcoder.py` or `python image_format_converter.py` still demonstrates a valid response.
