# Load Generator (loadgen.py)

`loadgen.py` drives repeatable HTTP POST workloads against the sample functions
and writes structured results for analysis. It is designed for local runs and
Cloud Run endpoints, with consistent JSON payloads.

## What it does

- Sends POST requests with JSON bodies to each configured function endpoint.
- Supports multiple URLs per function (comma-separated), with randomized
  distribution across the list.
- Runs a warmup phase (optional) and a measured phase.
- Writes per-request logs (`results_<run_id>.jsonl`) and a summary
  (`summary_<run_id>.json`) to `OUT_DIR`.

## Quick start

1) Ensure dependencies are installed (managed through the project's `environment.yml`)

2) Configure your environment:

```bash
cp .env.example .env
# edit .env with your URLs and payload settings
```

3) Run the load generator:

```bash
python loadgen.py
```

### Run and output

- `RUN_ID`: Run identifier used in output filenames.
- `OUT_DIR`: Directory for results and summary files.

### Load settings

- `TOTAL_REQUESTS_PER_URL`: Number of measured requests per URL.
- `WARMUP_REQUESTS_PER_URL`: Warmup requests per URL (set to 0 to skip).
- `CONCURRENCY`: Worker threads for concurrent requests.
- `TIMEOUT_S`: Per-request timeout in seconds.
- `VERIFY_TLS`: Set to `false` for local/self-signed HTTPS.
- `SLEEP_BETWEEN_REQUESTS_S`: Fixed delay before each request.
- `JITTER_S`: Random delay added per request (0..JITTER_S seconds).

### Auth and headers

- `AUTH_BEARER_TOKEN`: Adds `Authorization: Bearer <token>`.
- `EXTRA_HEADERS_JSON`: JSON object of additional headers.

### Target URLs

Comma-separated base URLs per function. Each request is sent to
`<base_url>/<REQUEST_PATH>`.

- `API_HEALTH_CHECK_URLS`
- `IMAGE_FORMAT_CONVERTER_URLS`
- `CRYPTO_KEY_GEN_URLS`
- `VIDEO_TRANSCODER_URLS`
- `REQUEST_PATH` (default `/`)

### Payload configuration

These map directly to the sample function schemas (JSON only).

**Image converter**
- `IMAGE_MODE`: `gcs` or `inline`.
- `IMAGE_GCS_URI`: `gs://bucket/object` when `IMAGE_MODE=gcs`.
- `IMAGE_INLINE_KB`: Size of inline payload (KB) when `IMAGE_MODE=inline`.
- `IMAGE_RETURN_INLINE`: Set to `true` to request inline output.

**Crypto key generator**
- `CRYPTO_KEY_SIZE`: Maps to `bits` in the handler.
- `CRYPTO_PUBLIC_EXPONENT`
- `CRYPTO_TARGET_MS`
- `CRYPTO_ITERATIONS`

**Video transcoder**
- `VIDEO_MODE`: `gcs` or `inline`.
- `VIDEO_GCS_URI`: `gs://bucket/object` when `VIDEO_MODE=gcs`.
- `VIDEO_INLINE_KB`: Size of inline payload (KB) when `VIDEO_MODE=inline`.
- `VIDEO_PASSES`
- `VIDEO_TARGET_MS`
- `VIDEO_RETURN_INLINE`: Set to `true` to request inline output.

## Payload behavior

- All requests are JSON, with `Content-Type: application/json`.
- Inline payloads are base64 in the `data` field.
- GCS payloads use `gcs_uri` (or `bucket`/`object` if you customize loadgen).
- Inline output may be rejected by handlers if it exceeds `MAX_INLINE_MB`.

## Outputs

### results_<run_id>.jsonl

One JSON object per request, including:

- `phase`: `warmup` or `measured`
- `function_id`, `base_url`, `url`
- `latency_ms`, `status_code`
- `request_bytes`, `response_bytes`
- `error` (if any)
- `response_json` (parsed JSON when possible) or `response_snippet`

### summary_<run_id>.json

Aggregated stats by function and base URL, computed from `phase=="measured"`:

- `latency_ms`: p50, p95, p99, min, max, mean
- `bytes`: request_mean, response_mean
- `ok_count`, `error_count`
- `http_status_counts`

## Mapping results to metadata

The sample handlers return `input_bytes` and `output_bytes` in their JSON
responses. Use those values (when present) or the `request_bytes` and
`response_bytes` from the results to estimate:

- `runtime_ms`: use `latency_ms` or the handler-reported timing.
- `data_input_gb` / `data_output_gb`: derive from bytes in/out.
- `invocations_per_day`: map from your trace plan or loadgen run size.

This aligns with the `function_metadata.json` schema used by the agent.

## Tips and troubleshooting

- If you see `413` responses, reduce inline payload sizes or set
  `IMAGE_RETURN_INLINE=false` and `VIDEO_RETURN_INLINE=false` to use GCS.
- If requests time out, increase `TIMEOUT_S` to exceed the longest `target_ms`.
- If a function should be skipped, leave its URL list empty.

## Notes for Cloud Run

- Ensure the function service account has read access to GCS inputs and write
  access for outputs when using `gcs` modes.
- Use separate URLs per region to compare performance across deployments.
