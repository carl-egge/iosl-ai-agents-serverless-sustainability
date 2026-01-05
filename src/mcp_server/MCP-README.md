# Agent with MCP

Workflow with MCP:

When the agent's `/run` endpoint is called:

```
1. Load function metadata
   - Read function_metadata.json from GCS bucket
   - Parse natural language descriptions (if any) using Gemini

2. Check cached schedules
   - For each function, check if a valid cached schedule exists
   - Cache is valid if: metadata unchanged AND age < 7 days

3. Determine regions to fetch
   - Collect allowed regions from all functions needing new schedules
   - Apply GPU filtering (if gpu_required=true)
   - Apply latency filtering (if latency_important=true)

4. Fetch carbon intensity forecasts
   - Query Electricity Maps API for 24-hour forecasts
   - Get data for all required regions

5. Generate schedules
   - Use Gemini to select optimal region/time combinations
   - Consider: carbon intensity, transfer costs, priority setting
   - Save schedule_<function_name>.json to GCS

6. Deploy functions to optimal regions using MCP 
   - For each function with code in metadata:
     - Check if already deployed (via code hash comparison)
     - If new/changed: deploy to optimal region via MCP server
     - Update schedule.json with deployment info (function_url)
   - Skip deployment if function already active with same code
```

## API Endpoints

### GET /health
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "service": "agent",
  "mode": "CLOUD",
  "bucket": "faas-scheduling-us-east1",
  "has_emaps_token": true,
  "has_gemini_key": true,
  "mcp_server_url": "https://mcp-function-deployer-xxx.run.app",
  "has_mcp_api_key": true
}
```

### POST /run
Trigger the carbon-aware scheduler for all functions in function_metadata.json.

**Request:**
```bash
curl -X POST https://agent-xxx.run.app/run \
  -H "Content-Type: application/json" \
  -d "{}"
```

**Response:**
```json
{
  "status": "success",
  "message": "Carbon-aware schedules generated and functions deployed",
  "forecast_location": "gs://bucket/carbon_forecasts.json",
  "functions": {
    "write_to_bucket": {
      "status": "success",
      "schedule_location": "gs://bucket/schedule_write_to_bucket.json",
      "top_5_recommendations": [...],
      "total_recommendations": 24,
      "deployment": {
        "deployed": true,
        "reason": "new_function",
        "function_url": "https://write-to-bucket-xxx.run.app",
        "region": "northamerica-northeast1"
      }
    }
  }
}
```

### POST /submit
Submit a new function for one-time carbon-aware deployment.

for ad-hoc deployments only. The function is NOT added to function_metadata.json and will not be included in future /run calls.

**Request:**
```bash
curl -X POST https://agent-xxx.run.app/submit \
  -H "Content-Type: application/json" \
  -d '{
    "code": "def handler(payload): return {\"result\": payload.get(\"x\", 0) * 2}",
    "deadline": "2026-01-05T18:00:00Z",
    "memory_mb": 256
  }'
```

## Ways to Call the Agent in Google Cloud

### 1. Direct HTTP Request
```bash
curl -X POST https://agent-752774698672.us-east1.run.app/run \
  -H "Content-Type: application/json" \
  -d "{}"
```

## Quick Start Guide

### Step 1: Configure function_metadata.json

Add function to `local_bucket/function_metadata.json`:

```json
{
  "functions": {
    "my_function": {
      "function_id": "my_function",
      "runtime_ms": 1000,
      "memory_mb": 256,
      "description": "My custom function",
      "gpu_required": false,
      "data_input_gb": 0.001,
      "data_output_gb": 0.001,
      "source_location": "us-east1",
      "invocations_per_day": 100,
      "allowed_regions": ["us-east1", "us-central1", "europe-west1"],
      "code": "def handler(payload):\n    return {\"message\": \"Hello from \" + payload.get(\"name\", \"World\")}",
      "requirements": ""
    }
  }
}
```

### Step 2: Upload to GCS

```bash
gsutil cp local_bucket/function_metadata.json gs://faas-scheduling-us-east1/
```

### Step 3: Call the Agent

```bash
curl -X POST https://agent-752774698672.us-east1.run.app/run \
  -H "Content-Type: application/json" \
  -d "{}"
```

### Step 4: Check the Response

The response includes:
- Schedule recommendations (24 time slots with optimal regions)
- Deployment status (function URL if deployed)

### Step 5: View the Schedule

```bash
gsutil cat gs://faas-scheduling-us-east1/schedule_my_function.json
```

The schedule includes:
```json
{
  "function_name": "my_function",
  "recommendations": [...],
  "deployment": {
    "function_url": "https://my-function-xxx.run.app",
    "region": "us-east1",
    "deployed_at": "2026-01-05T17:00:00"
  }
}
```

## MCP Server

The MCP (Model Context Protocol) Server is a separate Cloud Run service that handles the actual deployment of functions to Google Cloud Functions. The Agent communicates with it via JSON-RPC over HTTP.

### How the MCP Server Works

```
Agent                           MCP Server                      GCP
  |                                 |                            |
  |  1. deploy_function(code,region)|                            |
  |-------------------------------->|                            |
  |                                 |  2. Create zip archive     |
  |                                 |     (main.py + requirements)|
  |                                 |                            |
  |                                 |  3. Upload to GCS          |
  |                                 |--------------------------->|
  |                                 |                            |
  |                                 |  4. Create/Update Function |
  |                                 |--------------------------->|
  |                                 |                            |
  |                                 |  5. Wait for deployment    |
  |                                 |<---------------------------|
  |                                 |                            |
  |  6. Return function_url         |                            |
  |<--------------------------------|                            |
```

### MCP Server Tools

The MCP Server exposes these tools via the `/mcp` endpoint:

| `deploy_function` | Deploy Python code as a Cloud Function |
| `get_function_status` | Check if a function exists and its state |
| `delete_function` | Remove a deployed function |
| `generate_function_name` | Generate a unique UUID-based name |

### Agent-MCP Interaction

When the Agent deploys a function, it:

1. **Calls `get_function_status`** to check if the function already exists
2. **Compares code hash** to detect if the code has changed
3. **Calls `deploy_function`** if deployment is needed, passing:
   - `function_name`: The function ID from metadata
   - `code`: The Python code to deploy
   - `region`: The optimal region from schedule
   - `memory_mb`, `timeout_seconds`, `requirements`: From metadata

The MCP Server then:

1. **Wraps the code** with a `functions_framework` decorator
2. **Creates a zip archive** with `main.py` and `requirements.txt`
3. **Uploads to GCS** at `gs://bucket/function-source/{name}/{name}.zip`
4. **Deploys via Cloud Functions v2 API**
5. **Returns the function URL** (e.g., `https://func-xxx.run.app`)

### MCP Server Configuration

| Variable | Description |
|----------|-------------|
| PROJECT_ID | GCP project ID |
| GCS_BUCKET | Bucket for function source code |
| MCP_API_KEY | API key for authentication |

### Calling the MCP Server Directly

```bash
# List available tools
curl -X POST https://mcp-function-deployer-xxx.run.app/mcp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}'

# Deploy a function
curl -X POST https://mcp-function-deployer-xxx.run.app/mcp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "jsonrpc":"2.0",
    "method":"tools/call",
    "params":{
      "name":"deploy_function",
      "arguments":{
        "function_name":"my-test-func",
        "region":"us-east1",
        "code":"def handler(payload): return {\"hello\": \"world\"}"
      }
    },
    "id":1
  }'

# Check function status
curl -X POST https://mcp-function-deployer-xxx.run.app/mcp \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "jsonrpc":"2.0",
    "method":"tools/call",
    "params":{
      "name":"get_function_status",
      "arguments":{
        "function_name":"my-test-func",
        "region":"us-east1"
      }
    },
    "id":1
  }'
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| GCS_BUCKET_NAME | GCS bucket for configs and schedules |
| MCP_SERVER_URL | URL of the MCP server |
| ELECTRICITYMAPS_TOKEN | Electricity Maps API token (secret) |
| GEMINI_API_KEY | Google Gemini API key (secret) |
| MCP_API_KEY | API key for MCP server auth (secret) |

## Deployment Commands

### Full Deployment
```bash
./scripts/deploy-to-gcp.sh
```

### Agent Only
```bash
cd src/agent
cp agent.py mcp_client.py prompts.py gcp_deploy/

cd gcp_deploy
gcloud run deploy agent \
  --source . \
  --region us-east1 \
  --service-account agent-scheduler@iosl-faas-scheduling.iam.gserviceaccount.com \
  --allow-unauthenticated \
  --memory 1Gi \
  --timeout 300 \
  --set-env-vars "GCS_BUCKET_NAME=faas-scheduling-us-east1,MCP_SERVER_URL=https://mcp-function-deployer-752774698672.us-east1.run.app" \
  --set-secrets "ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest,MCP_API_KEY=MCP_API_KEY:latest"
```

## Viewing Logs

```bash
gcloud run services logs read agent --region us-east1 --limit 100
```