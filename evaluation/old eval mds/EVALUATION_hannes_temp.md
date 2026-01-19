# Evaluation Protocol — Energy Efficiency Metrics for Serverless Load-Shifting Agent

## 1) Overview & Objective

This document defines the evaluation methodology for comparing scheduling approaches for serverless sustainability.

**Goal:** Quantify the sustainability and cost efficiency of different scheduling approaches using four metrics:
- **Latency** — end-to-end response time
- **Energy** — total energy consumption (kWh)
- **Emissions** — carbon footprint (gCO2)
- **Cost Overhead** — additional costs compared to baseline ($)

---

## 2) Approaches

Each GCP project represents a different scheduling approach:

| Approach | Description |
|----------|-------------|
| **Baseline** | All functions execute in home region (us-east1) |
| **Static Green** | All functions execute in generally greenest region |
| **Agent** | Dynamic scheduling based on carbon intensity forecasts, user priorities, and function metadata |

All approaches use identical workload traces, function configurations, and code.

---

## 3) Metrics — Definitions & Calculations

### 3.1) Absolute Metrics (Per-Invocation)

#### Latency (ms)

End-to-end time from request to completion, including queue delays and network latency.

**Measurement:** Directly measured by loadgen tool (not calculated).

---

#### Energy (kWh)

