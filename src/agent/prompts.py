"""Prompt builders for Gemini scheduling."""


def create_gcp_prompt(function_metadata, carbon_forecasts_formatted, cost_info=""):
    """Create the prompt for Gemini LLM (GCP workflow)."""
    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata["instant_execution"] else "FLEXIBLE DEADLINE"

    # Build data transfer context
    data_transfer_context = ""
    has_data_transfer = function_metadata.get("data_input_gb") or function_metadata.get("data_output_gb")

    if has_data_transfer:
        data_input_gb = function_metadata.get("data_input_gb", 0.0)
        data_output_gb = function_metadata.get("data_output_gb", 0.0)
        source_location = function_metadata.get("source_location", "unknown")
        invocations = function_metadata.get("invocations_per_day", 1)
        total_data_gb = data_input_gb + data_output_gb

        data_transfer_context = f"""
Data Transfer Requirements:
- Input data: {data_input_gb} GB per invocation
- Output data: {data_output_gb} GB per invocation
- Total per invocation: {total_data_gb} GB
- Invocations per day: {invocations}
- DAILY data volume: {total_data_gb * invocations:.1f} GB/day
- Source location: {source_location}
{cost_info}

SCHEDULING DECISION FRAMEWORK:

This function's characteristics:
- Data per invocation: {total_data_gb} GB
- Daily transfer cost if remote: ${total_data_gb * invocations * 0.05:.2f}/day
- Annual transfer cost if remote: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year
- Runtime: {function_metadata['runtime_ms']}ms
- Invocations: {invocations}/day

DECISION RULES (apply in order):

1. INSTANT EXECUTION CHECK:
   {instant_note}
   If instant execution required → Choose best available region NOW
   Latency and availability trump optimization - pick lowest current carbon

2. NEGLIGIBLE COST CHECK (annual < $1000):
   Annual transfer cost: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year
   If < $1000/year → Cost is negligible, optimize purely for carbon
   Choose cleanest available region - transfer cost is insignificant

3. MASSIVE DATA CHECK (annual > $10,000):
   If > $10,000/year → Data transfer dominates
   For short runtimes (<5000ms): Strong preference for local execution
   For long runtimes (>30000ms): May justify remote if carbon difference is extreme

4. MODERATE COST RANGE ($1,000 - $10,000/year):
   This function falls in this range: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year

   Apply cost-benefit analysis:
   - Short runtime ({function_metadata['runtime_ms']}ms) + Moderate/Large data:
     CO2 savings from compute are small, transfer cost likely dominates
     → Prefer local execution unless carbon difference is extreme (>10x)

   - Long runtime (>30000ms) + Moderate data:
     CO2 savings from compute can be substantial
     → May justify remote execution if carbon intensity difference is significant

   Rule of thumb: If annual cost > $5000 AND runtime < 5000ms → Stay local
"""

    prompt = f"""You are a carbon-aware serverless function scheduler. Your goal is to optimize for BOTH carbon emissions AND cost efficiency.

Function Details:
- Function ID: {function_metadata['function_id']}
- Runtime: {function_metadata['runtime_ms']} ms
- Memory: {function_metadata['memory_mb']} MB
- Execution Type: {instant_note}
- Description: {function_metadata['description']}
{data_transfer_context}

{carbon_forecasts_formatted}

Task:
Create a scheduling recommendation for each of the next 24 time slots.
For each time slot, recommend the BEST Google Cloud region to execute this function, considering both carbon emissions and data transfer costs.

Rules:
1. Consider the carbon intensity forecast for each region at each time slot
2. Consider data transfer costs when applicable (regions closer to data source are cheaper)
3. Balance carbon efficiency vs. cost - explain your reasoning for each recommendation
4. If instant_execution is true, prioritize immediate execution in best overall region
5. Rank time slots by overall optimization (carbon + cost combined)

Output Format (JSON only, no markdown):
{{
  "recommendations": [
    {{
      "datetime": "2025-01-17T10:00:00",
      "region": "europe-north1",
      "carbon_intensity": 45,
      "transfer_cost_usd": <USE EXACT VALUE FROM "Cost per region" SECTION ABOVE>,
      "priority": 1,
      "reasoning": "Chose europe-north1 over source region ({function_metadata.get('source_location', 'source')}) despite $X.XX transfer cost. Saves ~800g CO2 per execution (45 vs 420 gCO2/kWh in source). Effective cost: $206/ton CO2 avoided - environmental benefit justifies the expense."
    }},
    {{
      "datetime": "2025-01-17T18:00",
      "region": "{function_metadata.get('source_location', 'source')}",
      "carbon_intensity": 420,
      "transfer_cost_usd": 0.0,
      "priority": 24,
      "reasoning": "Local execution has zero transfer cost but highest carbon footprint (420 gCO2/kWh). Only recommended if budget constraints prevent remote execution or instant execution is required."
    }}
  ]
}}

CRITICAL REQUIREMENTS FOR REASONING FIELD:
Your reasoning MUST explain the specific tradeoff decision, not just restate the data:

BAD Examples (DO NOT DO THIS):
"This region offers the lowest carbon intensity (12 gCO2eq/kWh) with cost $0.165"
"Lowest carbon for this time slot among European regions"
"Good carbon intensity with moderate transfer cost"

GOOD Examples (DO THIS):
"Despite $0.165 transfer cost, saves ~1,400g CO2 vs source region (12 vs 420 gCO2/kWh). At $118/ton CO2 avoided, environmental benefit clearly outweighs cost."
"Chose remote execution over local ($0 cost) because massive carbon savings (1,400g per execution) justify the $0.165 expense."
"Local execution preferred here: zero transfer cost and carbon difference is minimal (only 30g CO2 savings). Not worth $0.165 for marginal benefit."

Your reasoning MUST include:
1. COMPARISON to source region carbon intensity
2. QUANTIFIED carbon savings in grams
3. EXPLANATION of why this choice beats alternatives
4. TRADEOFF analysis (cost vs carbon benefit)

CRITICAL REQUIREMENTS:
- Use the EXACT datetime strings from the forecast data above
- Use the Google Cloud region names (europe-west1, europe-north1, etc.) NOT the Electricity Maps zone codes
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- **MUST sort recommendations by priority field (1 = BEST, 24 = WORST)**
  The FIRST recommendation in the array MUST have priority=1 (the absolute best time/region to execute)
  The LAST recommendation MUST have priority=24 (the worst time/region)
  Sort the array in ASCENDING order by priority before returning
- Include detailed "reasoning" field for EACH recommendation with specific tradeoff analysis
- For transfer_cost_usd: Copy the EXACT dollar amount from the "Cost per region for this workload" section above
  Example: If the section shows "$2.0000 USD: europe-north2", use 2.0 for transfer_cost_usd
  DO NOT calculate or estimate costs yourself - use the provided values exactly
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt


def create_local_prompt(function_metadata, carbon_forecasts_formatted, cost_info=""):
    """Create the prompt for Gemini LLM (local workflow)."""
    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata["instant_execution"] else "FLEXIBLE DEADLINE"

    data_transfer_context = ""
    has_data_transfer = function_metadata.get("data_input_gb") or function_metadata.get("data_output_gb")

    if has_data_transfer:
        data_input_gb = function_metadata.get("data_input_gb", 0.0)
        data_output_gb = function_metadata.get("data_output_gb", 0.0)
        source_location = function_metadata.get("source_location", "unknown")
        invocations = function_metadata.get("invocations_per_day", 1)
        total_data_gb = data_input_gb + data_output_gb

        data_transfer_context = f"""
