# Evaluation Protocol — Serverless Load‑Shifting FaaS Setup

## 1. Purpose and Scope

This document defines a **complete, reproducible evaluation protocol** for assessing a serverless load‑shifting system with an AI‑based scheduler. It is written to be *operational*: a new group member should be able to prepare infrastructure, execute the experiments, and collect metrics **without additional design decisions**.

The evaluation is a **feasibility study**, not a production benchmark. The goals are:

* Demonstrate that spatio‑temporal scheduling is technically viable in a FaaS context
* Compare the AI‑based approach against two meaningful baselines
* Collect sufficient real Cloud Run metrics to support relative conclusions

The protocol intentionally prioritizes **simplicity, determinism, and low cost** while retaining scientific credibility.

---

## 2. Experimental Design Overview

### 2.1 Independent Variable

**Scheduling policy**, realized as three isolated scenarios:

| Scenario                                       | Description                                                                                 |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **A — Fixed Region**                           | All functions execute immediately in one pre‑selected region                                |
| **B — Lowest‑Carbon Region (Hourly Baseline)** | Functions execute immediately in the region with lowest carbon intensity for that hour      |
| **C — AI Agent**                               | Dispatcher routes execution to region and time selected by the AI agent (may include delay) |

Each scenario runs in its **own GCP project** to avoid interference and simplify metric attribution.

---

### 2.2 Controlled Variables

The following must be **identical across all scenarios**:

* Function source code and container images
* CPU / memory limits
* Concurrency limits
* Timeout configuration
* Input payloads and dataset
* Invocation frequency and mix ("workload trace")
* Cold‑start behavior (min instances = 0 everywhere)

---

### 2.3 Dependent Measurements

The evaluation relies on **real Cloud Run metrics**, collected via Google Cloud Monitoring and logs:

* Request count
* Request latency distribution
* CPU utilization
* Memory utilization
* Billable instance time
* Network bytes sent / received

No direct power or energy measurements are performed.

---

## 3. Workload Definition

### 3.1 Functions Under Test

Exactly **four functions** constitute the entire workload:

| Function                 | Characteristics                        |
| ------------------------ | -------------------------------------- |
| `api-health-check`       | Low compute, low data, high throughput |
| `crypto-key-gen`         | High compute, low data                 |
| `image-format-converter` | Moderate compute, moderate data        |
| `video-transcoder`       | High compute, high data                |

No additional workloads may be introduced.

---

### 3.2 Fixed Input Dataset

All data‑intensive functions use **the same immutable dataset**:

* One image file (e.g. PNG)
* One video‑like binary file

These objects:

* Are stored in **one GCS bucket**
* Located in **one fixed region**
* Are referenced **by GCS URI only** (no inline base64 during experiments)

The dataset must never change between scenarios.

---

### 3.3 Standardized Request Payloads

The following payloads are used for *all* invocations:

**Health Check**

```json
{"check":"ping"}
```

**Image Conversion**

```json
{"gcs_uri":"<IMAGE_URI>","format":"WEBP","quality":85}
```

**Crypto Key Generation**

```json
{"bits":4096}
```

**Video Transcoding**

```json
{"gcs_uri":"<VIDEO_URI>","passes":4}
```

Inline return (`return_inline`) is disabled for all experiments.

---

## 4. Invocation Schedule (Workload Trace)

### 4.1 Design Principles

The workload trace is:

* **Low‑throughput** (cost‑efficient)
* **Long‑running** (captures day/night and weekday effects)
* **Deterministic** (identical across scenarios)

Rather than a complex per‑request trace file, the experiment uses a **fixed hourly schedule**.

---

### 4.2 Hourly Invocation Mix

Each hour, the runner submits exactly:

| Function          | Invocations per hour |
| ----------------- | -------------------- |
| Health Check      | 20                   |
| Crypto Key Gen    | 3                    |
| Image Conversion  | 2                    |
| Video Transcoding | 1                    |

**Total:** 26 invocations per hour

This yields:

* 624 invocations per day
* 4,368 invocations per week

---

### 4.3 Intra‑Hour Timing

Within each hour:

* Invocations are distributed across fixed minute slots
* A small deterministic jitter (seconds) is added per invocation
* Jitter is derived from a stable hash of the invocation ID

This avoids synchronization artifacts while remaining fully reproducible.

---

## 5. Execution Infrastructure

### 5.1 Project Isolation

Each scenario runs in its **own GCP project**:

| Scenario | Project                 |
| -------- | ----------------------- |
| A        | `project-fixed-region`  |
| B        | `project-lowest-carbon` |
| C        | `project-ai-agent`      |

No resources are shared between projects.

