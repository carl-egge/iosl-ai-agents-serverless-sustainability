#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./schedule_agent_daily.sh --agent-uri URI [options]

Required:
  --agent-uri URI                     Full Cloud Run service URL to invoke.

Options:
  --project PROJECT_ID                GCP project ID (default: gcloud config project)
  --region REGION                     Cloud Run region (default: us-east1)
  --location LOCATION                 Cloud Scheduler location (default: same as region)
  --service-name NAME                 Cloud Run service name (optional; auto-detect from URI)
  --service-account-email EMAIL       Service account to sign OIDC token
                                      (default: PROJECT_NUMBER-compute@developer.gserviceaccount.com)
  --job-name NAME                     Scheduler job name (default: agent-daily)
  --schedule CRON                     Cron schedule (default: "0 0 * * *")
  --time-zone TZ                      Time zone (default: "America/New_York")
  --attempt-deadline DURATION         Attempt deadline (default: 1800s)
  -h, --help                          Show help

Examples:
  ./schedule_agent_daily.sh --agent-uri https://agent-xyz-ue.a.run.app/run --project my-project
USAGE
}

PROJECT_ID="${PROJECT_ID:-}"
REGION="${REGION:-us-east1}"
SCHEDULER_LOCATION="${SCHEDULER_LOCATION:-}"
SERVICE_NAME="${SERVICE_NAME:-}"
AGENT_URI="${AGENT_URI:-}"
SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_EMAIL:-}"
JOB_NAME="${JOB_NAME:-agent-daily}"
SCHEDULE="${SCHEDULE:-0 0 * * *}"
TIME_ZONE="${TIME_ZONE:-America/New_York}"
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
    --service-name)
      SERVICE_NAME="$2"
      shift 2
      ;;
    --agent-uri)
      AGENT_URI="$2"
      shift 2
      ;;
    --service-account-email)
      SERVICE_ACCOUNT_EMAIL="$2"
      shift 2
      ;;
    --job-name)
      JOB_NAME="$2"
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

if [[ -z "$AGENT_URI" ]]; then
  echo "AGENT_URI is required. Pass --agent-uri or set AGENT_URI." >&2
  exit 1
fi

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
  SERVICE_ACCOUNT_EMAIL="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
fi

if [[ -z "$SERVICE_NAME" ]]; then
  SERVICE_NAME="$(gcloud run services list \
    --platform managed \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --format "value(metadata.name,status.url)" \
    | awk -v uri="$AGENT_URI" '$2 == uri { print $1; exit }')"
fi

if [[ -n "$SERVICE_NAME" ]]; then
  gcloud run services add-iam-policy-binding "$SERVICE_NAME" \
    --member "serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
    --role "roles/run.invoker" \
    --region "$REGION" \
    --project "$PROJECT_ID" >/dev/null
else
  echo "Warning: Could not resolve Cloud Run service name from AGENT_URI. Skipping run.invoker binding." >&2
fi

SCHEDULER_AGENT="service-${PROJECT_NUMBER}@gcp-sa-cloudscheduler.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding "$SERVICE_ACCOUNT_EMAIL" \
  --member "serviceAccount:${SCHEDULER_AGENT}" \
  --role "roles/iam.serviceAccountTokenCreator" \
  --project "$PROJECT_ID" >/dev/null

if gcloud scheduler jobs describe "$JOB_NAME" \
  --location "$SCHEDULER_LOCATION" \
  --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB_NAME" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri "$AGENT_URI" \
    --http-method POST \
    --oidc-service-account-email "$SERVICE_ACCOUNT_EMAIL" \
    --attempt-deadline "$ATTEMPT_DEADLINE" \
    --location "$SCHEDULER_LOCATION" \
    --project "$PROJECT_ID"
else
  gcloud scheduler jobs create http "$JOB_NAME" \
    --schedule "$SCHEDULE" \
    --time-zone "$TIME_ZONE" \
    --uri "$AGENT_URI" \
    --http-method POST \
    --oidc-service-account-email "$SERVICE_ACCOUNT_EMAIL" \
    --attempt-deadline "$ATTEMPT_DEADLINE" \
    --location "$SCHEDULER_LOCATION" \
    --project "$PROJECT_ID"
fi

echo "Scheduler job ready: ${JOB_NAME} (${SCHEDULE} ${TIME_ZONE})"
