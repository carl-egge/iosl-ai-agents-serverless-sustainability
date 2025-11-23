#!/usr/bin/env python3
"""
Carbon-Aware Serverless Function Scheduler - Google Cloud Run Version
Uses Electricity Maps API for carbon intensity forecasts and Google Gemini for scheduling decisions.
Writes results to Google Cloud Storage.
"""

import json
import requests
from datetime import datetime
from google.cloud import storage
from flask import Flask, jsonify, request
import google.generativeai as genai
import os

app = Flask(__name__)

# Configuration from environment variables
BUCKET_NAME = os.environ.get('GCS_BUCKET_NAME', 'your-bucket-name')
ELECTRICITYMAPS_TOKEN = os.environ.get('ELECTRICITYMAPS_TOKEN')
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

# Google Cloud Run European regions mapped to Electricity Maps zones
# GCloud Region -> Electricity Maps Zone
REGIONS = {
    "europe-north1": {"name": "Finland", "emaps_zone": "FI", "gcloud_region": "europe-north1"},
    "europe-north2": {"name": "Stockholm, Sweden", "emaps_zone": "SE-SE3", "gcloud_region": "europe-north2"},
    "europe-west1": {"name": "Belgium", "emaps_zone": "BE", "gcloud_region": "europe-west1"},
    "europe-west2": {"name": "London, UK", "emaps_zone": "GB", "gcloud_region": "europe-west2"},
    "europe-west3": {"name": "Frankfurt, Germany", "emaps_zone": "DE", "gcloud_region": "europe-west3"},
    "europe-west4": {"name": "Netherlands", "emaps_zone": "NL", "gcloud_region": "europe-west4"},
    "europe-west6": {"name": "Zurich, Switzerland", "emaps_zone": "CH", "gcloud_region": "europe-west6"},
    "europe-west8": {"name": "Milan, Italy", "emaps_zone": "IT-NO", "gcloud_region": "europe-west8"},
    "europe-west9": {"name": "Paris, France", "emaps_zone": "FR", "gcloud_region": "europe-west9"},
    "europe-west10": {"name": "Berlin, Germany", "emaps_zone": "DE", "gcloud_region": "europe-west10"},
    "europe-west12": {"name": "Turin, Italy", "emaps_zone": "IT-NO", "gcloud_region": "europe-west12"},
    "europe-central2": {"name": "Warsaw, Poland", "emaps_zone": "PL", "gcloud_region": "europe-central2"},
    "europe-southwest1": {"name": "Madrid, Spain", "emaps_zone": "ES", "gcloud_region": "europe-southwest1"},
}

# Default function metadata (can be overridden via request)
DEFAULT_FUNCTION_METADATA = {
    "function_id": "dummy_function",
    "runtime_ms": 5000,
    "memory_mb": 1024,
    "instant_execution": False,
    "description": "Default serverless function"
}


