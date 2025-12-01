# Carbon-Aware Serverless Function Scheduler

A Google Cloud Run service that uses Electricity Maps carbon intensity forecasts and Google Gemini AI to recommend optimal execution times and regions for serverless functions.

## Prerequisites

- Google Cloud project with billing enabled
- GCS bucket (already created)
- API keys:
  - [Electricity Maps API token](https://api-portal.electricitymaps.com/)
  - [Google Gemini API key](https://aistudio.google.com/app/apikey)

## Setup (One-Time)

Open [Google Cloud Shell](https://shell.cloud.google.com/) and run the following commands.

### 1. Set Your Project

```bash
gcloud config set project YOUR_PROJECT_ID
```

### 2. Create Secrets

Store your API keys securely (replace with your actual keys):

```bash
echo -n "YOUR_ELECTRICITYMAPS_TOKEN" | gcloud secrets create ELECTRICITYMAPS_TOKEN --data-file=-

echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create GEMINI_API_KEY --data-file=-
```

> **Important:** The `-n` flag prevents adding a newline character to the secret.

### 3. Grant Permissions

Allow Cloud Run to access the secrets:

```bash
# Get your project number
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')

# Grant secret access
gcloud secrets add-iam-policy-binding ELECTRICITYMAPS_TOKEN \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding GEMINI_API_KEY \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### 4. Upload Files

```bash
mkdir -p ~/agent
cd ~/agent
```

Upload the deployment files from `deployments/gcp/` via Cloud Shell UI (click **⋮** → **Upload**):
- `deployments/gcp/main.py`
- `deployments/gcp/requirements.txt`
- `deployments/gcp/Procfile`

Verify:
```bash
ls -la  # Should show: main.py, requirements.txt, Procfile
```

### 5. Deploy

```bash
cd ~/agent

gcloud run deploy agent \
  --source . \
  --region europe-west1 \
  --timeout=300 \
  --allow-unauthenticated \
  --set-env-vars GCS_BUCKET_NAME=YOUR_BUCKET_NAME \
  --set-secrets ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest
```

Replace `YOUR_BUCKET_NAME` with your actual GCS bucket name.

---

## Running the Scheduler

### Trigger via curl

First, get your service URL:
```bash
gcloud run services describe agent --region europe-west1 --format='value(status.url)'
```

Then trigger the scheduler:
```bash
curl -X POST YOUR_SERVICE_URL/run
```

### Trigger via Cloud Console

1. Go to [Cloud Run](https://console.cloud.google.com/run)
2. Click on `agent` service
3. Click the service URL
4. Append `/run` to the URL

### Response

On success, you'll receive:
```json
{
  "status": "success",
  "message": "Carbon-aware schedule generated successfully",
  "schedule_location": "gs://YOUR_BUCKET/execution_schedule.json",
  "forecast_location": "gs://YOUR_BUCKET/carbon_forecasts.json",
  "top_5_recommendations": [...],
  "total_recommendations": 24
}
```

---

## Checking Results

### View Schedule in GCS

```bash
gcloud storage cat gs://YOUR_BUCKET_NAME/execution_schedule.json
```

### View Carbon Forecasts

```bash
gcloud storage cat gs://YOUR_BUCKET_NAME/carbon_forecasts.json
```

### View Logs

```bash
gcloud run services logs read agent --region europe-west1 --limit=50
```

### Health Check

```bash
curl YOUR_SERVICE_URL/health
```

---

## Output Files

| File | Description |
|------|-------------|
| `execution_schedule.json` | Gemini's 24-hour scheduling recommendations sorted by carbon efficiency |
| `carbon_forecasts.json` | Raw carbon intensity forecasts from Electricity Maps for all regions |

### Schedule Format

```json
{
  "recommendations": [
    {
      "datetime": "2025-11-23T22:00:00",
      "region": "europe-north1",
      "carbon_intensity": 45,
      "priority": 1
    },
    ...
  ],
  "metadata": {
    "generated_at": "2025-11-23T19:30:00",
    "regions_used": ["europe-north1", "europe-west1", ...],
    "failed_regions": []
  }
}
```

---


## Troubleshooting

### "Invalid header value" error
The secret has a newline. Recreate it:
```bash
gcloud secrets delete ELECTRICITYMAPS_TOKEN --quiet
echo -n "YOUR_TOKEN" | gcloud secrets create ELECTRICITYMAPS_TOKEN --data-file=-
# Re-grant permissions (see step 3)
# Redeploy
```

### Worker timeout
Ensure both timeouts are set:
- `Procfile` contains `--timeout 300`
- Deploy command includes `--timeout=300`

### Permission denied on secrets
Run the permission commands from step 3, then redeploy.

### Check secret values
```bash
gcloud secrets versions access latest --secret=ELECTRICITYMAPS_TOKEN
gcloud secrets versions access latest --secret=GEMINI_API_KEY
```
