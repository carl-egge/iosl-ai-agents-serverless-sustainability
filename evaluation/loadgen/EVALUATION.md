# (WIP) Evaluation Protocol — GPS-UP for a Serverless Load-Shifting Agent

### 1) Objective

Quantify the impact of the **scheduler agent (planner + dispatcher + queues)** versus two non-agent baselines using **GPS-UP**-style metrics:

- **Speedup** $(S) = (T_{base} / T_{new})$
- **Greenup** $(G) = (E_{base} / E_{new})$
- **Powerup** $(U) = (P_{base} / P_{new})$
    
    with ($E = P \cdot T$)
    

Because direct power measurement is impractical in FaaS, we use a **power proxy** and focus on **relative** comparisons.

---

### 2) Scenarios (A/B/C)

Run the *same invocation trace* under three scenarios:

**A — Fixed Region (Home baseline)**

Always execute immediately in one pre-selected region.

**B — Caller Region (Local baseline)**

Execute immediately in the region where the call originates.

**C — AI Agent (Proposed)**

Dispatcher routes to region/time chosen by the agent policy (including delayed execution if applicable).

---

### 3) Workloads (4 functions, fixed configs)

Use the four functions as the only workload set:

1. **api-health-check** (low compute, low data)
2. **crypto-key-gen** (high compute, low data)
3. **image-conversion** (moderate compute, moderate/high data)
4. **video-transcoding** (high compute, high data)

**Fix** per-function runtime configuration across all scenarios (same memory/CPU limits, same code, same dependencies).

---

### 4) Invocation Trace Design

Create one trace per function and replay it identically across A/B/C:

- **Time span:** at least 24h (ideally multiple days if you want carbon variability).
- **Call pattern:** include steady rate + bursts (simple is fine).
- **Input tiers (optional but recommended):**
    - image/video: small/medium/large payload classes
    - keep exact bytes logged per invocation

---

### 5) Data to Record (minimal instrumentation)

For every invocation, log:

- `function_id`
- `scenario` ∈ {A,B,C}
- `request_timestamp` (when the developer calls)
- `start_timestamp` (when execution starts)
- `end_timestamp` (when execution ends)
- `executed_region`
- `payload_bytes_in`, `payload_bytes_out` (or total bytes)
- `invocation_id` (for join consistency)

From this compute:

- **End-to-end time**: ($T = end - request$)
    
    (This intentionally includes queue delay in scenario C; it reflects the real “time-to-result”.)
    

---

### 6) Power Proxy Model (to avoid measuring watts)

Define a per-function **relative power score** ($P^{*}$) that is *constant across scenarios* (unless you explicitly vary resource configuration, which you are not doing here).

**Recommended simplest assumption (zero extra measurements):**

- ($P^{*}(f)$) is constant for each function (f)
- Therefore ($P_{base} = P_{new}$) → **Powerup ≈ 1** for all comparisons

Then:

- Energy proxy: ($E^{} = P^{}\cdot T$)
- If ($P^{*}$) cancels, **Greenup becomes effectively time-based unless you add carbon intensity** (next section).

---

### 7) “Greenup” as Carbon-Adjusted Energy (recommended adaptation)

To make “Greenup” reflect the benefit of spatio-temporal shifting, incorporate carbon intensity:

- Get **carbon intensity** (CI(region, time)) from an external source (forecast or historical actuals).
- Define carbon-adjusted energy proxy per invocation:
    
    $E^{CO2*} = (P^{*}\cdot T)\cdot CI(executedRegion, executionTime)$
    
- Then:
    
    $Greenup = \frac{\sum E^{CO2*}{baseline}}{\sum E^{CO2*}{agent}}$
    

This requires **no power measurement**, only timestamps, region, and an hourly (CI) series.

---

### 8) Metric Computation (per function and aggregated)

For each function (f), compute totals across all invocations (i):

- $(T_{scenario}(f) = \text{mean or sum of } T_i)$
- $(E^{CO2*}_{scenario}(f) = \sum (P^{*}(f)\cdot T_i \cdot CI_i))$

Then:

- **Speedup (C vs baseline X):**
    
    $S_{C\leftarrow X}(f)=\frac{T_X(f)}{T_C(f)}$
    
- **Greenup (C vs baseline X):**
    
    $G_{C\leftarrow X}(f)=\frac{E^{CO2*}_X(f)}{E^{CO2*}_C(f)}$
    
- **Powerup (C vs baseline X):**
    
    $U_{C\leftarrow X}(f)\approx 1$
    
    (under constant (P^{*}))
    

**Aggregate metric across all functions** by summing numerators/denominators (weighted implicitly by invocation counts):
$G_{overall}=\frac{\sum_f E^{CO2*}_X(f)}{\sum_f E^{CO2*}_C(f)}$

---

## Reasonable Assumptions to Reduce Measurements (recommended set)

### A) Treat power as constant per function (eliminate watt/energy measurement)

- **Assumption:** for a fixed memory/CPU configuration, the average power draw per invocation for a given function type is constant across regions and scenarios.
- **Effect:** Powerup ≈ 1; you do not need any power measurement campaign.

### B) Use a single carbon intensity time series per region at hourly resolution

- **Assumption:** carbon intensity is piecewise constant within each hour.
- **Effect:** one lookup per invocation (round execution start to hour) is sufficient.

### C) Ignore network energy unless you want a second “data-heavy sensitivity” result

Option 1 (simplest):

- **Assumption:** energy is dominated by compute for crypto/video, and network effects do not change conclusions.
    
    Option 2 (still low effort):
    
- Add a linear byte term with a fixed coefficient ($k_{net}$) shared across scenarios:
    
    $E^{CO2*} = (P^{*}T + k_{net}\cdot bytes)\cdot CI$
    
- **Effect:** you can report a sensitivity band “compute-only” vs “compute+network”.

### D) Control cold starts by design rather than measuring them

- **Assumption:** exclude first N warm-up invocations per region/function or run a warm-up phase before logging.
- **Effect:** you avoid needing to model cold start variability.

### E) Use end-to-end time uniformly (do not split latency components)

- **Assumption:** only end-to-end time matters for Speedup; internal breakdown is not required for this study.
- **Effect:** fewer traces/metrics, simpler analysis.

---

## Output Format (what you report)

For each function (and overall), provide a small table:

- $(T_A, T_B, T_C)$
- $(G_{C\leftarrow A}, G_{C\leftarrow B})$
- $(S_{C\leftarrow A}, S_{C\leftarrow B})$
- $(U \approx 1)$ (state the assumption explicitly)

---

If you want one more simplification: you can **drop Powerup entirely** (or state “Powerup is 1 by design”) and present only **Speedup and Carbon-Greenup**, which is typically the most interpretable outcome for spatio-temporal carbon-aware scheduling in serverless systems.