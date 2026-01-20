# Metrics Methodology — Energy, Emissions, and Cost Calculations

## 1. Purpose and Scope

This document defines **how metrics are calculated** for the serverless load-shifting evaluation. It covers formulas, constants, assumptions, and tools.

**For experiment design and execution procedures, see [EVALUATION.md](EVALUATION.md).**

---

## 2. Metrics Overview

| Metric | Unit | Method |
|--------|------|--------|
| **Latency** | ms | Measured (Cloud Run request latency) |
| **Energy** | kWh | Calculated (CCF methodology) |
| **Emissions** | gCO2 | Calculated (energy × carbon intensity) |
| **Cost Overhead** | USD | Calculated (transfer + agent costs) |

---

## 3. Calculation Formulas

### 3.1 Energy (Per-Invocation)

Reference: [Cloud Carbon Footprint methodology](https://www.cloudcarbonfootprint.org/docs/methodology/)

**Power consumption:**
```
cpu_power_w      = vcpus × (0.71 + cpu_utilization × 3.55) W    # CCF min/max model
memory_power_w   = memory_gib × 0.4 W/GiB                       # allocation-based
gpu_power_w      = gpu_count × 72W × 0.8                        # if GPU required
total_power_w    = cpu_power_w + memory_power_w + gpu_power_w
```

**Energy per invocation:**
```
compute_energy_kwh = total_power_w × (runtime_s / 3600) × PUE
network_energy_kwh = (data_received_gb + data_sent_gb) / request_count × 0.001 kWh/GB
total_energy_kwh   = compute_energy_kwh + network_energy_kwh
```

**Notes:**
- CPU power uses CCF **min/max model** with actual measured utilization from GCP Cloud Monitoring
  - Formula: `vcpus × (min_watts + cpu_util × (max_watts - min_watts))`
  - GCP values from SPECPower: min=0.71W, max=4.26W per vCPU (delta=3.55W)
  - At 50% utilization: 0.71 + 0.5 × 3.55 = 2.485 W/vCPU
- Memory power uses **allocated capacity** (allocation-based, not utilization-based) — DRAM refresh power is independent of access patterns
- PUE (Power Usage Effectiveness) = 1.1 (Google datacenter efficiency)

---

### 3.2 Emissions (Per-Invocation)

```
total_emissions_g = total_energy_kwh × carbon_intensity_g_per_kwh
```

Carbon intensity sourced from [ElectricityMaps API](https://portal.electricitymaps.com/docs) based on region and execution time.

---

### 3.3 Cost Overhead (Per-Invocation)

Additional costs compared to baseline (executing in home region with no agent).

**Components:**
1. **Transfer costs** — regional data transfer vs home region
2. **Agent architecture costs** (agent scenario only) — execution, request, and API costs

```
transfer_cost_usd   = transfer_gb × region_rate_per_gb
total_cost_overhead = transfer_cost + agent_architecture_costs
```

| Region | Transfer Rate |
|--------|---------------|
| us-east1 (home) | $0/GB |
| North America | $0.02/GB |
| Europe | $0.05/GB |

Reference: [GCP Cloud Storage pricing](https://cloud.google.com/storage/pricing#network-buckets)

---

### 3.4 Yearly Scaling

```
annual_invocations = invocations_per_day × 365

annual_energy_kwh    = per_invocation_energy × annual_invocations
annual_emissions_kg  = (per_invocation_emissions_g × annual_invocations) / 1000
annual_transfer_cost = per_invocation_transfer × annual_invocations
```

**Agent architecture scaling (different frequencies):**
- Dispatcher costs: per invocation → × annual_invocations
- Agent execution: daily → × 365
- Agent API calls (Gemini + ElectricityMaps): weekly → × 52

**Latency:** Mean from measurements (NOT scaled)

---

### 3.5 Project Aggregation

```
Project_Total = Σ(Function_Annual_Values)
```

- **Energy, emissions, costs:** Sum across all functions
- **Latency:** Mean across all functions

---

### 3.6 Relative Metrics (GPS-UP Ratios)

Reference: Abdulsalam et al. (2015) IEEE IGSC — "Using the Greenup, Powerup, and Speedup metrics to evaluate software energy efficiency"

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **Speedup** | `Latency_baseline / Latency_approach` | >1 = faster than baseline |
| **Powerup** | `Energy_baseline / Energy_approach` | >1 = less energy than baseline |
| **Greenup** | `Emissions_baseline / Emissions_approach` | >1 = lower carbon than baseline |
| **Cost Overhead** | Absolute $ value | Baseline = $0 by definition |

---

## 4. Constants

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

## 5. Calculation Assumptions

| Assumption | Description | Effect |
|------------|-------------|--------|
| Constant Power Per Function Type | Same power for fixed config across regions | Focus on time and carbon intensity variations |
| Hourly Carbon Intensity Resolution | CI piecewise constant within each hour | One CI lookup per invocation |
| Forecasted Carbon Intensity Accuracy | Forecasts assumed correct | Real-world forecast error not modeled |
| Network Energy Proportional to Transfer | Linear model (bytes × 0.001 kWh/GB) | Excludes complex routing effects |
| Free Tier Exhaustion | All executions incur costs at standard rates | May overestimate costs for small projects |
| MCP Deployment Costs Not Measured | Deployment overhead not explicitly tracked | Negligible impact on yearly totals |

---

## 6. Why We Calculate Instead of Using GCP Data

| Metric | Reason |
|--------|--------|
| **Latency** | End-to-end latency needs client-side measurement; GCP only has server-side |
| **Energy** | Not provided by GCP |
| **Emissions** | GCP carbon data may not be available in time; doesn't include API emissions |
| **Costs** | Free tier complicates scaling; doesn't include API costs |


**Loadgen Latency measurement source:** The loadgen job logs `end_to_end_latency_ms` for
each direct invocation (scenario A/B). For scenario C, `end_to_end_latency_ms`
is `null` when the dispatcher schedules execution for a later time (time-shift).

---

## 7. Tools

### 7.1 GCP Metrics Fetcher

**Tool:** `evaluation/gcp_metrics/fetch_gcp_metrics.py`

Collects from GCP Cloud Monitoring:
- Request count
- Request latency (mean, p50, p95, p99)
- CPU utilization
- Memory utilization
- Billable instance time
- Network bytes (sent/received)

**Usage:**
```bash
python fetch_gcp_metrics.py --project-id <PROJECT> --url <SERVICE_URL>
# or batch mode:
python fetch_gcp_metrics.py --config experiment_config.json
```

**Output:** `evaluation/results/{project_id}/gcp_metrics_{project_id}_{name}_{timestamp}.json`

---

### 7.2 Final Metrics Calculator

**Tool:** `evaluation/final_metrics/calculate.py`

Applies formulas from this document to compute:
- Per-invocation metrics (energy, emissions, cost overhead)
- Per-year projections
- Project aggregation

**Usage:**
```bash
python calculate.py \
  --gcp-metrics ../results/{project_id}/gcp_metrics_*.json \
  --carbon-intensity 400
```

**Output:** `evaluation/results/{project_id}/final_metrics_{project_id}_{name}_{timestamp}.json`

Output structure:
- `calculation_constants` — power constants used
- `functions[]` — per-function inputs and calculated metrics
- `project_aggregation` — summed yearly values across all functions

---

## 8. References

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
