#!/bin/bash
#
# Deploy MCP Function Deployer to Cloud Run
#
# Usage:
#   ./deploy.sh
#
# Prerequisites:
#   - gcloud authenticated: gcloud auth login
#   - Project set: gcloud config set project YOUR_PROJECT
#

set -e

# Configuration
SERVICE_NAME="mcp-function-deployer"
REGION="${REGION:-us-east1}"
PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project)}"
GCS_BUCKET="${GCS_BUCKET:-faas-scheduling-${REGION}}"
SA_NAME="mcp-deployer"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "============================================"
echo "Deploying MCP Function Deployer"
echo "============================================"
echo "  Project:  $PROJECT_ID"
echo "  Region:   $REGION"
echo "  Service:  $SERVICE_NAME"
echo "  Bucket:   $GCS_BUCKET"
echo "============================================"
echo ""

# Check if service account exists
if ! gcloud iam service-accounts describe $SA_EMAIL &>/dev/null; then
    echo "Creating service account: $SA_NAME"
    gcloud iam service-accounts create $SA_NAME \
        --display-name="MCP Function Deployer"

    # Grant necessary permissions
    for role in roles/cloudfunctions.developer roles/storage.objectAdmin roles/iam.serviceAccountUser; do
        gcloud projects add-iam-policy-binding $PROJECT_ID \
            --member="serviceAccount:$SA_EMAIL" \
            --role="$role" \
            --quiet > /dev/null
    done
fi

# Check if MCP_API_KEY secret exists
if ! gcloud secrets describe MCP_API_KEY &>/dev/null; then
    echo ""
    echo "Creating MCP_API_KEY secret..."
    read -sp "Enter API key for MCP server: " api_key
    echo
    echo -n "$api_key" | gcloud secrets create MCP_API_KEY --data-file=-

    # Grant access to service account
    gcloud secrets add-iam-policy-binding MCP_API_KEY \
        --member="serviceAccount:$SA_EMAIL" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet > /dev/null
fi

echo ""
echo "Deploying to Cloud Run..."

gcloud run deploy $SERVICE_NAME \
    --source . \
    --region $REGION \
    --service-account $SA_EMAIL \
    --allow-unauthenticated \
    --memory 1Gi \
    --cpu 1 \
    --timeout 300 \
    --min-instances 0 \
    --max-instances 10 \
    --set-env-vars "PROJECT_ID=$PROJECT_ID,GCS_BUCKET=$GCS_BUCKET" \
    --set-secrets "MCP_API_KEY=MCP_API_KEY:latest"

# Get the service URL
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format='value(status.url)')

echo ""
echo "============================================"
echo "Deployment complete!"
echo "============================================"
echo ""
echo "Service URL: $SERVICE_URL"
echo ""
echo "Test commands:"
echo ""
echo "  # Health check"
echo "  curl $SERVICE_URL/health"
echo ""
echo "  # List tools"
echo "  curl -X POST $SERVICE_URL/mcp \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'X-API-Key: YOUR_API_KEY' \\"
echo "    -d '{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\",\"id\":1}'"
echo ""
