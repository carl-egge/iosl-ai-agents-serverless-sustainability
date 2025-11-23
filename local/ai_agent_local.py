#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carbon-Aware Serverless Function Scheduler
Uses Electricity Maps API for carbon intensity forecasts and Google Gemini for scheduling decisions.
"""

import json
import requests
from datetime import datetime, timedelta
import google.generativeai as genai
import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Fix encoding for Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

# Configuration
SAVE_FORECAST_DATA = True  # Set to True to save raw forecast data to JSON file

# Electricity Maps zones - Using actual zone codes
# Selected: Germany + France (required) + 8 lowest MINIMUM carbon intensity zones
# Rankings based on minimum carbon intensity (best possible performance)
REGIONS = {
    "DE": {"name": "Germany", "emaps_zone": "DE"},              # Min: 368, Avg: 408.2 gCO2eq/kWh
    "FR": {"name": "France", "emaps_zone": "FR"},               # Min: 33, Avg: 37.9 gCO2eq/kWh
    "SE-SE1": {"name": "Sweden North", "emaps_zone": "SE-SE1"}, # Min: 12, Avg: 14.0 gCO2eq/kWh
    "SE-SE2": {"name": "Sweden North-Central", "emaps_zone": "SE-SE2"}, # Min: 14, Avg: 14.4 gCO2eq/kWh
    "SE-SE3": {"name": "Sweden South-Central", "emaps_zone": "SE-SE3"}, # Min: 19, Avg: 22.4 gCO2eq/kWh
    "AU-TAS": {"name": "Australia Tasmania", "emaps_zone": "AU-TAS"},   # Min: 19, Avg: 20.6 gCO2eq/kWh
    "NO-NO2": {"name": "Norway Zone 2", "emaps_zone": "NO-NO2"},        # Min: 24, Avg: 24.8 gCO2eq/kWh
    "NO-NO1": {"name": "Norway Zone 1", "emaps_zone": "NO-NO1"},        # Min: 25, Avg: 25.0 gCO2eq/kWh
    "NO-NO5": {"name": "Norway Zone 5", "emaps_zone": "NO-NO5"},        # Min: 25, Avg: 25.0 gCO2eq/kWh
    "IS": {"name": "Iceland", "emaps_zone": "IS"},                      # Min: 28, Avg: 28.0 gCO2eq/kWh
}

def load_function_metadata(filepath="storage/function_metadata.json"):
    """Load function metadata from JSON file."""
    with open(filepath, 'r') as f:
        return json.load(f)

def get_carbon_forecast_electricitymaps(api_token, zone, horizon_hours=24):
    """
    Fetch carbon intensity forecast from Electricity Maps API.

    Args:
        api_token: Electricity Maps API token
        zone: Zone identifier (e.g., 'DE', 'FR')
        horizon_hours: Forecast horizon (6, 24, 48, or 72)

    Returns:
        List of forecast data points with carbonIntensity and datetime
    """
    forecast_url = 'https://api.electricitymaps.com/v3/carbon-intensity/forecast'
    headers = {'auth-token': api_token}
    params = {
        'zone': zone,
        'horizonHours': horizon_hours
    }

    response = requests.get(forecast_url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        forecast = data.get('forecast', [])
        return forecast
    else:
        raise Exception(f"Electricity Maps API failed for zone {zone}: {response.status_code} - {response.text}")

def get_carbon_forecasts_all_regions(api_token):
    """Fetch carbon forecasts for all configured regions from Electricity Maps."""
    forecasts = {}
    failed_regions = []

    for region_key, region_info in REGIONS.items():
        try:
            forecast = get_carbon_forecast_electricitymaps(api_token, region_info['emaps_zone'])
            forecasts[region_key] = {
                'name': region_info['name'],
                'forecast': forecast
            }
            print(f"✓ Fetched forecast for {region_key} ({region_info['name']}) - {len(forecast)} data points")
        except Exception as e:
            print(f"✗ Failed to fetch forecast for {region_key}: {e}")
            failed_regions.append(region_key)
            # Continue with other regions instead of raising

    if not forecasts:
        raise Exception("Failed to fetch forecasts for all regions. Please check your API token and try again.")

    if failed_regions:
        print(f"\nNote: {len(failed_regions)} region(s) failed: {', '.join(failed_regions)}")
        print("Continuing with available regions...\n")

    return forecasts

def format_forecast_for_llm(forecasts):
    """Format carbon forecasts into a concise string for LLM."""
    # Get the start time from the first forecast
    first_region = next(iter(forecasts.values()))
    start_time = datetime.fromisoformat(first_region['forecast'][0]['datetime'].replace('Z', '+00:00'))

    formatted = f"Carbon Intensity Forecast (gCO2eq/kWh) for next 24 hours starting {start_time.strftime('%Y-%m-%d %H:%M')}:\n\n"

    for region_key, region_data in forecasts.items():
        formatted += f"{region_key} ({region_data['name']}):\n"

        hourly_values = []
        for point in region_data['forecast'][:24]:  # Limit to 24 hours
            dt = datetime.fromisoformat(point['datetime'].replace('Z', '+00:00'))
            carbon = point['carbonIntensity']
            hourly_values.append(f"  {dt.strftime('%Y-%m-%d %H:%M')} - {carbon} gCO2eq/kWh")

        formatted += "\n".join(hourly_values) + "\n\n"

    return formatted

def create_scheduling_prompt(function_metadata, carbon_forecasts_formatted, forecasts):
    """Create the prompt for Gemini LLM."""

    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata['instant_execution'] else "FLEXIBLE DEADLINE"

    # Get the first forecast timestamp to include in prompt
    first_region = next(iter(forecasts.values()))
    start_time = datetime.fromisoformat(first_region['forecast'][0]['datetime'].replace('Z', '+00:00'))

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

def get_gemini_schedule(function_metadata, carbon_forecasts):
    """Use Google Gemini to create optimal execution schedule."""

    # Configure Gemini
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        raise Exception("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=api_key)
    # Using Gemini 2.5 Flash - fast and cost-effective model
    model = genai.GenerativeModel('gemini-2.5-flash')

    # Format forecast data for LLM
    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)

    # Create prompt
    prompt = create_scheduling_prompt(function_metadata, carbon_forecasts_formatted, carbon_forecasts)

    print("\n" + "="*60)
    print("Sending request to Gemini API...")
    print("="*60)

    # Get response from Gemini
    response = model.generate_content(prompt)

    # Parse JSON response
    response_text = response.text.strip()

    # Remove markdown code blocks if present
    if response_text.startswith('```json'):
        response_text = response_text[7:]
    if response_text.startswith('```'):
        response_text = response_text[3:]
    if response_text.endswith('```'):
        response_text = response_text[:-3]

    response_text = response_text.strip()

    try:
        schedule = json.loads(response_text)
        return schedule
    except json.JSONDecodeError as e:
        print(f"\nError parsing Gemini response as JSON: {e}")
        print(f"Raw response:\n{response_text}")
        raise

def save_schedule(schedule, filepath="storage/execution_schedule.json"):
    """Save the execution schedule to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(schedule, f, indent=2)
    print(f"\n✓ Schedule saved to {filepath}")