Data Transfer Requirements:
- Input data: {data_input_gb} GB per invocation
- Output data: {data_output_gb} GB per invocation
- Total per invocation: {total_data_gb} GB
- Invocations per day: {invocations}
- DAILY data volume: {total_data_gb * invocations:.1f} GB/day
- Source location: {source_location}
{cost_info}

SCHEDULING DECISION FRAMEWORK:

This function's characteristics:
- Data per invocation: {total_data_gb} GB
- Daily transfer cost if remote: ${total_data_gb * invocations * 0.05:.2f}/day
- Annual transfer cost if remote: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year
- Runtime: {function_metadata['runtime_ms']}ms
- Invocations: {invocations}/day

DECISION RULES (apply in order):

1. INSTANT EXECUTION CHECK:
   {instant_note}
   If instant execution required → Choose best available region NOW
   Latency and availability trump optimization - pick lowest current carbon

2. NEGLIGIBLE COST CHECK (annual < $1000):
   Annual transfer cost: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year
   If < $1000/year → Cost is negligible, optimize purely for carbon
   Choose cleanest available region - transfer cost is insignificant

3. MASSIVE DATA CHECK (annual > $10,000):
   If > $10,000/year → Data transfer dominates
   For short runtimes (<5000ms): Strong preference for local execution
   For long runtimes (>30000ms): May justify remote if carbon difference is extreme

