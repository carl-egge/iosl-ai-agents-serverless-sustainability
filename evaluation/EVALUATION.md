# Evaluation Protocol — Serverless Load-Shifting Experiment

## 1. Purpose and Scope

This document defines the **experiment protocol** for evaluating a serverless load-shifting system with an AI-based scheduler. It covers experiment design, infrastructure setup, and execution procedures.

**Goals:**
- Demonstrate that spatio-temporal scheduling is technically viable in a FaaS context
- Compare the AI-based approach against two meaningful baselines
- Collect sufficient real Cloud Run metrics to support conclusions

This is a **feasibility study**, not a production benchmark. The protocol prioritizes **simplicity, determinism, and low cost** while retaining scientific credibility.

**For calculation methodology, formulas, and constants, see [METRICS.md](METRICS.md).**

---

## 2. Experimental Design

### 2.1 Scheduling Approaches

Each GCP project represents a different scheduling approach. All approaches use **instant execution** except Agent, which may delay execution.

| Approach | GCP Project | Execution Region | Description |
|----------|-------------|------------------|-------------|
| **Baseline** | `project-baseline` | us-east1 (home) | All functions execute immediately in home region |
| **Static Green** | `project-static-green` | europe-north2 (GPU: europe-west1) | All functions execute immediately in greenest region |
| **Agent** | `project-ai-agent` | Dynamic | Dispatcher routes to region/time based on AI scheduling |

**Home region:** us-east1 — where GCS bucket is stored and all requests originate from. This ensures comparable network conditions across scenarios.

All approaches use identical workload traces, function configurations, and code. No resources are shared between projects.

### 2.2 Candidate Regions

Functions are deployed to **5 regions** (GCP quota limit):

| Region | Location | GPU | Carbon Profile | Notes |
|--------|----------|-----|----------------|-------|
| **us-east1** | South Carolina | No | Medium-high | Home region (bucket location) |
| **us-central1** | Iowa | Yes | Medium | GPU-capable US region |
| **northamerica-northeast1** | Montreal | No | Very low | Green region (hydro power) |
| **europe-north2** | Stockholm | No | Very low | Green region (Nordic grid) |
| **europe-west1** | Belgium | Yes | Medium | GPU-capable EU region |

**Rationale:**
- **Geographic diversity** — North America and Europe coverage
- **GPU availability** — One GPU region per continent (us-central1, europe-west1)
- **Carbon diversity** — Mix of green (Montreal, Stockholm) and medium-carbon regions
- **Home region baseline** — us-east1 is intentionally not the greenest, demonstrating the benefit of region shifting
- **Quota compliance** — 5 regions stays within GCP deployment limits

This setup enables the Agent to make diverse scheduling decisions across regions, showcasing the potential of spatio-temporal load shifting.

---

## 3. Workload Definition

### 3.1 Functions Under Test

Exactly **four functions** constitute the entire workload:

| Function | Characteristics |
|----------|-----------------|
| `api-health-check` | Low compute, low data, high throughput |
| `crypto-key-gen` | High compute, low data |
| `image-format-converter` | Moderate compute, moderate data |
| `video-transcoder` | High compute, high data (GPU-capable) |

No additional workloads may be introduced.

### 3.2 Fixed Input Dataset

All data-intensive functions use **the same immutable dataset**:
- One image file (e.g., PNG)
- One video-like binary file

These objects:
- Are stored in **GCS bucket in us-east1** (home region)
- Are referenced **by GCS URI only** (no inline base64 during experiments)
- Must never change between scenarios

### 3.3 Standardized Request Payloads

**Health Check**
```json
{"check": "ping"}
```

**Crypto Key Generation**
```json
{"bits": 4096}
```

**Image Conversion**
```json
{"gcs_uri": "<IMAGE_URI>", "format": "WEBP", "quality": 85}
```

**Video Transcoding**
```json
{"gcs_uri": "<VIDEO_URI>", "passes": 2}
```

Inline return (`return_inline`) is disabled for all experiments.

---

## 4. Invocation Schedule

### 4.1 Design Principles

The workload trace is:
- **Constant daily load** — same invocations every day for consistent per-invocation metrics
- **Long-running** — 7 days captures day/night and weekday effects
- **Deterministic** — identical across scenarios

We use a reduced invocation count for cost efficiency. Mean values (CPU utilization, latency, etc.) over these runs should not change significantly with more invocations per day, making this approach suitable for estimating per-invocation metrics.

### 4.2 Hourly Schedule

Each function is invoked **once per working hour** (08:00–18:00 UTC):

