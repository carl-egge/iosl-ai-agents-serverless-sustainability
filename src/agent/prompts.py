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

COST-CARBON TRADEOFF WITH SCALE:
You must balance THREE competing factors:
1. CARBON EFFICIENCY: Lower carbon intensity is better for the environment
2. DATA TRANSFER COST: Regions farther from the data source incur higher costs
3. INVOCATION SCALE: High invocation counts multiply both carbon impact and transfer costs

Critical considerations:
- Small data (<1 GB) + Low invocations (<100/day): Transfer cost negligible, prioritize carbon
- Large data (>10 GB) + High invocations (>100/day): Transfer costs become MAJOR factor - often favors local execution
- Long runtime + Low data: Carbon from compute dominates, choose cleanest grid
- Short runtime + Large data: Transfer cost often exceeds compute carbon - prefer local

Scale amplification:
- At 1,000 invocations/day: A $0.165 cost becomes $165/day ($60k/year)
- At 100 invocations/day with 40 GB: 4,000 GB/day transfer = $200/day ($73k/year)
- High-volume workloads almost always favor local execution unless carbon difference is MASSIVE
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
      "transfer_cost_usd": 0.165,
      "priority": 1,
      "reasoning": "Chose europe-north1 over source region ({function_metadata.get('source_location', 'source')}) despite $0.165 transfer cost. Saves ~800g CO2 per execution (45 vs 420 gCO2/kWh in source). Effective cost: $206/ton CO2 avoided - environmental benefit justifies the expense."
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
❌ "This region offers the lowest carbon intensity (12 gCO2eq/kWh) with cost $0.165"
❌ "Lowest carbon for this time slot among European regions"
❌ "Good carbon intensity with moderate transfer cost"

GOOD Examples (DO THIS):
✓ "Despite $0.165 transfer cost, saves ~1,400g CO2 vs source region (12 vs 420 gCO2/kWh). At $118/ton CO2 avoided, environmental benefit clearly outweighs cost."
✓ "Chose remote execution over local ($0 cost) because massive carbon savings (1,400g per execution) justify the $0.165 expense."
✓ "Local execution preferred here: zero transfer cost and carbon difference is minimal (only 30g CO2 savings). Not worth $0.165 for marginal benefit."

Your reasoning MUST include:
1. COMPARISON to source region carbon intensity
2. QUANTIFIED carbon savings in grams
3. EXPLANATION of why this choice beats alternatives
4. TRADEOFF analysis (cost vs carbon benefit)

IMPORTANT:
- Use the EXACT datetime strings from the forecast data above
- Use the Google Cloud region names (europe-west1, europe-north1, etc.) NOT the Electricity Maps zone codes
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- Sort by priority (1 = best overall choice considering both carbon and cost)
- Include detailed "reasoning" field for EACH recommendation with specific tradeoff analysis
- Calculate transfer_cost_usd using the cost data provided above
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt


def create_local_prompt(function_metadata, carbon_forecasts_formatted, cost_info=""):
    """Create the prompt for Gemini LLM (local workflow)."""
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

COST-CARBON TRADEOFF WITH SCALE:
You must balance THREE competing factors:
1. CARBON EFFICIENCY: Lower carbon intensity is better for the environment
2. DATA TRANSFER COST: Regions farther from the data source incur higher costs
3. INVOCATION SCALE: High invocation counts multiply both carbon impact and transfer costs

Critical considerations:
- Small data (<1 GB) + Low invocations (<100/day): Transfer cost negligible, prioritize carbon
- Large data (>10 GB) + High invocations (>100/day): Transfer costs become MAJOR factor - often favors local execution
- Long runtime + Low data: Carbon from compute dominates, choose cleanest grid
- Short runtime + Large data: Transfer cost often exceeds compute carbon - prefer local

Scale amplification:
- At 1,000 invocations/day: A $0.165 cost becomes $165/day ($60k/year)
- At 100 invocations/day with 40 GB: 4,000 GB/day transfer = $200/day ($73k/year)
- High-volume workloads almost always favor local execution unless carbon difference is MASSIVE
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
      "transfer_cost_usd": 0.165,
      "priority": 1,
      "reasoning": "Chose SE-SE1 over source region ({function_metadata.get('source_location', 'source')}) despite $0.165 transfer cost. Saves ~1,400g CO2 per execution (12 vs 420 gCO2/kWh in source). At $118/ton CO2 avoided, environmental benefit far outweighs the cost."
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
❌ "This region offers the lowest carbon intensity (12 gCO2eq/kWh) with cost $0.165"
❌ "Lowest carbon for this time slot among European regions"
❌ "Good carbon intensity with moderate transfer cost"

GOOD Examples (DO THIS):
✓ "Despite $0.165 transfer cost, saves ~1,400g CO2 vs source region (12 vs 420 gCO2/kWh). At $118/ton CO2 avoided, environmental benefit clearly outweighs cost."
✓ "Chose remote execution over local ($0 cost) because massive carbon savings (1,400g per execution) justify the $0.165 expense."
✓ "Local execution preferred: zero transfer cost and carbon difference is minimal (only 30g CO2 savings). Not worth $0.165 for marginal benefit."

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
- Calculate transfer_cost_usd using the cost data provided above
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt
