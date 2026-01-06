# Cloud Run Sample Functions

A set of self-contained Python entrypoint scripts that can be deployed from `src/sample_functions` via `functions-framework`. Each handler focuses on one workload profile so you can explore different runtime/data scenarios without writing Flask apps.

## Files

- `simple_addition.py`: read `num1`/`num2` from a JSON POST payload and return the sum in JSON.
- `carbon_api_call.py`: call Electricity Maps, summarize carbon-intensity metrics, and load credentials from the repo root `.env` for local runs.
- `write_to_bucket.py`: write the received JSON payload into a new `runs/.../result.json` object under `OUTPUT_BUCKET`.
- `api_health_check.py`: lightweight API health check payload for high-throughput, low-data invocations.
- `image_format_converter.py`: synthesize a large PNG image and transcode it to WebP to emulate short runtime + large data.
- `crypto_key_gen.py`: generate an RSA key pair to consume CPU time while keeping payload sizes small (long runtime + little data).
- `video_transcoder.py`: create large buffers and compress them multiple times so the function resembles a long runtime + large data transcoding job.
- `requirements.txt`: declares `functions-framework`, HTTP helpers, image/crypto dependencies, and the GCS client.
- `main.py`: re-exports every handler so Cloud Run Buildpacks find the functions without an explicit `GOOGLE_FUNCTION_SOURCE`.

## Deploying each handler from source

1. `cd src/sample_functions`
2. Run the command that matches the handler you want to publish (replace placeholders with your region/bucket/token values):

The `main.py` file re-exports every handler so that Buildpacks discover the function you name with `--function` without needing `GOOGLE_FUNCTION_SOURCE`.

```
gcloud run deploy api-health-check \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function api_health_check
```

```
gcloud run deploy carbon-call \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function carbon_api_call \
  --set-env-vars ELECTRICITYMAPS_TOKEN=your-token
```

```
gcloud run deploy bucket-writer \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function write_to_bucket \
  --set-env-vars OUTPUT_BUCKET=your-bucket,REGION=europe-west1
```

```
gcloud run deploy image-converter \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function image_format_converter
```

```
gcloud run deploy crypto-key-gen \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function crypto_key_gen
```

```
gcloud run deploy video-transcoder \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --function video_transcoder
```

The buildpack reads `requirements.txt`, installs the dependencies, and runs the Functions Framework handler you declare via `--entrypoint` or `--function` (for the runtime-powered paths).

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

- Image converter (convert an uploaded image and return the new bytes; responds with base64 `converted_image`):

```
curl -X POST $URL -H "Content-Type: application/octet-stream" --data-binary @input.png
```

To process a larger file already in GCS, send JSON with either `gcs_uri` (e.g. `"gs://your-bucket/big.png"`) or `bucket`/`object` fields along with optional `format`/`quality` overrides. You can also use `multipart/form-data` (or Insomnia’s form editor) and add the `gcs_uri` field there instead of JSON:
```
curl -X POST $URL -H "Content-Type: application/json" \
  -d '{"gcs_uri":"gs://example-bucket/big.png","format":"WEBP","quality":85}'
```

If you prefer JSON for smaller payloads, encode them with `base64` and send `{"data": "..."}`; the handler still returns the converted bytes as `converted_image`.

- Crypto key generator (optional `bits`):

```
curl -X POST $URL -H "Content-Type: application/json" -d '{"bits": 4096}'
```

- Video transcoder (compress the payload you upload and receive the processed bytes as base64):

```
curl -X POST $URL -H "Content-Type: application/octet-stream" --data-binary @large-video.bin
```

You can optionally send `{"passes": 4, "data": "...base64..."}` if you need to wrap the payload inside JSON; the `processed_data` field in the response holds the last pass output.

To process a large asset already in GCS, send a JSON body with `gcs_uri` (or `bucket`/`object`). You can still specify `passes` to control the number of compression passes.
```
curl -X POST $URL -H "Content-Type: application/json" \
  -d '{"gcs_uri":"gs://example-bucket/big-video.bin","passes":4}'
```

## Local sanity checks

Install dependencies with `pip install -r requirements.txt`, then run any module directly, for example:

```
python simple_addition.py
```

Each module prints its own sample response by invoking the decorated handler with a dummy request.
The latest video and image handlers already build a sample payload when executed this way, so running `python video_transcoder.py` or `python image_format_converter.py` still demonstrates a valid response.
