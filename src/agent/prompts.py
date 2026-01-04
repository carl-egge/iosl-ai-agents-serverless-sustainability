"""Prompt builders for Gemini scheduling."""


def create_prompt(
    function_metadata: dict,
    carbon_forecasts_formatted: str,
    metrics_info: str,
    region_metrics: dict,
    priority: str = "balanced"
):
    """
    Create the prompt for Gemini LLM to generate optimal scheduling recommendations.

    Args:
        function_metadata: Function metadata dict
        carbon_forecasts_formatted: Formatted carbon forecast string
        metrics_info: Formatted region metrics string (costs and emissions)
        region_metrics: Dict of calculated metrics per region
        priority: Optimization priority - "balanced", "costs", or "emissions"

    Returns:
        Formatted prompt string
    """

    # Build decision rules based on priority
    if priority == "costs":
        decision_framework = """
DECISION FRAMEWORK - COST OPTIMIZATION PRIORITY:

Your PRIMARY goal is cost minimization. Carbon emissions are SECONDARY.

Core Principles:

1. PARETO OPTIMALITY:
   - If a region is both cheaper AND cleaner → Always choose it
   - No tradeoff needed when one option dominates both dimensions

2. COST-FIRST MINDSET:
   - Any non-trivial cost increase requires strong justification
   - Example: Region A: $100/year, 200kg CO2 vs Region B: $200/year, 150kg CO2
     → Choose Region A (doubling cost to save 25% emissions is NOT justified under cost priority)
   - Counterexample: Region A: $100/year, 1000kg CO2 vs Region B: $105/year, 50kg CO2
     → Choose Region B (5% cost increase for 95% emissions reduction IS justified - too extreme to ignore)

3. WHEN TO CONSIDER EMISSIONS:
   - Only when cost difference is negligible in absolute terms (judge based on scale)
   - Only when emissions difference is extreme (multiple orders of magnitude)
   - Use your judgment on what "negligible" and "extreme" mean given the actual numbers

Your reasoning MUST explain why cost savings justify accepting higher emissions (or vice versa in edge cases).
"""

    elif priority == "emissions":
        decision_framework = """
DECISION FRAMEWORK - EMISSIONS OPTIMIZATION PRIORITY:

Your PRIMARY goal is carbon emissions minimization. Cost is SECONDARY.

Core Principles:

1. PARETO OPTIMALITY:
   - If a region is both cheaper AND cleaner → Always choose it
   - No tradeoff needed when one option dominates both dimensions

2. EMISSIONS-FIRST MINDSET:
   - Any non-trivial emissions increase requires strong justification
   - Example: Region A: $500/year, 50kg CO2 vs Region B: $250/year, 75kg CO2
     → Choose Region A (50% emissions increase to save $250 is NOT justified under emissions priority)
   - Counterexample: Region A: $5000/year, 51kg CO2 vs Region B: $500/year, 52kg CO2
     → Choose Region B (90% cost savings for 2% more emissions IS justified - cost difference too extreme)

3. WHEN TO CONSIDER COSTS:
   - Only when emissions difference is negligible in absolute terms (judge based on scale)
   - Only when cost difference is extreme (multiple orders of magnitude)
   - Use your judgment on what "negligible" and "extreme" mean given the actual numbers

Your reasoning MUST explain why emissions reduction justifies accepting higher costs (or vice versa in edge cases).
"""

    else:  # balanced (default)
        decision_framework = """
DECISION FRAMEWORK - BALANCED OPTIMIZATION:

Your goal is to find the best tradeoff between cost and carbon emissions.

Core Principles:

1. PARETO OPTIMALITY:
   - If a region is both cheaper AND cleaner → Always choose it
   - Example: Region A: $500/year, 50kg CO2 vs Region B: $1000/year, 100kg CO2
     → Region A dominates on both dimensions, no tradeoff needed

2. COST-EFFECTIVENESS OF CARBON REDUCTION:
   - Calculate: (Extra cost per year) / (CO2 saved per year in kg) = Cost per kg CO2 avoided
   - Example: Region A: $1200/year, 100kg CO2 vs Region B: $800/year, 150kg CO2
     → Region A costs $400 more but saves 50kg CO2
     → Cost per kg avoided = $400 / 50kg = $8/kg
   - Use your judgment on whether this is good value (consider the absolute magnitudes too)

3. ABSOLUTE MAGNITUDE MATTERS:
   - Tiny absolute differences may not be worth optimizing
   - Example: $5/year difference or 1kg CO2/year difference → Choose based on larger relative impact
   - Large absolute differences deserve careful cost-effectiveness analysis

4. NO FIXED THRESHOLDS:
   - Don't apply rigid rules like "always choose if < $X/kg"
   - Consider the context: $10/kg to save 100kg (= $1000) is different from $10/kg to save 1kg (= $10)
   - Balance relative percentages with absolute magnitudes

Your reasoning MUST include cost-effectiveness calculations and explain why the tradeoff makes sense.
"""

    prompt = f"""You are a carbon-aware serverless function scheduler. Your goal is to optimize execution scheduling based on the specified priority level.

Function Details:
- Function ID: {function_metadata['function_id']}
- Runtime: {function_metadata['runtime_ms']} ms
- Memory: {function_metadata['memory_mb']} MB
- Description: {function_metadata['description']}
- Optimization Priority: {priority.upper()}

{metrics_info}

{carbon_forecasts_formatted}

{decision_framework}

Task:
Create a scheduling recommendation for each of the next 24 time slots.
For each time slot, recommend the BEST Google Cloud region to execute this function.

Output Format (JSON only, no markdown):
{{
  "recommendations": [
    {{
      "datetime": "2025-01-17 10:00",
      "region": "europe-north1",
      "carbon_intensity": 45,
      "transfer_cost_usd": <USE EXACT VALUE FROM REGION COMPARISON ABOVE>,
      "emissions_grams": <USE EXACT VALUE FROM REGION COMPARISON ABOVE>,
      "priority": 1,
      "reasoning": "europe-north1 costs $1,200/year vs source region's $800/year (+$400), but saves 50kg CO2/year (150kg vs 200kg). Cost per kg CO2 avoided = $8/kg, which is excellent. Worth the extra cost for substantial emissions reduction."
    }},
    {{
      "datetime": "2025-01-17 18:00",
      "region": "{function_metadata.get('source_location', 'us-east1')}",
      "carbon_intensity": 420,
      "transfer_cost_usd": 0.0,
      "emissions_grams": <USE EXACT VALUE FROM REGION COMPARISON ABOVE>,
      "priority": 24,
      "reasoning": "Source region has zero transfer cost ($0/year) but highest emissions (200kg CO2/year). Only optimal under strict cost minimization priority."
    }}
  ]
}}

CRITICAL REQUIREMENTS FOR REASONING FIELD:
Your reasoning MUST explain the tradeoff decision based on the priority mode ({priority}):

BAD Examples (vague, no tradeoff analysis):
"This region has low carbon intensity and reasonable cost"
"Good balance of cost and emissions"
"europe-north1 is a clean region"

GOOD Examples (specific, quantified tradeoffs):
"[BALANCED] europe-north1 costs $400 more annually but saves 50kg CO2 ($8/kg avoided). This is highly cost-effective for carbon reduction - choose it."
"[COSTS] us-east1 saves $600/year vs cleanest option. Yes, emissions are 30kg higher, but cost savings of $20/kg CO2 is too expensive for marginal environmental benefit - stay local."
"[EMISSIONS] europe-north1 cuts emissions by 45% (90kg → 50kg) for only $200 extra annually. Clear win for emissions priority - choose it despite higher cost."

Your reasoning MUST include:
1. Specific cost difference in $/year (not per-execution)
2. Specific emissions difference in kg CO2/year (not per-execution)
3. Cost per kg CO2 calculation when relevant
4. Decision based on the {priority} priority mode
5. Comparison to source region or other alternatives

CRITICAL REQUIREMENTS:
- Use datetime format "YYYY-MM-DD HH:MM" (e.g., "2025-01-17 10:00") - convert from forecast timestamps if needed
- Use the Google Cloud region names (europe-west1, europe-north1, etc.) NOT the Electricity Maps zone codes
- Provide EXACTLY 24 recommendations, one for each hour in the forecast
- **MUST sort recommendations by priority field (1 = BEST, 24 = WORST)**
  The FIRST recommendation in the array MUST have priority=1 (the absolute best time/region to execute)
  The LAST recommendation MUST have priority=24 (the worst time/region)
  Sort the array in ASCENDING order by priority before returning
- Include detailed "reasoning" field for EACH recommendation with specific tradeoff analysis
- For transfer_cost_usd: Use the EXACT per-execution cost from "Region Comparison" section
- For emissions_grams: Use the EXACT per-execution emissions from "Region Comparison" section
- Return ONLY valid JSON, no additional text or markdown formatting.
"""

    return prompt
