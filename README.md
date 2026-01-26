# WIP: Integrating AI Agents into Serverless Platforms for Enhanced Sustainability

## 📘 Project Overview
This project explores how **AI agents** can improve the **sustainability and efficiency** of **serverless computing platforms** such as AWS Lambda, Google Cloud Functions, or OpenFaaS.
Serverless computing simplifies deployment and scaling, but its growing adoption also increases energy consumption and environmental impact.
By integrating **intelligent, autonomous AI agents**, we aim to optimize resource allocation, deployment decisions, and code execution to achieve **better energy efficiency and performance**.

## 🎯 Objectives
- **Understand** how serverless workflows operate and where optimization opportunities exist.
- **Design and implement** AI agent(s) that support sustainability-oriented decisions in serverless environments.
- **Measure and evaluate** the effects of AI integration on resource utilization, performance, and energy efficiency.

## 🧩 Project Phases

### 1. Research & Brainstorming
- Study the **serverless computing model** and typical workflows.
- Explore **Agentic AI principles** and agent design patterns.
- Define the **goals and roles** of the proposed agent(s).
- Determine **integration points** between serverless infrastructure and AI components.
- Identify **key evaluation metrics** (e.g., latency, energy use, cost efficiency).
- Research available **tools and data sources** for collecting metrics.
- Explore **AI models** and frameworks suitable for reasoning, decision-making, or optimization.
- Ensure all proposals are **supported by research** and credible sources.

### 2. Design
- Define the **architecture** of the system (serverless + AI integration).
- Plan the **experimental setup** for testing and measuring performance.

### 3. Implementation
- Build the prototype or proof of concept.
- Run experiments and **evaluate results** based on defined metrics.
- Document findings and discuss sustainability implications.

## 🧠 Key Concepts
- **Serverless Computing (FaaS):** A cloud model where functions are executed on demand without managing infrastructure.
- **AI Agents:** Autonomous systems capable of perception, reasoning, and action to achieve defined goals.
- **Sustainability in Cloud Computing:** Efforts to minimize environmental impact by optimizing energy and resource consumption.

## 📚 Useful References
- [OpenFaaS](https://www.openfaas.com/)
- [MCP Protocol](https://modelcontextprotocol.io/docs/getting-started/intro)
- [LangChain](https://www.langchain.com/)
- [Agentic Design Principles](https://docs.google.com/document/d/1rsaK53T3Lg5KoGwvf8ukOUvbELRtH-V0LnOIFDxBryE/mobilebasic)


# Carbon-Aware Serverless Function Scheduler

An AI-powered scheduler that optimizes when and where to run serverless functions based on real-time carbon intensity forecasts and data transfer costs. Works both locally and in Google Cloud Run.

## Features
- **Carbon-aware scheduling**: Uses Electricity Maps API for real-time carbon intensity forecasts
- **Cost optimization**: Calculates data transfer costs between regions
- **AI-powered decisions**: Google Gemini analyzes trade-offs and generates schedules
- **Dual mode**: Works locally for testing and in Cloud Run for production
- **Natural language support**: Define functions with plain English descriptions
- **Multi-function scheduling**: Process multiple functions with region filtering

## Prerequisites
- Python 3.10+
- API Keys:
  - `ELECTRICITYMAPS_TOKEN` - [Get from Electricity Maps](https://www.electricitymaps.com/)
  - `GEMINI_API_KEY` - [Get from Google AI Studio](https://aistudio.google.com/app/apikey)

## Local Setup

### 1. Clone and Install
```bash
git clone <repository-url>
cd iosl-ai-agents-serverless-sustainability

# Create conda environment
conda env create -f environment.yml
conda activate iosl-faas-scheduling
```

### 2. Configure Environment
```bash
cp .env.example .env
# Edit .env and add your API keys:
# GEMINI_API_KEY=your-gemini-key
# ELECTRICITYMAPS_TOKEN=your-electricitymaps-token
```

### 3. Configure Functions
Edit `local_bucket/function_metadata.json` to define functions you want to schedule. Supports both structured JSON with explicit parameters or natural language descriptions.

### 4. Run Locally
```bash
python src/agent/agent.py
```

This will:
- Load config from `local_bucket/`
- Fetch real-time carbon forecasts
- Generate optimized schedules
- Save results to `local_bucket/schedule_*.json`

## OpenTofu Deployment
We provide a `main.tf` file to deploy the project to the Google Cloud Platform using OpenTofu.

The deployment needs a file containing various environment variables, called terraform.tfvars, in the root directory. It can be set up using the provided [example file](terraform.tfvars.example)

To deploy via OpenTofu you need to set up a gcloud project, and the gcloud-cli:
1. [Install gcloud-cli](https://docs.cloud.google.com/sdk/docs/install-sdk)
1. `gcloud  init`

To deploy to GCP with OpenTofu follow these steps

1. [Install OpenTofu](https://opentofu.org/docs/intro/install/)
1. `tofu init`
1. `tofu plan`
1. `tofu deploy`

To tear down the infrastructure use `tofu destroy`

## Google Cloud Run Deployment

See [src/agent/gcp_deploy/README.md](src/agent/gcp_deploy/README.md) for complete deployment instructions.

**Quick deploy:**
```bash
gcloud run deploy agent \
  --source src/agent/gcp_deploy \
  --region us-east1 \
  --timeout=300 \
  --allow-unauthenticated \
  --set-env-vars GCS_BUCKET_NAME=your-bucket \
  --set-secrets ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest
```

## Dispatcher

The dispatcher schedules functions at optimal times based on generated schedules.

## Configuration Files

### static_config.json
Contains:
- GCP region mappings to Electricity Maps zones
- Data transfer costs per region
- Pricing tiers and GPU availability
- Power consumption constants

### function_metadata.json
Defines functions to schedule with:
- **Structured format**: Explicit parameters (runtime, memory, etc.)
- **Natural language**: AI extracts parameters automatically

## API Endpoints (Cloud Run)

- **GET /health** - Health check endpoint
- **POST /run** - Triggers scheduler for all functions in `function_metadata.json`

## Output Files

After running, check `local_bucket/` (or GCS bucket in cloud):
- `carbon_forecasts.json` - Raw carbon intensity data for all regions
- `schedule_<function>.json` - Optimized schedule for each function with:
  - Recommended execution times and regions
  - Carbon intensity at each time
  - Data transfer costs
  - Priority rankings
  - Reasoning for each recommendation

## License

This project will adopt an open-source license (to be decided).

## Contributors

List of team members and roles will be added here as the project progresses.
