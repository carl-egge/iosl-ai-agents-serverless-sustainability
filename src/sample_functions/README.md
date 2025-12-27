# Cloud Run Sample Functions

This directory contains three tiny Python entrypoints that work on Cloud Run via `functions-framework`. Each module exposes one HTTP handler whose sole job is the functionality you asked for: an addition calculator, a carbon-intensity API summary, and a bucket writer.

## Files

- `simple_addition.py`: read `num1`/`num2` from a JSON body, return their sum in JSON.
- `carbon_api_call.py`: hit the Electricity Maps forecast API for a zone (defaults to `DE`) and summarize the carbon-intensity values; the module loads the repo root `.env` so the token can be set locally.
- `write_to_bucket.py`: write the request payload to a new `runs/.../result.json` object in the bucket named by `OUTPUT_BUCKET`.
- `requirements.txt`: dependencies that the Cloud Run buildpack installs.

## Deploying with one command

1. `cd src/sample_functions`
2. Run the appropriate `gcloud run deploy` command (replace the placeholders below):

```
gcloud run deploy simple-addition \
  --source . \
  --region YOUR_REGION \
  --allow-unauthenticated \
  --function simple_addition
```

```
gcloud run deploy carbon-call \
  --source . \
  --region YOUR_REGION \
  --allow-unauthenticated \
  --function carbon_api_call \
  --set-env-vars ELECTRICITYMAPS_TOKEN=your-token
```

```
gcloud run deploy bucket-writer \
  --source . \
  --region YOUR_REGION \
  --allow-unauthenticated \
  --function write_to_bucket \
  --set-env-vars OUTPUT_BUCKET=your-bucket,REGION=europe-west1
```

The buildpack reads `requirements.txt`, installs the deps, and runs each module via the Functions Framework entrypoint you supply.

## Calling the functions

Use the URL that `gcloud run deploy` prints.

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

## Local sanity checks

Install deps with `pip install -r requirements.txt`, then run any module directly, for example:

```
python carbon_api_call.py
```

This prints the handler output using the built-in dummy requests defined at the bottom of each file.
