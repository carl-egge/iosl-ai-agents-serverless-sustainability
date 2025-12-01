"""Prompt builders for Gemini scheduling."""


def create_gcp_prompt(function_metadata, carbon_forecasts_formatted):
    """Create the prompt for Gemini LLM (GCP workflow)."""
    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata["instant_execution"] else "FLEXIBLE DEADLINE"

    prompt = f"""You are a carbon-aware serverless function scheduler. Your goal is to minimize carbon emissions.

Function Details:
- Function ID: {function_metadata['function_id']}
- Runtime: {function_metadata['runtime_ms']} ms
- Memory: {function_metadata['memory_mb']} MB
- Execution Type: {instant_note}
- Description: {function_metadata['description']}

{carbon_forecasts_formatted}

Task:
Create a scheduling recommendation for each of the next 24 time slots.
For each time slot, recommend the BEST Google Cloud region to execute this function to minimize carbon emissions.

Rules:
1. Consider the carbon intensity forecast for each region at each time slot
2. Lower carbon intensity = better choice
3. If instant_execution is true, prioritize immediate execution in lowest-carbon region
4. Rank time slots by carbon efficiency (best execution times first)

Output Format (JSON only, no markdown):
{{
  "recommendations": [
    {{
      "datetime": "2025-01-17T10:00:00",
      "region": "europe-north1",
      "carbon_intensity": 45,
      "priority": 1
    }},
    ...
  ]
}}

IMPORTANT:
- Use the EXACT datetime strings from the forecast data above
- Use the Google Cloud region names (europe-west1, europe-north1, etc.) NOT the Electricity Maps zone codes
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- Sort by priority (1 = best/lowest carbon time to execute)
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt


def create_local_prompt(function_metadata, carbon_forecasts_formatted, forecasts):
    """Create the prompt for Gemini LLM (local workflow)."""
    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata["instant_execution"] else "FLEXIBLE DEADLINE"

    # Keep the extra timestamp context from the original local script
    first_region = next(iter(forecasts.values()))
    start_time = first_region["forecast"][0]["datetime"]
    _ = start_time  # retained for parity; value already reflected in formatted text

    prompt = f"""You are a carbon-aware serverless function scheduler. Your goal is to minimize carbon emissions.

Function Details:
- Function ID: {function_metadata['function_id']}
- Runtime: {function_metadata['runtime_ms']} ms
- Memory: {function_metadata['memory_mb']} MB
- Execution Type: {instant_note}
- Description: {function_metadata['description']}

{carbon_forecasts_formatted}

Task:
Create a scheduling recommendation for each of the next 24 time slots.
For each time slot, recommend the BEST region to execute this function to minimize carbon emissions.

Rules:
1. Consider the carbon intensity forecast for each region at each time slot
2. Lower carbon intensity = better choice
3. If instant_execution is true, prioritize immediate execution in lowest-carbon region
4. Rank time slots by carbon efficiency (best execution times first)

Output Format (JSON only, no markdown):
{{
  "recommendations": [
    {{
      "datetime": "2025-01-17T10:00:00",
      "region": "SE-SE1",
      "carbon_intensity": 12,
      "priority": 1
    }},
    ...
  ]
}}

IMPORTANT:
- Use the EXACT datetime strings from the forecast data above
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- Sort by priority (1 = best/lowest carbon time to execute)
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt
