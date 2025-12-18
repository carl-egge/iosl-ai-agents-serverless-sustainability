# GCP Agent Deployment Guide

## Quick Checklist

- [ ] Check if secrets exist with `gcloud secrets list` (create them if not)
- [ ] Upload `function_metadata.json` to GCS bucket
- [ ] Upload 5 files to Cloud Shell: `gcp_agent.py`, `prompts.py`, `config_loader.py`, `requirements.txt`, `Procfile`
- [ ] Deploy with `gcloud run deploy` (includes `--set-secrets` flag)
- [ ] Test with `curl -X POST YOUR_SERVICE_URL/run`

## What This Does

A new GCP-deployable agent that:
- Reads function metadata from GCS bucket (`function_metadata.json`)
- Uses sophisticated scheduling logic from `prompts.py`
- Generates carbon-aware schedules for multiple functions
- Outputs one schedule file per function

## Files You Need to Upload to Cloud Shell

**Yes, you need to upload ALL FIVE files:**

From `src/agent/`:
1. **`gcp_agent.py`** - Main agent (GCP-specific logic)
2. **`prompts.py`** - Shared prompt generation
3. **`config_loader.py`** - GCP-compatible config loader (loads config from GCS)

From `deployments/gcp/`:
4. **`requirements.txt`** - Python dependencies
5. **`Procfile`** - Gunicorn configuration

## File You Need to Upload to GCS Bucket

Upload to `gs://faas-scheduling-us-east1/`:

**`function_metadata.json`** (from `data/sample/`)

This contains metadata for the `write_to_bucket` function:
- **Runtime**: 150ms (lightweight JSON write)
- **Memory**: 256MB
- **Instant execution**: `true` (HTTP endpoint)
- **Data transfer**: 0.00001 GB (~10KB JSON)
- **Invocations**: 1000/day

## Prerequisites - API Keys Setup (If Not Done Already)

**Check if secrets exist:**
```bash
gcloud secrets list
```

If you see `ELECTRICITYMAPS_TOKEN` and `GEMINI_API_KEY`, **skip this section**.

If NOT, create them:
```bash
# Create secrets
echo -n "xxx" | gcloud secrets create ELECTRICITYMAPS_TOKEN --data-file=-
echo -n "xxx" | gcloud secrets create GEMINI_API_KEY --data-file=-

# Grant permissions
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')

gcloud secrets add-iam-policy-binding ELECTRICITYMAPS_TOKEN \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

## Deployment Steps

### Step 1: Upload function_metadata.json to GCS

```bash
gcloud storage cp data/sample/function_metadata.json gs://faas-scheduling-us-east1/
```

Verify all required files are in the bucket:
```bash
gcloud storage ls gs://faas-scheduling-us-east1/
# Should show: static_config.json, function_template.json, function_metadata.json
```

### Step 2: Upload Python Files to Cloud Shell

Open [Google Cloud Shell](https://shell.cloud.google.com/):

```bash
mkdir -p ~/agent-new
cd ~/agent-new
```

Upload these 5 files via Cloud Shell UI (click **⋮** → **Upload**):
- `src/agent/gcp_agent.py`
- `src/agent/prompts.py`
- `src/agent/config_loader.py`
- `deployments/gcp/requirements.txt`
- `deployments/gcp/Procfile`

Verify all files are there:
```bash
ls -la
# Should show: gcp_agent.py, prompts.py, config_loader.py, requirements.txt, Procfile
```

### Step 3: Deploy to Cloud Run

```bash
cd ~/agent-new

gcloud run deploy agent \
  --source . \
  --region us-east1 \
  --timeout=300 \
  --allow-unauthenticated \
  --set-secrets ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest
