#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./schedule_loadgen_job.sh [options]

Options:
  --project PROJECT_ID                 GCP project ID (default: gcloud config project)
  --region REGION                      Cloud Run region (default: us-east1)
  --location LOCATION                  Cloud Scheduler location (default: same as region)
  --job-name LOADGEN_JOB               Cloud Run Job name (default: loadgen-job)
  --scheduler-name SCHEDULER_JOB       Cloud Scheduler job name (default: loadgen-hourly)
  --schedule CRON                      Cron schedule (default: "0 8-18 * * *")
  --time-zone TZ                       Time zone (default: "America/New_York")
  --service-account-name NAME          Service account name (default: scheduler-sa)
  --service-account-email EMAIL        Full service account email (overrides name)
  --attempt-deadline DURATION          Scheduler attempt deadline (default: 1800s)
  -h, --help                           Show help

Examples:
  ./schedule_loadgen_job.sh --project my-project
  ./schedule_loadgen_job.sh --project my-project --schedule "0 * * * *"
USAGE
}

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-east1}"
SCHEDULER_LOCATION="${SCHEDULER_LOCATION:-}"
LOADGEN_JOB="${LOADGEN_JOB:-loadgen-job}"
SCHEDULER_JOB="${SCHEDULER_JOB:-loadgen-hourly}"
SCHEDULE="${SCHEDULE:-0 8-18 * * *}"
TIME_ZONE="${TIME_ZONE:-America/New_York}"
SERVICE_ACCOUNT_NAME="${SERVICE_ACCOUNT_NAME:-scheduler-sa}"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_EMAIL:-}"
ATTEMPT_DEADLINE="${ATTEMPT_DEADLINE:-1800s}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_ID="$2"
      shift 2
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --location)
      SCHEDULER_LOCATION="$2"
      shift 2
      ;;
    --job-name)
      LOADGEN_JOB="$2"
      shift 2
      ;;
    --scheduler-name)
      SCHEDULER_JOB="$2"
      shift 2
      ;;
    --schedule)
      SCHEDULE="$2"
      shift 2
      ;;
    --time-zone)
      TIME_ZONE="$2"
      shift 2
      ;;
    --service-account-name)
      SERVICE_ACCOUNT_NAME="$2"
      shift 2
      ;;
    --service-account-email)
      SERVICE_ACCOUNT_EMAIL="$2"
      shift 2
      ;;
    --attempt-deadline)
      ATTEMPT_DEADLINE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi

if [[ -z "$PROJECT_ID" ]]; then
  echo "PROJECT_ID is required. Pass --project or set PROJECT_ID." >&2
  exit 1
fi

if [[ -z "$SCHEDULER_LOCATION" ]]; then
  SCHEDULER_LOCATION="$REGION"
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")"

if [[ -z "$SERVICE_ACCOUNT_EMAIL" ]]; then
  SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
fi

if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
  if [[ -n "${SERVICE_ACCOUNT_NAME}" && "$SERVICE_ACCOUNT_EMAIL" == "${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com" ]]; then
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" --project "$PROJECT_ID"
  else
    echo "Service account not found: $SERVICE_ACCOUNT_EMAIL" >&2
    exit 1
  fi
fi

gcloud run jobs add-iam-policy-binding "$LOADGEN_JOB" \
  --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
  --role "roles/run.invoker" \
  --region "$REGION" \
  --project "$PROJECT_ID" >/dev/null

SCHEDULER_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$SERVICE_ACCOUNT_EMAIL" \
  --member "serviceAccount:${SCHEDULER_AGENT}" \
  --role "roles/iam.serviceAccountTokenCreator" \
  --project "$PROJECT_ID" >/dev/null

RUN_URI="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${LOADGEN_JOB}:run"

if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
  --location "$SCHEDULER_LOCATION" \
  --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri "$RUN_URI" \
    --http-method POST \
    --oauth-service-account-email "$SERVICE_ACCOUNT_EMAIL" \
    --attempt-deadline "$ATTEMPT_DEADLINE" \
    --location "$SCHEDULER_LOCATION" \
    --project "$PROJECT_ID"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri "$RUN_URI" \
    --http-method POST \
    --oauth-service-account-email "$SERVICE_ACCOUNT_EMAIL" \
    --attempt-deadline "$ATTEMPT_DEADLINE" \
    --location "$SCHEDULER_LOCATION" \
    --project "$PROJECT_ID"
fi

echo "Scheduler job ready: ${SCHEDULER_JOB} (${SCHEDULE} ${TIME_ZONE})"