Reference: [Cloud Carbon Footprint methodology](https://www.cloudcarbonfootprint.org/docs/methodology/)

**Power consumption:**
```
cpu_power_w      = vcpus × (0.71 + cpu_utilization × 3.55) W     # CCF min/max model
memory_power_w   = memory_gib × 0.4 W/GiB
gpu_power_w      = gpu_count × 72W × 0.8                         # if GPU required
total_power_w    = cpu_power_w + memory_power_w + gpu_power_w
```

**Energy per invocation:**
```
compute_energy_kwh = total_power_w × (runtime_s / 3600) × PUE
network_energy_kwh = (data_received_gb + data_sent_gb) / request_count × 0.001 kWh/GB
total_energy_kwh   = compute_energy_kwh + network_energy_kwh
```

Notes:
- CPU power uses CCF **min/max model** with **actual measured utilization** from GCP Cloud Monitoring
  - Formula: `vcpus × (min_watts + cpu_util × (max_watts - min_watts))`
  - GCP values from SPECPower: min=0.71W, max=4.26W per vCPU (delta=3.55W)
  - At 50% utilization: 0.71 + 0.5 × 3.55 = 2.485 W/vCPU
- Memory power uses **allocated capacity** (allocation-based, not utilization-based) — DRAM refresh power is independent of access patterns
- PUE (Power Usage Effectiveness) = 1.1 (Google datacenter efficiency)

---

#### Emissions (gCO2)

```
total_emissions_g = total_energy_kwh × carbon_intensity_g_per_kwh
```

Carbon intensity sourced from [ElectricityMaps API](https://portal.electricitymaps.com/docs) based on region and execution time.

---

#### Cost Overhead ($)

Additional costs compared to baseline (executing in home region with no agent).

**Components:**
1. **Transfer costs** — regional data transfer vs home region
2. **Agent architecture costs** (agent scenario only) — execution, request, and API costs

Reference: [GCP Cloud Storage pricing](https://cloud.google.com/storage/pricing#network-buckets)

```
transfer_cost_usd      = transfer_gb × region_rate_per_gb
total_cost_overhead    = transfer_cost + agent_architecture_costs
```

| Region | Transfer Rate |
|--------|---------------|
| us-east1 (home) | $0/GB |
| North America | $0.02/GB |
| Europe | $0.05/GB |

---

### 3.2) Yearly Scaling

```
annual_invocations = invocations_per_day × 365

annual_energy_kwh      = per_invocation_energy × annual_invocations
annual_emissions_kg    = (per_invocation_emissions_g × annual_invocations) / 1000
annual_transfer_cost   = per_invocation_transfer × annual_invocations
```

**Agent architecture scaling (different frequencies):**
- Dispatcher costs: per invocation → × annual_invocations
- Agent execution: daily → × 365
- Agent API calls (Gemini + ElectricityMaps): weekly → × 52

**Latency:** Mean from measurements (NOT scaled)

---

### 3.3) Aggregation

```
Project_Total = Σ(Function_Annual_Values)
```

Sum all function yearly values within a project for final comparison.

---

### 3.4) Relative Metrics (Ratios vs Baseline)

Reference: Abdulsalam et al. (2015) IEEE IGSC — "Using the Greenup, Powerup, and Speedup metrics to evaluate software energy efficiency"

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **Speedup** | `Latency_baseline / Latency_approach_i` | >1 = faster than baseline |
| **Powerup** | `Energy_baseline / Energy_approach_i` | >1 = less energy than baseline |
| **Greenup** | `Emissions_baseline / Emissions_approach_i` | >1 = lower carbon than baseline |
| **Cost Overhead** | Absolute $ value | Baseline = $0 by definition |

---

## 4) Constants

Reference: [Cloud Carbon Footprint methodology](https://www.cloudcarbonfootprint.org/docs/methodology/)

| Constant | Value | Source |
|----------|-------|--------|
| CPU power (min) | 0.71 W/vCPU | CCF/SPECPower (GCP idle) |
| CPU power (max) | 4.26 W/vCPU | CCF/SPECPower (GCP 100% load) |
| Memory power | 0.4 W/GiB | Cloud Carbon Footprint (~0.392 W/GB industry standard) |
| GPU power (L4) | 72W TDP × 0.8 utilization | NVIDIA spec |
| Network energy | 0.001 kWh/GB | Cloud Carbon Footprint (hyperscale optical fiber) |
| Datacenter PUE | 1.1 | Google reported efficiency |

All constants stored in `local_bucket/static_config.json`.

---

## 5) Experiment Assumptions

### A) Agent Scheduling Frequency
- **Assumption:** Agent function runs once per day
- **Rationale:** Agent generates schedule for the day based on carbon forecasts
- **Effect:** Agent execution and request costs scale by × 365 for yearly

### B) Agent API Call Frequency
- **Assumption:** Gemini API and ElectricityMaps API are called once per week
- **Rationale:** Agent reuses same schedule for up to 7 days if inputs don't change
- **Effect:** API costs scale by × 52 for yearly (NOT × 365)

### C) Dispatcher Call Frequency
- **Assumption:** Dispatcher is called for every function invocation in the agent scenario
- **Rationale:** All requests route through the dispatcher
- **Effect:** Dispatcher costs scale by × annual_invocations

### D) Constant Load Pattern
- **Assumption:** Invocation load is constant throughout the year
- **Rationale:** Simplifies yearly scaling; `annual = per_day × 365`
- **Effect:** No seasonal or time-of-day load variation modeled

### E) Stable Function Metadata
- **Assumption:** Function configurations (memory, CPU, GPU) do not change during the experiment
- **Rationale:** Ensures fair comparison across scenarios
- **Effect:** Same `invocations_per_day` used for all calculations

### F) No User Priority Changes
- **Assumption:** User priorities (latency tolerance, cost sensitivity) remain constant
- **Rationale:** Agent decisions based on fixed priority weights
- **Effect:** Consistent agent behavior throughout evaluation period

### G) Constant Power Per Function Type
- **Assumption:** For fixed memory/CPU configuration, average power per invocation is constant across regions
- **Rationale:** Cloud Run's resource allocation model provides consistent performance
- **Effect:** Focus on time and carbon intensity variations

### H) Hourly Carbon Intensity Resolution
- **Assumption:** Carbon intensity is piecewise constant within each hour
- **Effect:** One CI lookup per invocation (round to hour)

### I) Forecasted Carbon Intensity Accuracy
- **Assumption:** Forecasted carbon intensity values are assumed to be correct
- **Rationale:** Agent makes decisions based on ElectricityMaps forecasts
- **Limitation:** Real-world forecast accuracy is not modeled

### J) Network Energy Proportional to Transfer Volume
- **Assumption:** Network energy = bytes × 0.001 kWh/GB
- **Effect:** Simple linear model; excludes complex routing effects

### K) Cold Start Control
- **Assumption:** Exclude warmup invocations from measurements
- **Effect:** Focus on steady-state performance
- **Implementation:** Warmup phase before measurement period

### L) Free Tier Exhaustion
- **Assumption:** All Cloud Run free tier resources (180,000 vCPU-seconds/month) are exhausted
- **Effect:** All function executions incur costs at standard rates
- **Note:** May not be realistic for smaller projects; used to simplify computation

### M) MCP Deployment Costs Not Measured
- **Assumption:** Explicit metrics for deploying functions via MCP are not measured
- **Rationale:** Difficult to estimate accurately; negligible impact on yearly totals
- **Mitigation:** Agent compute time for deployment indirectly covers this overhead

---

## 6) Experiment Setup

> **Status:** This section is a placeholder and open for discussion.

### Load Generation

- **Tool:** `evaluation/loadgen/loadgen.py`
- **Request pattern:** TBD
- **Concurrent requests:** TBD
- **Total invocations per function:** TBD

### Test Duration

- **Number of test days:** TBD
- **Time windows per day:** TBD
- **Total measurement period:** TBD

### Per-Invocation Data Derivation

- **Sample size:** TBD invocations per function
- **Aggregation method:** Mean values from GCP Cloud Monitoring
- **Metrics captured:** CPU utilization, memory utilization, network I/O, request latencies

### Warmup Phase

- **Warmup invocations:** TBD (excluded from measurements)
- **Purpose:** Ensure containers are warm; avoid cold start variability

### Carbon Intensity Variation

- **Time coverage:** TBD (should span different hours/days for CI variation)
- **Regions tested:** TBD

---

## 7) Data Collection

| Data | Tool |
|------|------|
| GCP metrics (CPU, memory, network) | `evaluation/gcp_metrics/fetch_gcp_metrics.py` |
| Latency | `evaluation/loadgen/loadgen.py` |
| Final metrics calculation | `evaluation/final_metrics/calculate.py` |

---

## 8) Why We Calculate Instead of Using GCP Data

| Metric | Reason |
|--------|--------|
| **Latency** | End-to-end latency needs manual testing; no GCP metric for this |
| **Energy** | Not provided by GCP |
| **Emissions** | Time constraint (GCP data might not be available in time); doesn't include API emissions |
| **Costs** | Scaling difficult (free compute may not be exhausted during test phase); doesn't include API costs |

---

## 9) References

**Calculation Methodology:**
- Cloud Carbon Footprint: https://www.cloudcarbonfootprint.org/docs/methodology/
  - Source for CPU min/max model (GCP: 0.71-4.26 W/vCPU from SPECPower)
  - Source for memory power (0.392 W/GB ≈ 0.4 W/GiB)
  - Source for network energy (0.001 kWh/GB for hyperscale optical fiber)
  - Source for PUE (GCP: 1.1)

**Evaluation Metrics:**
- Abdulsalam, S., Laber, D., Pasricha, S., & Bradley, A. (2015). "Using the Greenup, Powerup, and Speedup metrics to evaluate software energy efficiency." *2015 Sixth International Green and Sustainable Computing Conference (IGSC)*. IEEE.
  - Source for GPS-UP ratio metrics

**Serverless Context:**
- Sharma, P., & Fuerst, A. (2024). "Accountable Carbon Footprints and Energy Profiling For Serverless Functions." *Proceedings of the 2024 ACM Symposium on Cloud Computing (SoCC '24)*, 522-541.
  - Supports per-invocation energy measurement approach for serverless
- Lin, C., & Shahrad, M. (2024). "Bridging the Sustainability Gap in Serverless through Observability and Carbon-Aware Pricing." *HotCarbon 2024*.
  - Supports carbon-aware scheduling motivation for serverless

**Pricing Data:**
- GCP Cloud Storage pricing: https://cloud.google.com/storage/pricing#network-buckets
- GCP Cloud Run pricing: https://cloud.google.com/run/pricing

---

**Document Status:** Work in Progress
**Last Updated:** 2026-01-19
