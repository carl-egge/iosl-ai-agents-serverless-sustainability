# Metrics Methodology — Energy, Emissions, and Cost Calculations

## 1. Purpose and Scope

This document defines **how metrics are calculated** for the serverless load-shifting evaluation. It covers formulas, constants, assumptions, and tools.

**For experiment design and execution procedures, see [EVALUATION.md](EVALUATION.md).**

---

## 2. Metrics Overview

| Metric | Unit | Method |
|--------|------|--------|
| **Latency*** | ms | Measured (End-to-End by Loadgen) |
| **Energy** | kWh | Calculated (CCF methodology) |
| **Emissions** | gCO2 | Calculated (energy × carbon intensity) |
| **Cost Overhead** | USD | Calculated (transfer + agent costs) |

*\*Latency is not measured for now. Comparing latency between approaches would not be meaningful when one approach (Agent) performs temporal shifting — scheduling executions for later hours rather than executing immediately.*

---

## 3. Calculation Formulas

### 3.1 Energy (Per-Invocation)

Reference: [Cloud Carbon Footprint methodology](https://www.cloudcarbonfootprint.org/docs/methodology/)

**Power consumption:**
```
cpu_power_w      = vcpus × (0.71 + cpu_utilization × 3.55) W    # CCF min/max model
memory_power_w   = memory_gib × 0.4 W/GiB                       # allocation-based
gpu_power_w      = gpu_count × (8 + gpu_utilization × 64) W     # CCF min/max model (if GPU required)
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
- GPU power uses CCF **min/max model** (same as CPU)
  - Formula: `gpu_count × (min_watts + gpu_util × (max_watts - min_watts))`
  - NVIDIA L4 values: min=8W (idle), max=72W (TDP)
  - L4 idle power estimated from T4 ratio (CCF T4: 8W/71W ≈ 11% idle → L4: 72W × 0.11 ≈ 8W)
  - GCP doesn't expose GPU utilization, so we assume 10% mean utilization (CPU data shows mean utilization is typically much lower than p95)
  - At 10% utilization: 8 + 0.1 × 64 = 14.4W
- PUE (Power Usage Effectiveness) = region-specific values (1.08–1.10) from [Google Data Centers](https://datacenters.google/efficiency/) Q3 2025 TTM. Fleet average 1.09 used as fallback for regions without a published value.

**Runtime calculation:**
```
runtime_s = request_latencies_ms.mean / 1000
```
- We use mean request latency from GCP Cloud Monitoring (`request_latencies_ms.mean`)
- **Why not billable_instance_time?** We observed up to 83× discrepancy between `billable_instance_time / request_count` and mean request latency, yet CPU utilization remained similar across projects. This strongly suggests CPU utilization is **not** measured over billable instance time.
- Mean request latency better approximates the time window over which CPU utilization is measured, making the energy formula (`power × runtime`) more accurate
- Using request latency ensures consistency in the energy formula — both factors use a comparable time base

*Note on idle energy:* Idle instances do consume some energy (memory refresh, CPU idle baseline). However, we lack reliable metrics for idle-time energy consumption. The `billable_instance_time` metric includes idle periods, but the CPU utilization metric does not. Mixing these incompatible measurements would produce inaccurate results.

---

### 3.2 Emissions (Per-Invocation)

```
total_emissions_g = total_energy_kwh × carbon_intensity_g_per_kwh
```

Carbon intensity sourced from [ElectricityMaps API](https://portal.electricitymaps.com/docs) based on region and execution time.

**Weighted Average Calculation:** Each function invocation is mapped to the carbon intensity of its execution hour (from GCP Cloud Monitoring hourly request counts). The weighted average across all hours is used as the carbon intensity value for the function:
```
weighted_avg = Σ(requests_hour × intensity_hour) / total_requests
```

**Limitation: Mock Forecast Mode**

Due to ElectricityMaps ToS changes mid-project restricting forecast endpoint access, the agent uses historical data as a workaround (`USE_ACTUAL_FORECASTS=False`). Past 24h carbon intensity is fetched and timestamps are shifted +24h to simulate forecasts.

This does not affect evaluation validity: all approaches use the same forecast data, so shifting values by one day has no effect on relative comparisons between approaches. It only has a minor effect on absolute emission values.

---

### 3.3 Cost Overhead (Per-Invocation)

Additional costs compared to baseline (executing in home region with no agent).

**Components:**
1. **Transfer costs** — regional data transfer vs home region
2. **Agent architecture costs** (agent scenario only) — execution and API costs

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

**Agent Execution Cost Overhead:**

For agent projects, the dispatcher and agent functions add compute cost overhead. This is calculated using `billable_instance_time` (what GCP actually charges):

```
billable_time_per_request_s = billable_instance_time_s / request_count

vcpu_cost      = allocated_vcpus × billable_time_per_request_s × tier_vcpu_rate
memory_cost    = (allocated_memory_mb / 1024) × billable_time_per_request_s × tier_memory_rate
invocation_cost = tier_invocation_rate

agent_setup_cost_per_invocation = vcpu_cost + memory_cost + invocation_cost
```

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
- Agent API calls: daily → × 365

**Agent API overhead (only for Agent approaches):**

See [AGENT_API_OVERHEAD.md](AGENT_API_OVERHEAD.md) for full methodology.

| Metric | Per API Call | Per Year (365 calls) |
|--------|--------------|----------------------|
| Energy | 0.010 kWh | 3.65 kWh |
| Carbon | 1.0 gCO2 | 365 gCO2 (0.365 kg) |
| Cost | $0.0054 | $1.97 |

*Note: Values are for Gemini API only. Electricity Maps API overhead is negligible (~0.0001 kWh/request) and excluded.*

These values are added to the `agent` function's metrics in Agent approach projects only.

**Latency*:** Mean from measurements (NOT scaled)

---

### 3.5 Project Aggregation

```
Project_Total = Σ(Function_Annual_Values)
```

- **Energy, emissions, costs:** Sum across all functions
- **Latency*:** Mean across all functions

---

### 3.6 Relative Metrics (GPS-UP Ratios)

Reference: Abdulsalam et al. (2015) IEEE IGSC — "Using the Greenup, Powerup, and Speedup metrics to evaluate software energy efficiency"

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| **Speedup*** | `Latency_baseline / Latency_approach` | >1 = faster than baseline |
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
| GPU power (L4 idle) | 8W | Estimated from CCF T4 ratio ([T4: 8W/71W](https://github.com/cloud-carbon-footprint/cloud-carbon-footprint/blob/trunk/packages/gcp/src/domain/GcpFootprintEstimationConstants.ts)) |
| GPU power (L4 max) | 72W | [NVIDIA L4 datasheet](https://www.nvidia.com/en-us/data-center/l4/) |
| GPU utilization (assumed) | 0.1 (10%) | Conservative estimate (CPU data shows mean << p95; GCP doesn't expose GPU metrics) |
| Network energy | 0.001 kWh/GB | Cloud Carbon Footprint (hyperscale optical fiber) |
| Datacenter PUE | 1.08–1.10 (per region) | [Google Data Centers](https://datacenters.google/efficiency/) (Q3 2025 TTM) |

All constants stored in `local_bucket/static_config.json`.

---

## 5. Why We Calculate Instead of Using GCP Data

| Metric | Reason |
|--------|--------|
| **Latency*** | End-to-end latency needs client-side measurement; GCP only has server-side |
| **Energy** | Not provided by GCP |
| **Emissions** | GCP carbon data may not be available in time; doesn't include API emissions |
| **Costs** | Experiments don't exhaust GCP's free compute tier, so billed costs are lower than actual compute costs outside free tier. Scaling from experiment to yearly rates is non-linear because the free tier would eventually be exhausted. Also doesn't include API costs. |


**Loadgen Latency* measurement source:** The loadgen job logs `end_to_end_latency_ms` for
each direct invocation (scenario A/B). For scenario C, `end_to_end_latency_ms`
is `null` when the dispatcher schedules execution for a later time (time-shift).

---

## 6. Calculation Assumptions

| Assumption | Description | Effect |
|------------|-------------|--------|
| Constant Power Per Function Type | Same power for fixed config across regions | Focus on time and carbon intensity variations |
| Hourly Carbon Intensity Resolution | CI piecewise constant within each hour | One CI lookup per invocation |
| Forecasted Carbon Intensity Accuracy | Forecasts assumed correct | Real-world forecast error not modeled |
| Network Energy Proportional to Transfer | Linear model (bytes × 0.001 kWh/GB) | Excludes complex routing effects |
| Equal Compute Costs Across Approaches | Compute costs (CPU, memory, invocation) are identical across approaches for the same function. Since experiments don't exhaust GCP's free compute tier, absolute cost scaling is unreliable. We therefore only measure *cost overhead* relative to baseline: data transfer costs, dispatcher/agent execution costs, and API costs. | Focuses comparison on costs that actually differ between approaches |
| MCP Deployment Costs Not Measured | Deployment overhead not explicitly tracked | Negligible impact on yearly totals |
| Runtime from Request Latency | Mean request latency from GCP used; evidence suggests this better matches CPU utilization measurement window than billable time | Energy formula uses consistent time base |
| Stable Inputs Throughout Year | Function metadata and priorities unchanged | Allows upscaling of our results to a whole year |
| Region-Specific PUE Coverage | Google only publishes PUE for owned-and-operated data centers; 12 of 22 regions lack published values and use the fleet average 1.09 as fallback. Of our 5 experiment regions, 3 have published PUE (us-east1: 1.10, us-central1: 1.10, europe-west1: 1.08) while 2 use the fleet average (europe-north2, northamerica-northeast1). | Slight inaccuracy for regions without published PUE |

**Note on Runtime Choice:** We observed that billable instance time can be up to 83× higher than mean request latency, yet CPU utilization remains similar across projects. This strongly suggests GCP does not measure CPU utilization over billable instance time. Using request latency ensures the energy formula (`power × runtime`) uses a consistent time base for both factors.

---

## 7. Tools

### 7.1 GCP Metrics Fetcher

**Tool:** `evaluation/gcp_metrics/fetch_gcp_metrics.py`

Collects from GCP Cloud Monitoring:
- Request count
- Request latency
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
  - Source for PUE methodology (GCP region-specific: 1.08–1.10, fleet avg 1.09)

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
**Last Updated:** 2026-01-22