def get_carbon_forecast_electricitymaps(zone, horizon_hours=24):
    """Fetch carbon intensity forecast from Electricity Maps API."""
    if not ELECTRICITYMAPS_TOKEN:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    forecast_url = 'https://api.electricitymaps.com/v3/carbon-intensity/forecast'
    headers = {'auth-token': ELECTRICITYMAPS_TOKEN}
    params = {
        'zone': zone,
        'horizonHours': horizon_hours
    }

    response = requests.get(forecast_url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        return data.get('forecast', [])
    else:
        raise Exception(f"Electricity Maps API failed for zone {zone}: {response.status_code} - {response.text}")


def get_carbon_forecasts_all_regions():
    """Fetch carbon forecasts for all configured regions from Electricity Maps."""
    forecasts = {}
    failed_regions = []

    for region_key, region_info in REGIONS.items():
        try:
            forecast = get_carbon_forecast_electricitymaps(region_info['emaps_zone'])
            forecasts[region_key] = {
                'name': region_info['name'],
                'gcloud_region': region_info['gcloud_region'],
                'emaps_zone': region_info['emaps_zone'],
                'forecast': forecast
            }
            print(f"Fetched forecast for {region_key} ({region_info['name']}) - {len(forecast)} data points")
        except Exception as e:
            print(f"Failed to fetch forecast for {region_key}: {e}")
            failed_regions.append(region_key)

    if not forecasts:
        raise Exception("Failed to fetch forecasts for all regions")

    return forecasts, failed_regions


def format_forecast_for_llm(forecasts):
    """Format carbon forecasts into a concise string for LLM."""
    first_region = next(iter(forecasts.values()))
    start_time = datetime.fromisoformat(first_region['forecast'][0]['datetime'].replace('Z', '+00:00'))

    formatted = f"Carbon Intensity Forecast (gCO2eq/kWh) for next 24 hours starting {start_time.strftime('%Y-%m-%d %H:%M')}:\n\n"

    for region_key, region_data in forecasts.items():
        formatted += f"{region_key} ({region_data['name']}):\n"

        hourly_values = []
        for point in region_data['forecast'][:24]:
            dt = datetime.fromisoformat(point['datetime'].replace('Z', '+00:00'))
            carbon = point['carbonIntensity']
            hourly_values.append(f"  {dt.strftime('%Y-%m-%d %H:%M')} - {carbon} gCO2eq/kWh")

        formatted += "\n".join(hourly_values) + "\n\n"

    return formatted


def create_scheduling_prompt(function_metadata, carbon_forecasts_formatted):
    """Create the prompt for Gemini LLM."""
    instant_note = "INSTANT EXECUTION REQUIRED" if function_metadata['instant_execution'] else "FLEXIBLE DEADLINE"

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


def get_gemini_schedule(function_metadata, carbon_forecasts):
    """Use Google Gemini to create optimal execution schedule."""
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.5-flash')

    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)
    prompt = create_scheduling_prompt(function_metadata, carbon_forecasts_formatted)

    print("Sending request to Gemini API...")
    response = model.generate_content(prompt)

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
        print(f"Error parsing Gemini response as JSON: {e}")
        print(f"Raw response:\n{response_text}")
        raise


def write_to_gcs(data, blob_name):
    """Write JSON data to Google Cloud Storage."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type='application/json'
    )

    print(f"Written to gs://{BUCKET_NAME}/{blob_name}")
    return f"gs://{BUCKET_NAME}/{blob_name}"


def run_scheduler(function_metadata=None):
    """Main scheduling logic."""
    if function_metadata is None:
        function_metadata = DEFAULT_FUNCTION_METADATA

    print("=" * 60)
    print("Carbon-Aware Serverless Function Scheduler")
    print("=" * 60)

    # Step 1: Fetch carbon forecasts
    print("\n1. Fetching carbon intensity forecasts from Electricity Maps...")
    carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions()

    # Save raw forecast data to GCS
    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": carbon_forecasts,
        "failed_regions": failed_regions
    }
    forecast_path = write_to_gcs(forecast_data, 'carbon_forecasts.json')

    # Step 2: Get schedule from Gemini
    print("\n2. Generating optimal execution schedule with Gemini...")
    schedule = get_gemini_schedule(function_metadata, carbon_forecasts)

    # Add metadata to schedule
    schedule['metadata'] = {
        'generated_at': datetime.now().isoformat(),
        'function_metadata': function_metadata,
        'regions_used': list(carbon_forecasts.keys()),
        'failed_regions': failed_regions
    }

    # Step 3: Save schedule to GCS
    print("\n3. Saving schedule to Cloud Storage...")
    schedule_path = write_to_gcs(schedule, 'execution_schedule.json')

    print("\n" + "=" * 60)
    print("Scheduling complete!")
    print("=" * 60)

    return schedule, forecast_path, schedule_path


@app.route('/run', methods=['POST', 'GET'])
def run():
    """Endpoint to trigger the carbon-aware scheduler."""
    try:
        # Get function metadata from request body if provided
        function_metadata = None
        if request.method == 'POST' and request.is_json:
            function_metadata = request.get_json()

        print("Running carbon-aware scheduler...")
        schedule, forecast_path, schedule_path = run_scheduler(function_metadata)

        # Get top 5 recommendations for response
        recommendations = schedule.get('recommendations', [])
        sorted_recs = sorted(recommendations, key=lambda x: x.get('priority', 999))
        top_5 = sorted_recs[:5]

        return jsonify({
            "status": "success",
            "message": "Carbon-aware schedule generated successfully",
            "schedule_location": schedule_path,
            "forecast_location": forecast_path,
            "top_5_recommendations": top_5,
            "total_recommendations": len(recommendations)
        }), 200

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "healthy",
        "service": "carbon-aware-scheduler",
        "bucket": BUCKET_NAME,
        "has_emaps_token": bool(ELECTRICITYMAPS_TOKEN),
        "has_gemini_key": bool(GEMINI_API_KEY)
    }), 200


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