| Function | Invocations/hour | Invocations/day |
|----------|------------------|-----------------|
| api-health-check | 1 | 10 |
| crypto-key-gen | 1 | 10 |
| image-format-converter | 1 | 10 |
| video-transcoder | 1 | 10 |

**Total:** 4 invocations per hour, **40 invocations per day**, **280 invocations per week**

### 4.3 Special Functions (Agent Scenario Only)

- **Dispatcher**: Called for every function invocation (40 calls/day)
- **Agent**: Runs once daily to generate the scheduling plan

---

## 5. Infrastructure Setup

### 5.1 Function Deployment

For **each project**:
- Deploy all four workload functions to all 5 candidate regions
- Use identical service names in all regions
- Set `min-instances = 0`

Agent scenario additionally deploys:
- The **dispatcher service**
- The **agent function** (triggered daily)

### 5.2 Runner Architecture

Each project contains its **own runner**, implemented as:
- One **Cloud Run Job** (`loadgen-job`)
- Triggered **hourly** by Cloud Scheduler (08:00–18:00 UTC)

The job:
1. Determines the current UTC hour
2. Invokes each function once according to scenario policy
3. Terminates after completing the hourly batch

### 5.3 Scenario-Specific Execution Logic

**Baseline:**
- All requests sent directly to workload services in **us-east1**
- No dispatcher involved
- Immediate execution

**Static Green:**
- All requests sent directly to workload services in **europe-north2**
- GPU functions (video-transcoder) use **europe-west1**
- No dispatcher involved
- Immediate execution

**Agent:**
- Runner sends requests only to the **dispatcher**
- Dispatcher applies AI scheduling policy
- May route to different regions or delay execution based on carbon forecasts

---

## 6. Experiment Assumptions

| Assumption | Description | Effect |
|------------|-------------|--------|
| Agent Scheduling Frequency | Agent runs once per day | Agent costs scale × 365 yearly |
| Agent API Call Frequency | Gemini/ElectricityMaps APIs called once per week | API costs scale × 52 yearly |
| Dispatcher Call Frequency | Dispatcher called per invocation | Dispatcher costs scale × annual_invocations |
| Stable Function Metadata | No config changes during experiment | Fair comparison across scenarios |
| No User Priority Changes | Fixed priority weights | Consistent agent behavior |
| Constant Load Pattern | Same schedule every day | Simple yearly scaling |
| Cold Starts Included | No warmup exclusion | Reflects real system behavior |

**For calculation-related assumptions (power models, carbon intensity, etc.), see [METRICS.md](METRICS.md).**

---

## 7. Execution Procedure

### 7.1 Preparation

1. Create three GCP projects
2. Deploy functions to all 5 regions (and dispatcher for Agent scenario)
3. Upload dataset to GCS in us-east1
4. Deploy runner job and Cloud Scheduler
5. Verify one dry-run hour

### 7.2 Experiment Run

For each scenario:
1. Enable hourly scheduler
2. Run for **7 days** (full experiment window)
3. Do not modify infrastructure during the run

Scenarios **may run in parallel** (separate projects) or sequentially.

### 7.3 Data Collection

> **Status:** Work in Progress

After each scenario:
1. xxx

**Tools:**
- GCP metrics extraction: `evaluation/gcp_metrics/fetch_gcp_metrics.py`
- Final metrics calculation: `evaluation/final_metrics/calculate.py`
- xxx

For detailed methodology, see [METRICS.md](METRICS.md).

---

## 8. Limitations

- **Assumptions constrain generalizability** — see Section 6 and [METRICS.md](METRICS.md) for full list
- **Power cannot be directly measured** — we use Cloud Carbon Footprint methodology with constant values; costs are calculated by our own framework. Absolute values may not be 100% accurate, but since we use the same logic for all approaches, results are comparable across projects — sufficient for the scope of this feasibility study.
- **Limited experiment duration** — 7 days of data scaled to yearly projections
- **Constant daily load** — we use a fixed invocation count per day to estimate per-invocation values, rather than actual production traffic. This is cost-efficient and adequate because mean metric values (CPU utilization, latency, etc.) should remain rather stable with larger invocation volume.

These limitations are acceptable for a feasibility study and must be stated explicitly in any report.

---

## 9. Expected Outcome

*[PLACEHOLDER — to be filled after experiments]*

---

## References

- [METRICS.md](METRICS.md) — Calculation methodology, formulas, constants, and tools

---

**Document Status:** Work in Progress
**Last Updated:** 2026-01-20
