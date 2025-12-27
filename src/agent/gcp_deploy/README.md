# GCP Cloud Run Deployment Guide

This directory contains the files needed to deploy the carbon-aware scheduling agent to Google Cloud Run.

## Directory Contents

- `main.py` - Entrypoint that imports the Flask app from `agent.py`
- `requirements.txt` - Python dependencies for Cloud Run
- `Procfile` - Gunicorn web server configuration
- `README.md` - This deployment guide

**Note:** The core agent logic is in `src/agent/agent.py` - this directory only contains deployment configuration.

## Prerequisites

### 1. GCP Project Setup
- GCP project with billing enabled
- APIs enabled: Cloud Run, Cloud Storage, Secret Manager
- `gcloud` CLI installed and authenticated

### 2. API Keys
- Electricity Maps API token
- Google Gemini API key

### 3. GCS Bucket with Configuration Files

Create a bucket and upload configuration:

```bash
export BUCKET_NAME="faas-scheduling-us-east1"
export REGION="us-east1"

# Create bucket
gcloud storage buckets create gs://${BUCKET_NAME} \
  --location=${REGION}

# Upload configuration files from local_bucket/
gcloud storage cp ../../local_bucket/static_config.json gs://${BUCKET_NAME}/
gcloud storage cp ../../local_bucket/function_metadata.json gs://${BUCKET_NAME}/
```

Verify files are uploaded:
```bash
gcloud storage ls gs://${BUCKET_NAME}/
# Should show: static_config.json, function_metadata.json
```

## Deployment Steps

### Step 1: Create Secrets

Check if secrets already exist:
```bash
gcloud secrets list
```

If `ELECTRICITYMAPS_TOKEN` and `GEMINI_API_KEY` don't exist, create them:

```bash
# Create Electricity Maps token secret
echo -n "your-electricitymaps-token" | gcloud secrets create ELECTRICITYMAPS_TOKEN --data-file=-

# Create Gemini API key secret
echo -n "your-gemini-api-key" | gcloud secrets create GEMINI_API_KEY --data-file=-

# Grant Cloud Run service account access to secrets
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')

gcloud secrets add-iam-policy-binding ELECTRICITYMAPS_TOKEN \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Step 2: Upload Files to Cloud Shell

Open [Google Cloud Shell](https://shell.cloud.google.com/)

Create deployment directory:
```bash
mkdir -p ~/agent-deploy
cd ~/agent-deploy
```

**Upload these 4 files** via Cloud Shell UI (**⋮** → **Upload**):
1. `src/agent/agent.py`
2. `src/agent/prompts.py`
3. `src/agent/gcp_deploy/main.py`
4. `src/agent/gcp_deploy/requirements.txt`
5. `src/agent/gcp_deploy/Procfile`

Verify all files uploaded:
```bash
ls -la
# Should show: agent.py, prompts.py, main.py, requirements.txt, Procfile
```

### Step 3: Deploy to Cloud Run

```bash
cd ~/agent-deploy

gcloud run deploy agent \
  --source . \
  --region us-east1 \
  --timeout=300 \
  --memory=1Gi \
  --cpu=1 \
  --allow-unauthenticated \
  --set-env-vars GCS_BUCKET_NAME=faas-scheduling-us-east1 \
  --set-secrets ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest
```

**What this does:**
- `--source .` - Builds container from current directory using `requirements.txt`
- `--timeout=300` - 5 minute timeout for long-running schedule generation
- `--set-env-vars` - Sets the GCS bucket name
- `--set-secrets` - Injects secrets as environment variables

Deployment takes ~2-3 minutes. When complete, you'll see the service URL.

### Step 4: Test Deployment

Get service URL:
```bash
gcloud run services describe agent \
  --region us-east1 \
  --format='value(status.url)'
```

Test health endpoint:
```bash
curl <SERVICE_URL>/health
```

Expected response:
```json
{
  "status": "healthy",
  "service": "agent",
  "mode": "CLOUD",
  "bucket": "faas-scheduling-us-east1",
  "has_emaps_token": true,
  "has_gemini_key": true
}
```

Run the scheduler:
```bash
curl -X POST <SERVICE_URL>/run
```

Expected response:
```json
{
  "status": "success",
  "message": "Carbon-aware schedules generated successfully",
  "forecast_location": "gs://faas-scheduling-us-east1/carbon_forecasts.json",
  "functions": {
    "write_to_bucket": {
      "status": "success",
      "schedule_location": "gs://faas-scheduling-us-east1/schedule_write_to_bucket.json",
      "top_5_recommendations": [...],
      "total_recommendations": 240
    }
  }
}
```

## How It Works

1. **main.py** - Cloud Run entrypoint
   - Adds `src/` to Python path
   - Imports `create_flask_app()` from `agent.agent`
   - Creates `app` at module level for gunicorn

2. **Procfile** - Tells gunicorn to serve `app` from `main.py`
   - Command: `gunicorn main:app`

3. **requirements.txt** - Cloud Run installs these dependencies during build

4. **agent.py** - Core logic (not in this directory)
   - Detects Cloud Run mode automatically
   - Reads from GCS bucket instead of `local_bucket/`
   - Returns Flask app via `create_flask_app()`

## Updating the Deployment

After changing `agent.py` or other source files:

1. Upload updated files to Cloud Shell
2. Run the same deploy command:

```bash
cd ~/agent-deploy
gcloud run deploy agent --source . --region us-east1
```

Cloud Run will rebuild and redeploy.

## Viewing Logs

```bash
gcloud run services logs read agent \
  --region us-east1 \
  --limit=50
```

Or view in Cloud Console: **Cloud Run → agent → Logs**

## Troubleshooting

### Import errors (Module not found)
- Ensure all 5 files are uploaded to Cloud Shell
- Check `main.py` correctly adds `src/` to path

### API key errors
- Verify secrets exist: `gcloud secrets list`
- Check service account has access to secrets
- View secret value: `gcloud secrets versions access latest --secret="GEMINI_API_KEY"`

### Timeout errors
- Increase timeout: `gcloud run services update agent --timeout=600 --region us-east1`

### GCS permission errors
```bash
# Grant service account Storage Object Admin role
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')

gcloud projects add-iam-policy-binding $(gcloud config get-value project) \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"
```

## Cost Optimization

- **Minimum instances:** 0 (scales to zero when idle)
- **Maximum instances:** 1 (can increase based on load)
- **CPU allocation:** Only during request processing
- **Memory:** 1Gi (adjust based on workload)

## Security Notes

- Secrets stored in Secret Manager (not in code)
- Service account has minimal permissions
- Can restrict to authenticated requests by removing `--allow-unauthenticated`
