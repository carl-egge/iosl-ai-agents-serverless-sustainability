# Agent API Overhead Estimation: Energy, Carbon & Cost

This document describes the methodology and calculations for estimating the energy consumption, carbon emissions, and monetary cost of the AI agent's **external API calls** (Gemini, Electricity Maps). This quantifies the API overhead of running the optimization pipeline itself.

---

## 1. Methodology

### 1.1 Components Considered

The agent Cloud Run function runs once every 24 hours (365×/year) to generate schedules. External API calls (Gemini, Electricity Maps) are made with each agent run.

We quantify three sources of overhead:

| Component | Data Available | Estimation Method |
|-----------|---------------|-------------------|
| Gemini API | Token counts from API response | Scaled from Google's official measurement |
| Electricity Maps API | Request count + response size | Per-request energy estimate |
| Network Transfer | Data volume | Energy per GB estimate |

### 1.2 Components Excluded

GCP infrastructure overhead (cold starts, container deployments) is excluded from this analysis. These costs are identical whether the agent is enabled or not, and therefore cancel out in any comparative evaluation.

## 2. Energy Estimation

### 2.1 LLM Inference (Gemini API)

#### 2.1.1 Reference Measurement

Google published full-stack measurements for Gemini Apps inference in May 2025:

| Metric | Median Text Prompt | Methodology |
|--------|-------------------|-------------|
| Energy | 0.24 Wh | Full-stack (GPU + CPU + RAM + idle capacity + PUE) |
| Carbon | 0.03 gCO2e | Google fleet average grid intensity |
| Water | 0.26 mL | Data center cooling |

This measurement includes active compute, idle machines maintained for availability, host CPU/RAM overhead, and datacenter efficiency losses (PUE 1.09). 

#### 2.1.2 Scaling to Our Agent

Google's measurement corresponds to a "median text prompt" in Gemini Apps. Consumer interactions are predominantly short, conversational queries. Based on typical usage patterns, the median prompt likely comprises 200–500 total tokens.

Our agent uses approximately 31,000 tokens per run (Precisely 17,370 input tokens and ~13,600 output tokens across 4 function calls). LLM inference energy consists of two components: fixed overhead (request routing, load balancing, idle capacity allocation) that does not scale with tokens, and variable compute (attention, KV cache, token generation) that does. Energy therefore follows a relationship of the form:

```
Energy = Fixed_Overhead + Variable_Energy(tokens)
```

We considered three scaling scenarios:

| Scenario | Method | Agent Energy |
|----------|--------|--------------|
| Optimistic | Pure token-based scaling | 5.8 Wh |
| Middle | Sublinear token scaling | ~10 Wh |
| Conservative | Linear token scaling from median | ~15 Wh |

We adopt the middle-ground estimate of **10 Wh per agent run** for the following reasons:

The optimistic estimate likely undercounts because token-level benchmarks typically measure GPU compute only, while Google's measurement includes full-stack overhead. Applying GPU-only rates to a full-stack comparison underestimates total energy.

The conservative estimate likely overcounts because Google's 0.24 Wh includes significant fixed overhead that gets amortized over larger requests. Attention mechanisms have been optimized and marginal cost per token decreases with prompt size.

A sublinear scaling model where energy grows proportionally to tokens raised to a power less than one (e.g., α ≈ 0.7–0.8) reflects the reality that larger prompts achieve better hardware utilization. This yields approximately 10 Wh for our 31,000-token agent.

### 2.2 External API Calls (Electricity Maps)

The Electricity Maps API is a lightweight REST service returning JSON carbon intensity forecasts. Typical response size is 1–10 KB, and servers are likely located in the EU (Danish company).

This estimate covers the marginal cost of serving our API requet, the server compute and network transfer required to respond to a single query. It excludes Electricity Maps upstream data collection infrastructure (sensors, grid operator API integrations, data processing pipelines), which is shared across all users and operates independently of individual requests, and is additionally not able to be approximated within a reasonable scope by us.

| Component | Value | Calculation |
|-----------|-------|-------------|
| Server-side compute | ~0.0001 kWh | DB lookup + JSON serialization |
| Network transfer | ~0.00000002 kWh | 10 KB × 0.002 kWh/GB |
| **Total per request** | **~0.1 Wh** | |

Even if upstream infrastructure were included and increased this estimate by an order of magnitude, the Electricity Maps overhead would remain negligible compared to Gemini inference.

### 2.3 Network Transfer

Estimates for network energy intensity vary considerably in the literature. We use 0.002 kWh/GB as an optimistic but defensible estimate for modern infrastructure. For our agent's data transfer (API requests and responses totaling less than 1 MB), network energy is negligible.