```

**How this works:**
- `--set-secrets` maps GCP secrets to environment variables
- Format: `ENV_VAR_NAME=SECRET_NAME:version`
- `gcp_agent.py` reads these via `os.environ.get("ELECTRICITYMAPS_TOKEN")` and `os.environ.get("GEMINI_API_KEY")`
- Your actual API keys are stored securely in GCP Secret Manager

Wait for deployment (~2-3 minutes). You'll see a service URL when done.

### Step 4: Test the Deployment

Get your service URL:
```bash
gcloud run services describe agent --region us-east1 --format='value(status.url)'
```

Health check:
```bash
curl YOUR_SERVICE_URL/health
```

Run scheduler:
```bash
curl -X POST YOUR_SERVICE_URL/run
```

## Expected Response

When you call `/run`, you get:

```json
{
  "status": "success",
  "message": "Carbon-aware schedules generated successfully",
  "forecast_location": "gs://faas-scheduling-us-east1/carbon_forecasts.json",
  "functions": {
    "write_to_bucket": {
      "status": "success",
      "schedule_location": "gs://faas-scheduling-us-east1/schedule_write_to_bucket.json",
      "top_5_recommendations": [
        {
          "datetime": "2025-12-08T14:00:00",
          "region": "europe-north1",
          "carbon_intensity": 28,
          "transfer_cost_usd": 0.0002,
          "priority": 1,
          "reasoning": "Despite minimal $0.0002 transfer cost, Finland offers lowest carbon..."
        }
      ],
      "total_recommendations": 24
    }
  }
}
```

## Output Files in GCS

After running, check your bucket:

```bash
gcloud storage ls gs://faas-scheduling-us-east1/
```

You'll see:
- `carbon_forecasts.json` - Raw forecast data
- `schedule_write_to_bucket.json` - Schedule for write_to_bucket function

View the schedule:
```bash
gcloud storage cat gs://faas-scheduling-us-east1/schedule_write_to_bucket.json
```

## Adding More Functions

To schedule additional functions, update `function_metadata.json` in GCS:

```bash
# Download current file
gcloud storage cp gs://faas-scheduling-us-east1/function_metadata.json .

# Edit it (add more functions to the "functions" object)
# Then upload back
gcloud storage cp function_metadata.json gs://faas-scheduling-us-east1/
```

### Format Options

You can specify functions in **TWO ways**:

#### 1. Structured JSON (explicit parameters)
```json
{
  "functions": {
    "process_images": {
      "function_id": "process_images",
      "runtime_ms": 3000,
      "memory_mb": 1024,
      "instant_execution": false,
      "description": "Process and resize images",
      "data_input_gb": 2.0,
      "data_output_gb": 1.5,
      "source_location": "us-east1",
      "invocations_per_day": 500,
      "allowed_regions": ["europe-west1", "us-east1"]
    }
  }
}
```

#### 2. Natural Language (AI-parsed)
```json
{
  "functions": {
    "video_transcoder": "I need a function that transcodes video files from MP4 to WebM format. It processes about 50 videos per day, each video is around 500MB. The transcoding takes a few minutes per video. Videos are stored in us-east1, and processing can be delayed by a few hours."
  }
}
```

When you use natural language:
- Gemini extracts all parameters automatically
- Returns confidence score and assumptions
- Shows warnings about uncertainties
- You can review extracted metadata in the logs

#### 3. Mixed Format (both in same file)
```json
{
  "functions": {
    "write_to_bucket": {
      "function_id": "write_to_bucket",
      "runtime_ms": 150,
      ...
    },
    "video_transcoder": "I need a function that transcodes videos..."
  }
}
```

See `data/sample/function_metadata_mixed.json` for a complete example.

No redeployment needed - just call `/run` again!

## Differences from `old_gcp_agent.py`

| Feature | old_gcp_agent.py | gcp_agent.py |
|---------|------------------|--------------|
| Function metadata | Hardcoded in file | Read from GCS |
| Multiple functions | Single function only | Multiple functions |
| Input format | Structured JSON only | Structured JSON OR natural language |
| Natural language parsing | No | Yes (AI-powered with Gemini) |
| Region filtering | No | Yes (per-function allowed_regions) |
| Prompt logic | Inline basic prompt | Shared sophisticated prompts |
| Cost calculation | Basic inline logic | Shared config_loader module |
| Schedule output | Single file | One file per function |
| Reasoning quality | Basic | Detailed cost-benefit analysis |
| Priority sorting | Not enforced | Explicitly enforced in LLM prompt |