def save_forecast_data(forecasts, filepath="storage/carbon_forecasts.json"):
    """Save raw forecast data to JSON file for analysis."""
    # Convert to serializable format
    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": {}
    }

    for region_key, region_data in forecasts.items():
        forecast_data["regions"][region_key] = {
            "name": region_data['name'],
            "forecast": region_data['forecast']
        }

    with open(filepath, 'w') as f:
        json.dump(forecast_data, f, indent=2)
    print(f"✓ Forecast data saved to {filepath}")

def print_schedule_summary(schedule):
    """Print a human-readable summary of the schedule."""
    print("\n" + "="*60)
    print("EXECUTION SCHEDULE SUMMARY (Best to Worst)")
    print("="*60)

    recommendations = schedule.get('recommendations', [])

    # Sort by priority
    sorted_recs = sorted(recommendations, key=lambda x: x.get('priority', 999))

    print(f"\nTop 5 Best Execution Times:")
    print("-" * 60)
    for i, rec in enumerate(sorted_recs[:5], 1):
        dt = rec.get('datetime', 'N/A')
        region = rec.get('region', 'N/A')
        carbon = rec.get('carbon_intensity', 'N/A')
        priority = rec.get('priority', 'N/A')

        print(f"{i}. {dt} → {region:20s} ({carbon} gCO2eq/kWh) [Priority: {priority}]")

    print(f"\nWorst 3 Execution Times:")
    print("-" * 60)
    for i, rec in enumerate(sorted_recs[-3:], 1):
        dt = rec.get('datetime', 'N/A')
        region = rec.get('region', 'N/A')
        carbon = rec.get('carbon_intensity', 'N/A')
        priority = rec.get('priority', 'N/A')

        print(f"{i}. {dt} → {region:20s} ({carbon} gCO2eq/kWh) [Priority: {priority}]")

def main():
    """Main execution flow."""
    print("="*60)
    print("Carbon-Aware Serverless Function Scheduler Agent")
    print("Using Electricity Maps API for Carbon Intensity Data")
    print("="*60)

    # Load function metadata
    print("\n1. Loading function metadata...")
    function_metadata = load_function_metadata()
    print(f"   Function: {function_metadata['function_id']}")
    print(f"   Runtime: {function_metadata['runtime_ms']}ms")
    print(f"   Instant execution: {function_metadata['instant_execution']}")

    # Get carbon forecasts from Electricity Maps
    print("\n2. Fetching carbon intensity forecasts from Electricity Maps...")

    emaps_token = os.environ.get('ELECTRICITYMAPS_TOKEN')
    if not emaps_token:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set. Please set it to your Electricity Maps API token.")

    print(f"   Configured regions:")
    for region_key, region_info in REGIONS.items():
        print(f"   - {region_key}: {region_info['name']} (zone: {region_info['emaps_zone']})")
    print()

    carbon_forecasts = get_carbon_forecasts_all_regions(emaps_token)

    # Optionally save raw forecast data
    if SAVE_FORECAST_DATA:
        print("\n   Saving raw forecast data...")
        save_forecast_data(carbon_forecasts)

    # Get schedule from Gemini
    print("\n3. Generating optimal execution schedule with Gemini...")
    schedule = get_gemini_schedule(function_metadata, carbon_forecasts)

    # Save schedule
    print("\n4. Saving schedule...")
    save_schedule(schedule)

    # Print summary
    print_schedule_summary(schedule)

    print("\n" + "="*60)
    print("✓ Scheduling complete!")
    print("="*60)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