4. MODERATE COST RANGE ($1,000 - $10,000/year):
   This function falls in this range: ${total_data_gb * invocations * 0.05 * 365:,.0f}/year

   Apply cost-benefit analysis:
   - Short runtime ({function_metadata['runtime_ms']}ms) + Moderate/Large data:
     CO2 savings from compute are small, transfer cost likely dominates
     → Prefer local execution unless carbon difference is extreme (>10x)

   - Long runtime (>30000ms) + Moderate data:
     CO2 savings from compute can be substantial
     → May justify remote execution if carbon intensity difference is significant

   Rule of thumb: If annual cost > $5000 AND runtime < 5000ms → Stay local
"""

    prompt = f"""You are a carbon-aware serverless function scheduler. Your goal is to optimize for BOTH carbon emissions AND cost efficiency.

Function Details:
- Function ID: {function_metadata['function_id']}
- Runtime: {function_metadata['runtime_ms']} ms
- Memory: {function_metadata['memory_mb']} MB
- Execution Type: {instant_note}
- Description: {function_metadata['description']}
{data_transfer_context}

{carbon_forecasts_formatted}

Task:
Create a scheduling recommendation for each of the next 24 time slots.
For each time slot, recommend the BEST region to execute this function, considering both carbon emissions and data transfer costs.

Rules:
1. Consider the carbon intensity forecast for each region at each time slot
2. Consider data transfer costs when applicable (regions closer to data source are cheaper)
3. Balance carbon efficiency vs. cost - explain your reasoning for each recommendation
4. If instant_execution is true, prioritize immediate execution in best overall region
5. Rank time slots by overall optimization (carbon + cost combined)

Output Format (JSON only, no markdown):
{{
  "recommendations": [
    {{
      "datetime": "2025-01-17T10:00:00",
      "region": "SE-SE1",
      "carbon_intensity": 12,
      "transfer_cost_usd": <USE EXACT VALUE FROM "Cost per region" SECTION ABOVE>,
      "priority": 1,
      "reasoning": "Chose SE-SE1 over source region ({function_metadata.get('source_location', 'source')}) despite $X.XX transfer cost. Saves ~1,400g CO2 per execution (12 vs 420 gCO2/kWh in source). At $118/ton CO2 avoided, environmental benefit far outweighs the cost."
    }},
    {{
      "datetime": "2025-01-17T18:00",
      "region": "{function_metadata.get('source_location', 'source')}",
      "carbon_intensity": 420,
      "transfer_cost_usd": 0.0,
      "priority": 24,
      "reasoning": "Local execution has zero transfer cost but highest carbon (420 gCO2/kWh). Only optimal if cost constraints outweigh environmental priorities."
    }}
  ]
}}

CRITICAL REQUIREMENTS FOR REASONING FIELD:
Your reasoning MUST explain the specific tradeoff decision, not just restate the data:

BAD Examples (DO NOT DO THIS):
"This region offers the lowest carbon intensity (12 gCO2eq/kWh) with cost $0.165"
"Lowest carbon for this time slot among European regions"
"Good carbon intensity with moderate transfer cost"

GOOD Examples (DO THIS):
"Despite $0.165 transfer cost, saves ~1,400g CO2 vs source region (12 vs 420 gCO2/kWh). At $118/ton CO2 avoided, environmental benefit clearly outweighs cost."
"Chose remote execution over local ($0 cost) because massive carbon savings (1,400g per execution) justify the $0.165 expense."
"Local execution preferred: zero transfer cost and carbon difference is minimal (only 30g CO2 savings). Not worth $0.165 for marginal benefit."

Your reasoning MUST include:
1. COMPARISON to source region carbon intensity
2. QUANTIFIED carbon savings in grams
3. EXPLANATION of why this choice beats alternatives
4. TRADEOFF analysis (cost vs carbon benefit)

IMPORTANT:
- Use the EXACT datetime strings from the forecast data above
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- Sort by priority (1 = best overall choice considering both carbon and cost)
- Include detailed "reasoning" field for EACH recommendation with specific tradeoff analysis
- For transfer_cost_usd: Copy the EXACT dollar amount from the "Cost per region for this workload" section above
  Example: If the section shows "$2.0000 USD: europe-north2", use 2.0 for transfer_cost_usd
  DO NOT calculate or estimate costs yourself - use the provided values exactly
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt
