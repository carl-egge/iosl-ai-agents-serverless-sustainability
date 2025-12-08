#!/usr/bin/env python3
"""
Carbon-Aware Serverless Function Scheduler - GCP Agent
Reads function metadata from GCS bucket and generates carbon-aware schedules.
All configuration and function info is loaded from gs://faas-scheduling-us-east1/

DEPLOYMENT NOTE: Deploy this file along with prompts.py and gcp_config_loader.py
"""

import json
import os
from datetime import datetime
from google.cloud import storage
from flask import Flask, jsonify
import google.generativeai as genai
import requests

# Import shared logic from other modules
from prompts import create_gcp_prompt
from gcp_config_loader import format_region_costs_for_llm

# Configuration from environment variables
BUCKET_NAME = "faas-scheduling-us-east1"
ELECTRICITYMAPS_TOKEN = os.environ.get("ELECTRICITYMAPS_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# GCS paths for configuration files
STATIC_CONFIG_PATH = "static_config.json"
FUNCTION_METADATA_PATH = "function_metadata.json"

# Cache for static config
_static_config_cache = None


def read_from_gcs(blob_name):
    """Read JSON data from Google Cloud Storage."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)

    content = blob.download_as_string()
    return json.loads(content)


def write_to_gcs(data, blob_name):
    """Write JSON data to Google Cloud Storage."""
    storage_client = storage.Client()
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json"
    )

    print(f"Written to gs://{BUCKET_NAME}/{blob_name}")
    return f"gs://{BUCKET_NAME}/{blob_name}"


def load_static_config():
    """Load static configuration from GCS bucket."""
    global _static_config_cache
    if _static_config_cache is None:
        print(f"Loading static_config.json from gs://{BUCKET_NAME}/{STATIC_CONFIG_PATH}")
        _static_config_cache = read_from_gcs(STATIC_CONFIG_PATH)
    return _static_config_cache


def load_function_metadata():
    """Load function metadata from GCS bucket."""
    print(f"Loading function_metadata.json from gs://{BUCKET_NAME}/{FUNCTION_METADATA_PATH}")
    return read_from_gcs(FUNCTION_METADATA_PATH)


def get_carbon_forecast_electricitymaps(zone, horizon_hours=24):
    """Fetch carbon intensity forecast from Electricity Maps API."""
    if not ELECTRICITYMAPS_TOKEN:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    forecast_url = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
    headers = {"auth-token": ELECTRICITYMAPS_TOKEN}
    params = {
        "zone": zone,
        "horizonHours": horizon_hours,
    }

    response = requests.get(forecast_url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        return data.get("forecast", [])
    else:
        raise Exception(
            f"Electricity Maps API failed for zone {zone}: {response.status_code} - {response.text}"
        )


def get_carbon_forecasts_all_regions(allowed_regions=None):
    """
    Fetch carbon forecasts for configured regions from Electricity Maps.

    Args:
        allowed_regions: Optional list of region codes to fetch. If None, fetches all European regions.
                        Example: ["europe-north2", "europe-west1", "us-east1"]
    """
    static_config = load_static_config()

    # Determine which regions to fetch
    regions = {}

    if allowed_regions:
        # Use only the specified regions
        print(f"Filtering to allowed regions: {allowed_regions}")
        for region_code in allowed_regions:
            if region_code in static_config["regions"]:
                region_info = static_config["regions"][region_code]
                regions[region_code] = {
                    "name": region_info["name"],
                    "emaps_zone": region_info["electricity_maps_zone"],
                    "gcloud_region": region_code,
                }
            else:
                print(f"Warning: Region {region_code} not found in static_config")
    else:
        # Default: Get all European regions
        for region_code, region_info in static_config["regions"].items():
            if region_code.startswith("europe-"):
                regions[region_code] = {
                    "name": region_info["name"],
                    "emaps_zone": region_info["electricity_maps_zone"],
                    "gcloud_region": region_code,
                }

    forecasts = {}
    failed_regions = []

    for region_key, region_info in regions.items():
        try:
            forecast = get_carbon_forecast_electricitymaps(region_info["emaps_zone"])
            forecasts[region_key] = {
                "name": region_info["name"],
                "gcloud_region": region_info["gcloud_region"],
                "emaps_zone": region_info["emaps_zone"],
                "forecast": forecast,
            }
            print(
                f"Fetched forecast for {region_key} ({region_info['name']}) - {len(forecast)} data points"
            )
        except Exception as exc:
            print(f"Failed to fetch forecast for {region_key}: {exc}")
            failed_regions.append(region_key)

    if not forecasts:
        raise Exception("Failed to fetch forecasts for all regions")

    return forecasts, failed_regions


def format_forecast_for_llm(forecasts):
    """Format carbon forecasts into a concise string for LLM."""
    first_region = next(iter(forecasts.values()))
    start_time = datetime.fromisoformat(
        first_region["forecast"][0]["datetime"].replace("Z", "+00:00")
    )

    formatted = (
        "Carbon Intensity Forecast (gCO2eq/kWh) for next 24 hours starting "
        f"{start_time.strftime('%Y-%m-%d %H:%M')}:\n\n"
    )

    for region_key, region_data in forecasts.items():
        formatted += f"{region_key} ({region_data['name']}):\n"

        hourly_values = []
        for point in region_data["forecast"][:24]:
            dt = datetime.fromisoformat(point["datetime"].replace("Z", "+00:00"))
            carbon = point["carbonIntensity"]
            hourly_values.append(
                f"  {dt.strftime('%Y-%m-%d %H:%M')} - {carbon} gCO2eq/kWh"
            )

        formatted += "\n".join(hourly_values) + "\n\n"

    return formatted


def _generate_with_gemini(prompt, log_message=None):
    """Shared Gemini invocation and JSON parsing."""
    if not GEMINI_API_KEY:
        raise Exception("GEMINI_API_KEY environment variable not set")

    if log_message:
        print(log_message)

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")

    response = model.generate_content(prompt)
    response_text = response.text.strip()

    # Remove markdown code blocks if present
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]

    response_text = response_text.strip()

    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        print(f"Error parsing Gemini response as JSON: {exc}")
        print(f"Raw response:\n{response_text}")
        raise


def parse_natural_language_request(user_description: str) -> dict:
    """
    Convert natural language function description to structured metadata using Gemini.

    Args:
        user_description: Natural language description of the serverless function

    Returns:
        Dictionary with structured function metadata
    """
    prompt = f"""You are a serverless infrastructure expert. Convert this natural language function description into structured metadata for carbon-aware scheduling.

User's description:
\"\"\"{user_description}\"\"\"

Extract and estimate these parameters:
1. function_id: Create a descriptive ID (snake_case, lowercase, no spaces)
2. runtime_ms: Estimate execution time in milliseconds
   - Simple API calls: 50-200ms
   - Image processing: 500-2000ms
   - Video processing: 30,000-300,000ms
   - ML inference: 1,000-10,000ms
   - Data transformations: 100-5,000ms
3. memory_mb: Estimate memory requirement (choose from: 128, 256, 512, 1024, 2048, 4096)
4. instant_execution: true if time-sensitive/real-time, false if batch/flexible
5. description: Clean technical summary of the function (one sentence)
6. data_input_gb: Estimate input data size per invocation (in GB)
7. data_output_gb: Estimate output data size per invocation (in GB)
8. source_location: Extract if mentioned (e.g., us-east1, europe-west1), default to "us-east1"
9. invocations_per_day: Extract frequency or estimate based on use case
10. allowed_regions: Extract if mentioned, otherwise leave empty array []

IMPORTANT estimation guidelines:
- Be conservative with estimates (overestimate resource needs for safety)
- If runtime is uncertain, multiply your estimate by 2x
- For memory, always round UP to the next tier
- Include ALL data transfer (downloads AND uploads)
- Consider peak loads, not just average usage

Return ONLY valid JSON matching this exact schema (no markdown, no explanations):
{{
  "function_id": "string",
  "runtime_ms": number,
  "memory_mb": number,
  "instant_execution": boolean,
  "description": "string",
  "data_input_gb": number,
  "data_output_gb": number,
  "source_location": "string",
  "invocations_per_day": number,
  "allowed_regions": ["array of region codes or empty"],
  "confidence_score": number (0.0-1.0, how confident you are in these estimates),
  "assumptions": ["list of key assumptions made during estimation"],
  "warnings": ["list of potential concerns or uncertainties"]
}}

Example output:
{{
  "function_id": "image_resizer",
  "runtime_ms": 1200,
  "memory_mb": 512,
  "instant_execution": true,
  "description": "Resize user-uploaded images to multiple thumbnail sizes",
  "data_input_gb": 0.008,
  "data_output_gb": 0.012,
  "source_location": "us-east1",
  "invocations_per_day": 500,
  "allowed_regions": [],
  "confidence_score": 0.75,
  "assumptions": [
    "Estimated 1200ms based on typical image processing with multiple outputs",
    "Input: single 8MB image",
    "Output: 3 resized versions totaling 12MB"
  ],
  "warnings": [
    "Runtime could vary significantly based on image dimensions",
    "Memory usage may spike for very large images"
  ]
}}"""

    print(f"Parsing natural language request with Gemini...")
    return _generate_with_gemini(prompt, log_message="Extracting function metadata from natural language...")


def get_gemini_schedule(function_metadata, carbon_forecasts):
    """Use Google Gemini to create optimal execution schedule."""
    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)

    # Load static config and format cost information using imported function
    static_config = load_static_config()
    cost_info = ""

    # Only include cost info if data transfer is specified
    if function_metadata.get("data_input_gb") or function_metadata.get("data_output_gb"):
        data_input_gb = function_metadata.get("data_input_gb", 0.0)
        data_output_gb = function_metadata.get("data_output_gb", 0.0)
        source_location = function_metadata.get("source_location")

        # Use imported format_region_costs_for_llm from config_loader
        cost_info = format_region_costs_for_llm(
            carbon_forecasts, data_input_gb, data_output_gb, source_location, static_config
        )

    # Use imported create_gcp_prompt from prompts
    prompt = create_gcp_prompt(function_metadata, carbon_forecasts_formatted, cost_info)

    return _generate_with_gemini(prompt, log_message="Sending request to Gemini API...")


def run_scheduler_for_function(function_name, function_metadata, carbon_forecasts):
    """Generate schedule for a single function."""
    print(f"\nGenerating schedule for function: {function_name}")
    print(f"  Runtime: {function_metadata.get('runtime_ms')}ms")
    print(f"  Memory: {function_metadata.get('memory_mb')}MB")
    print(f"  Instant execution: {function_metadata.get('instant_execution', False)}")

    # Generate schedule
    schedule = get_gemini_schedule(function_metadata, carbon_forecasts)

    # Add metadata
    schedule["metadata"] = {
        "generated_at": datetime.now().isoformat(),
        "function_metadata": function_metadata,
        "regions_used": list(carbon_forecasts.keys()),
    }

    # Save schedule to GCS
    schedule_filename = f"schedule_{function_name}.json"
    schedule_path = write_to_gcs(schedule, schedule_filename)

    return schedule, schedule_path


def run_scheduler():
    """Main scheduling logic for the GCP agent."""
    print("=" * 60)
    print("Carbon-Aware Serverless Function Scheduler - GCP Agent")
    print("=" * 60)

    # Step 1: Load function metadata from GCS
    print("\n1. Loading function metadata from GCS...")
    try:
        function_metadata_file = load_function_metadata()
    except Exception as exc:
        print(f"Error loading function_metadata.json: {exc}")
        raise Exception(
            f"Could not load function_metadata.json from gs://{BUCKET_NAME}/{FUNCTION_METADATA_PATH}. "
            "Please ensure the file exists and contains valid JSON."
        )

    # Extract functions to schedule
    functions_raw = function_metadata_file.get("functions", {})
    if not functions_raw:
        raise Exception("No functions found in function_metadata.json")

    print(f"Found {len(functions_raw)} function(s) to schedule:")
    for func_name in functions_raw.keys():
        print(f"  - {func_name}")

    # Step 1.5: Process functions - detect string vs object and parse natural language if needed
    print("\n1.5. Processing function metadata...")
    functions_to_schedule = {}

    for func_name, func_data in functions_raw.items():
        if isinstance(func_data, str):
            # Natural language description - parse it
            print(f"  {func_name}: Detected natural language description, parsing with Gemini...")
            try:
                parsed_metadata = parse_natural_language_request(func_data)
                # Override function_id with the key name from JSON
                parsed_metadata["function_id"] = func_name
                functions_to_schedule[func_name] = parsed_metadata
                print(f"    ✓ Parsed successfully (confidence: {parsed_metadata.get('confidence_score', 0):.2f})")

                # Show extracted info
                if parsed_metadata.get("assumptions"):
                    print(f"    Assumptions: {', '.join(parsed_metadata['assumptions'][:2])}")
                if parsed_metadata.get("warnings"):
                    print(f"    Warnings: {', '.join(parsed_metadata['warnings'][:2])}")
            except Exception as exc:
                print(f"    ✗ Failed to parse natural language: {exc}")
                raise Exception(f"Could not parse natural language description for function '{func_name}': {exc}")
        elif isinstance(func_data, dict):
            # Structured metadata - use directly
            print(f"  {func_name}: Using structured metadata directly")
            functions_to_schedule[func_name] = func_data
        else:
            raise Exception(
                f"Invalid format for function '{func_name}': must be either a string (natural language) "
                f"or object (structured metadata), got {type(func_data).__name__}"
            )

    # Step 2: Collect unique regions from all functions
    print("\n2. Determining regions to fetch...")
    all_allowed_regions = set()
    for func_name, func_metadata in functions_to_schedule.items():
        allowed_regions = func_metadata.get("allowed_regions")
        if allowed_regions:
            all_allowed_regions.update(allowed_regions)
            print(f"  {func_name} -> regions: {allowed_regions}")
        else:
            print(f"  {func_name} -> no region filter (will use all European regions)")

    # Step 3: Fetch carbon forecasts for all needed regions
    print("\n3. Fetching carbon intensity forecasts from Electricity Maps...")
    if all_allowed_regions:
        carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions(list(all_allowed_regions))
    else:
        carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions()

    # Save raw forecast data to GCS
    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": carbon_forecasts,
        "failed_regions": failed_regions,
    }
    forecast_path = write_to_gcs(forecast_data, "carbon_forecasts.json")

    # Step 4: Generate schedules for each function
    print("\n4. Generating optimal execution schedules with Gemini...")
    schedules = {}
    schedule_paths = {}

    for function_name, function_metadata in functions_to_schedule.items():
        try:
            # Filter carbon forecasts to only the allowed regions for this function
            allowed_regions = function_metadata.get("allowed_regions")
            if allowed_regions:
                filtered_forecasts = {k: v for k, v in carbon_forecasts.items() if k in allowed_regions}
                print(f"\n  Scheduling {function_name} with filtered regions: {list(filtered_forecasts.keys())}")
            else:
                filtered_forecasts = carbon_forecasts
                print(f"\n  Scheduling {function_name} with all available regions")

            schedule, schedule_path = run_scheduler_for_function(
                function_name, function_metadata, filtered_forecasts
            )
            schedules[function_name] = schedule
            schedule_paths[function_name] = schedule_path
        except Exception as exc:
            print(f"Error generating schedule for {function_name}: {exc}")
            schedules[function_name] = {"error": str(exc)}
            schedule_paths[function_name] = None

    print("\n" + "=" * 60)
    print("Scheduling complete!")
    print("=" * 60)

    return schedules, schedule_paths, forecast_path


# Flask app for Cloud Run deployment
app = Flask(__name__)


@app.route("/run", methods=["POST", "GET"])
def run():
    """Endpoint to trigger the carbon-aware scheduler."""
    try:
        print("Running carbon-aware scheduler...")
        schedules, schedule_paths, forecast_path = run_scheduler()

        # Prepare response with top recommendations for each function
        results = {}
        for function_name, schedule in schedules.items():
            if "error" in schedule:
                results[function_name] = {
                    "status": "error",
                    "message": schedule["error"],
                }
            else:
                recommendations = schedule.get("recommendations", [])
                sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))
                top_5 = sorted_recs[:5]

                results[function_name] = {
                    "status": "success",
                    "schedule_location": schedule_paths[function_name],
                    "top_5_recommendations": top_5,
                    "total_recommendations": len(recommendations),
                }

        return (
            jsonify(
                {
                    "status": "success",
                    "message": "Carbon-aware schedules generated successfully",
                    "forecast_location": forecast_path,
                    "functions": results,
                }
            ),
            200,
        )

    except Exception as exc:
        print(f"Error: {exc}")
        import traceback

        traceback.print_exc()
        return jsonify({"status": "error", "message": str(exc)}), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return (
        jsonify(
            {
                "status": "healthy",
                "service": "agent",
                "bucket": BUCKET_NAME,
                "has_emaps_token": bool(ELECTRICITYMAPS_TOKEN),
                "has_gemini_key": bool(GEMINI_API_KEY),
            }
        ),
        200,
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
