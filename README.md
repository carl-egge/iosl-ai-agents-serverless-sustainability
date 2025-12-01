# Integrating AI Agents into Serverless Platforms for Enhanced Sustainability

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

An AI-assisted scheduler that recommends when and where to run serverless functions based on carbon intensity forecasts. The shared planner powers both local runs and a Google Cloud Run deployment.

## Repository Layout
```
README.md
.env.example
src/
  agent/                 # Shared planner logic (local + GCP)
  dispatcher/            # Placeholder for future dispatch logic
deployments/
  gcp/                   # Cloud Run entrypoint + Procfile + deps
  local/                 # Local runner entrypoint + helper script
data/sample/             # Example function metadata and sample outputs
scripts/                 # Utility/test scripts for APIs and regions
tests/                   # (empty placeholder)
```

## Prerequisites
- Python 3.10+ recommended
- Keys: `ELECTRICITYMAPS_TOKEN`, `GEMINI_API_KEY`
- (Local) `python-dotenv` for loading `.env`

## Setup & Local Run
1) Copy env template and fill in your keys:
```
cp .env.example .env
```
2) Install dependencies (reuse the GCP requirements plus dotenv):
```
python -m venv .venv
.\.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r deployments/gcp/requirements.txt python-dotenv
```
3) Run the planner from the repo root:
```
python deployments/local/run_agent.py
```
Outputs (schedule + forecasts) are written to `data/sample/` and summarized in the console. The PowerShell helper `deployments/local/run_agent_local.ps1` runs the same entrypoint.

## Google Cloud Run Deployment
- Entry module: `deployments/gcp/main.py`
- Procfile: `deployments/gcp/Procfile`
- Requirements: `deployments/gcp/requirements.txt`

Deploy (see `deployments/gcp/README.md` for full steps, secrets, and troubleshooting):
```
gcloud run deploy agent \
  --source . \
  --region <region> \
  --allow-unauthenticated \
  --timeout=300 \
  --set-env-vars GCS_BUCKET_NAME=<your-bucket> \
  --set-secrets ELECTRICITYMAPS_TOKEN=ELECTRICITYMAPS_TOKEN:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest
```

## Data & Samples
- `data/sample/function_metadata.json` — sample function metadata the planner reads.
- `data/sample/carbon_forecasts.json` / `execution_schedule.json` — sample outputs (overwritten by new runs).
- `data/sample/all_regions_forecast.json` — produced by `scripts/test_all_regions.py`.

## Utility Scripts (run from repo root)
- `python scripts/test_all_regions.py` — fetch forecasts for many zones; writes to `data/sample/all_regions_forecast.json`.
- `python scripts/test_electricitymaps.py` — quick Electricity Maps key check (update inline `API_TOKEN` first).
- `python scripts/test_gemini.py` — list available Gemini models (uses `GEMINI_API_KEY` or CLI arg).

## Notes
- Core planner lives in `src/agent/planner.py`; entrypoints are thin wrappers.
- Ensure `.env` or environment variables are set before running locally.
- Cloud Run Flask app is created via `create_gcp_app()` in `src/agent/planner.py` and referenced by the Procfile.


## License

This project will adopt an open-source license (to be decided).

## Contributors

List of team members and roles will be added here as the project progresses.