---

## 3. Carbon Estimation

### 3.1 Carbon Intensity Approach

Calculating carbon emissions from energy requires a grid carbon intensity value. For Gemini API calls, multiple approaches exist:

| Approach | Value (gCO2/kWh) | Basis |
|----------|------------------|-------|
| Google fleet average | ~100 | Attributional (reported by Google) |
| US grid average | ~400 | National average |
| Marginal intensity | 200–600 | Physical grid response to added load |

For this case, the distinction between attributional and consequential emissions is important. Attributional emissions reflect what carbon footprint is assigned to an activity under standard accounting. Google reports ~100 gCO2/kWh because they purchase renewable energy and carbon credits. Consequential emissions reflect what physical emissions result when load is actually added to the grid, which may differ significantly.

For our use case, use Google's reported fleet average of 100 gCO2/kWh for three reasons. First, it maintains consistency with our energy measurement, which derives from Google's official data. Second, it is an official, verifiable figure that makes our estimates reproducible. Third, attributional accounting is standard practice in corporate carbon reporting and lifecycle assessment.

We acknowledge this is an attributional figure. A consequential analysis would require different intensity values, but for quantifying agent overhead within Google's infrastructure, the attributional approach is appropriate.

### 3.2 Calculation

Applying Google's attributional intensity to our energy estimate:

```
Carbon = 0.010 kWh × 100 gCO2/kWh = 1.0 gCO2 per agent run
```

---

## 4. Cost Estimation

Gemini 1.5 Flash pricing is $0.075 per million input tokens and $0.30 per million output tokens. For our agent:

```
Cost = (17,370 × $0.075/1M) + (13,600 × $0.30/1M)
     = $0.00130 + $0.00408
     = $0.0054 per agent run
```

---

## 5. Results

### 5.1 Summary

| Metric | Per API Call | Per Year (365 calls) |
|--------|--------------|----------------------|
| Tokens | 31,000 | 11.3 M |
| Energy | 10 Wh (0.010 kWh) | 3.65 kWh |
| Carbon | 1.0 gCO2 | 365 gCO2 (0.365 kg) |
| Monetary Cost | $0.0054 | $1.97 |

**Note:** The agent Cloud Run function and external APIs are both called daily (365×/year). The values above represent API overhead only, not the Cloud Run execution overhead (which is captured separately in GCP metrics).

### 5.2 Sensitivity Analysis

The energy estimate is subject to uncertainty in how LLM energy scales with prompt size. Under different assumptions:

| Scenario | Energy (Wh) | Carbon (gCO2) | Annual Energy | Annual Carbon |
|----------|-------------|---------------|---------------|---------------|
| Optimistic | 5.8 | 0.58 | 2.12 kWh | 212 gCO2 |
| Middle (adopted) | 10 | 1.0 | 3.65 kWh | 365 gCO2 |
| Conservative | 15 | 1.5 | 5.48 kWh | 548 gCO2 |

The range spans approximately 0.21–0.55 kg CO2 per year.

---

## 6. Limitations

Several sources of uncertainty affect these estimates.

**LLM energy scaling is not well characterized.** Google's published measurement applies to median-length consumer prompts. How energy scales to 60–150× larger prompts involves assumptions about fixed versus variable costs that cannot be validated without access to Google's infrastructure.

**Gemini architecture is a black box.** Google does not publish parameter counts, hardware details, or inference optimization techniques for Gemini models. Our estimates rely entirely on their published per-prompt measurement.

**Carbon intensity is attributional, not consequential.** Google's reported 100 gCO2/kWh reflects their renewable energy procurement, not the marginal grid emissions caused by additional load. Actual climate impact may differ.

**Upstream infrastructure is excluded.** For Electricity Maps, we measure only the marginal serving cost, not their data collection pipeline. For Gemini, we measure inference only, not model training or datacenter embodied carbon.

**Network transfer estimates vary widely.** Published values for network energy intensity span an order of magnitude. Our estimate uses an optimistic value; conservative estimates would increase overhead slightly but not materially affect conclusions.

---

## References

1. Google (2025). Measuring the environmental impact of AI inference. Google Cloud Blog. https://cloud.google.com/blog/products/infrastructure/measuring-the-environmental-impact-of-ai-inference

2. Sustainable Web Design (2024). Estimating Digital Emissions. https://sustainablewebdesign.org/estimating-digital-emissions/

3. Energy Intensity of Internet Traffic (2024). Historical analysis and literature review. https://blog.mynl.com/posts/notes/2024-05-21-Energy-Intensity-of-Internet-Traffic/

4. Electricity Maps. API Documentation. https://docs.electricitymaps.com/

---