---

### 5.2 Function Deployment

For **each project**:

* Deploy all four workload functions
* Deploy to all candidate regions
* Use identical service names in all regions
* Set `min-instances = 0`

Scenario C additionally deploys:

* The **dispatcher service**

---

### 5.3 Runner Architecture

Each project contains its **own runner**, implemented as:

* One **Cloud Run Job** (`loadgen-job`)
* Triggered **hourly** by Cloud Scheduler

The job:

1. Determines the current UTC hour
2. Generates the fixed hourly invocation mix
3. Sends requests according to the active scenario policy
4. Logs one structured line per invocation

The job terminates after completing the hourly batch.

---

## 6. Scenario‑Specific Execution Logic

### 6.1 Scenario A — Fixed Region

* All requests are sent directly to workload services in **one chosen region**
* No dispatcher is involved
* No execution delay is applied

---

### 6.2 Scenario B — Lowest‑Carbon Region (Hourly Baseline)

* A precomputed mapping exists: `hour → region`
* Mapping is derived from carbon‑intensity data **offline**
* For each hour, all requests go directly to the mapped region

Carbon data is **not queried live** during the experiment.

---

### 6.3 Scenario C — AI Agent

* The runner sends requests only to the **dispatcher**
* Payload includes:

  * function name
  * deadline (end of scheduling horizon)
  * invocation metadata

The dispatcher:

* Applies the AI scheduling policy
* Chooses execution region and time
* May enqueue delayed execution

---

## 7. Logging and Correlation

### 7.1 Invocation Identity

Every invocation carries:

* `experiment_id`
* `scenario`
* `event_id`
* `trace_hour`

These fields must appear in:

* Runner logs
* Dispatcher logs (scenario C)
* Function logs

---

### 7.2 Cold Starts

Cold starts are **intentionally included**:

* No pre‑warming
* No min instances
* Cold‑start effects are treated as part of system behavior

No attempt is made to isolate or remove them.

---

## 8. Metric Collection

### 8.1 Source of Metrics

Metrics are collected exclusively from:

* Google Cloud Monitoring (Cloud Run metrics)
* Cloud Run request logs

No client‑side latency measurements are required.

---

### 8.2 Metric Extraction Tool

The existing metrics tool:

* Takes service URLs and time windows as input
* Automatically extracts service name and region
* Outputs structured JSON with raw metrics

Each scenario produces **one metrics JSON file** covering the full experiment window.

---

### 8.3 Services Included per Scenario

* **Scenario A:** workload services in the fixed region
* **Scenario B:** workload services in all regions
* **Scenario C:** dispatcher + workload services in all regions

---

## 9. Evaluation Metrics

### 9.1 Time Metric (Speedup)

End‑to‑end execution time is approximated by:

* Request latency as reported by Cloud Run

For each scenario and function:

* Mean latency
* p95 latency

Speedup is computed **relatively** between scenarios.

---

### 9.2 Energy and Power Assumptions

Direct power measurement is infeasible. Therefore:

* Power per function is assumed **constant** across scenarios
* Resource configuration does not change

As a result:

* **Powerup ≈ 1** by design

---

### 9.3 Carbon‑Adjusted Energy (Greenup)

A carbon‑aware energy proxy is defined:

```
E* = T × CI(region, hour)
```

Where:

* `T` = execution time proxy
* `CI` = carbon intensity for the execution region and hour

Greenup is computed as the ratio of summed `E*` values between scenarios.

---

## 10. Execution Procedure

### 10.1 Preparation

1. Create three GCP projects
2. Deploy functions and dispatcher
3. Upload dataset to GCS
4. Deploy runner job and scheduler
5. Verify one dry‑run hour

---

### 10.2 Experiment Run

For each scenario:

1. Enable hourly scheduler
2. Run for the full experiment window (≥ 3 days, ideally 7)
3. Do not modify infrastructure during the run

Scenarios must not overlap in time.

---

### 10.3 Data Collection

After each scenario:

1. Export Cloud Run metrics for the full window
2. Store metrics JSON with scenario label
3. Optionally export request logs filtered by `experiment_id`

---

## 11. Validity and Limitations

* Results are **relative**, not absolute
* Network energy is not modeled explicitly
* Cold starts are included but not isolated
* Throughput is intentionally low

These limitations are acceptable for a feasibility study and must be stated explicitly in any report.

---

## 12. Expected Outcome

The evaluation should demonstrate:

* That scenario C can reduce carbon‑adjusted energy relative to A and/or B
* The latency trade‑offs introduced by delayed execution
* That real Cloud Run metrics are sufficient to support these conclusions

---

**End of Evaluation Protocol**
