#!/usr/bin/env python3
"""
Carbon-Aware Serverless Function Scheduler
Uses Electricity Maps API for carbon intensity forecasts and Google Gemini for scheduling decisions.
Shared planner logic for both local runs and the Cloud Run deployment.
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # Optional dependency for local runs
    load_dotenv = None

from agent.prompts import create_gcp_prompt, create_local_prompt
from sample_functions.simple_addition import (
    SIMPLE_ADDITION_METADATA,
    SIMPLE_ADDITION_METADATA_INSTANT,
)
from sample_functions.simple_api_call import (
    SIMPLE_API_CALL_METADATA,
    SIMPLE_API_CALL_METADATA_INSTANT,
)

# Base paths (used for data files)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"

# Fix encoding for Windows (mirrors original local script behavior)
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _fetch_carbon_forecast(api_token, zone, horizon_hours=24):
    """Shared Electricity Maps forecast fetch."""
    forecast_url = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
    headers = {"auth-token": api_token}
    params = {
        "zone": zone,
        "horizonHours": horizon_hours,
    }

    response = requests.get(forecast_url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        return data.get("forecast", [])
    else:
        raise Exception(f"Electricity Maps API failed for zone {zone}: {response.status_code} - {response.text}")


def format_forecast_for_llm(forecasts):
    """Format carbon forecasts into a concise string for LLM."""
    first_region = next(iter(forecasts.values()))
    start_time = datetime.fromisoformat(first_region["forecast"][0]["datetime"].replace("Z", "+00:00"))

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
            hourly_values.append(f"  {dt.strftime('%Y-%m-%d %H:%M')} - {carbon} gCO2eq/kWh")

        formatted += "\n".join(hourly_values) + "\n\n"

    return formatted


def _generate_schedule(api_key, prompt, log_message=None):
    """Shared Gemini invocation and JSON parsing."""
    if not api_key:
        raise Exception("GEMINI_API_KEY environment variable not set")

    if log_message:
        print(log_message)

    import google.generativeai as genai

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-2.5-flash")

    response = model.generate_content(prompt)
    response_text = response.text.strip()

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


# ---------------------------------------------------------------------------
# GCP / Cloud Run scheduler (from gcloud/agent/main.py)
# ---------------------------------------------------------------------------
GCP_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "your-bucket-name")
GCP_ELECTRICITYMAPS_TOKEN = os.environ.get("ELECTRICITYMAPS_TOKEN")
GCP_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Google Cloud Run European regions mapped to Electricity Maps zones
# GCloud Region -> Electricity Maps Zone
GCP_REGIONS = {
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
GCP_DEFAULT_FUNCTION_METADATA = {
    "function_id": "dummy_function",
    "runtime_ms": 5000,
    "memory_mb": 1024,
    "instant_execution": False,
    "description": "Default serverless function",
}


def get_carbon_forecast_electricitymaps_gcp(zone, horizon_hours=24):
    """Fetch carbon intensity forecast from Electricity Maps API (GCP workflow)."""
    if not GCP_ELECTRICITYMAPS_TOKEN:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    return _fetch_carbon_forecast(GCP_ELECTRICITYMAPS_TOKEN, zone, horizon_hours)


def get_carbon_forecasts_all_regions_gcp():
    """Fetch carbon forecasts for all configured regions from Electricity Maps."""
    forecasts = {}
    failed_regions = []

    for region_key, region_info in GCP_REGIONS.items():
        try:
            forecast = get_carbon_forecast_electricitymaps_gcp(region_info["emaps_zone"])
            forecasts[region_key] = {
                "name": region_info["name"],
                "gcloud_region": region_info["gcloud_region"],
                "emaps_zone": region_info["emaps_zone"],
                "forecast": forecast,
            }
            print(f"Fetched forecast for {region_key} ({region_info['name']}) - {len(forecast)} data points")
        except Exception as exc:  # Keep behavior identical to original logging
            print(f"Failed to fetch forecast for {region_key}: {exc}")
            failed_regions.append(region_key)

    if not forecasts:
        raise Exception("Failed to fetch forecasts for all regions")

    return forecasts, failed_regions


def get_gemini_schedule_gcp(function_metadata, carbon_forecasts):
    """Use Google Gemini to create optimal execution schedule (GCP workflow)."""
    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)
    prompt = create_gcp_prompt(function_metadata, carbon_forecasts_formatted)
    return _generate_schedule(GCP_GEMINI_API_KEY, prompt, log_message="Sending request to Gemini API...")


def write_to_gcs(data, blob_name):
    """Write JSON data to Google Cloud Storage."""
    from google.cloud import storage

    storage_client = storage.Client()
    bucket = storage_client.bucket(GCP_BUCKET_NAME)
    blob = bucket.blob(blob_name)

    blob.upload_from_string(
        json.dumps(data, indent=2),
        content_type="application/json",
    )

    print(f"Written to gs://{GCP_BUCKET_NAME}/{blob_name}")
    return f"gs://{GCP_BUCKET_NAME}/{blob_name}"


def run_scheduler(function_metadata=None):
    """Main scheduling logic for the GCP deployment."""
    if function_metadata is None:
        function_metadata = GCP_DEFAULT_FUNCTION_METADATA

    print("=" * 60)
    print("Carbon-Aware Serverless Function Scheduler")
    print("=" * 60)

    # Step 1: Fetch carbon forecasts
    print("\n1. Fetching carbon intensity forecasts from Electricity Maps...")
    carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions_gcp()

    # Save raw forecast data to GCS
    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": carbon_forecasts,
        "failed_regions": failed_regions,
    }
    forecast_path = write_to_gcs(forecast_data, "carbon_forecasts.json")

    # Step 2: Get schedule from Gemini
    print("\n2. Generating optimal execution schedule with Gemini...")
    schedule = get_gemini_schedule_gcp(function_metadata, carbon_forecasts)

    # Add metadata to schedule
    schedule["metadata"] = {
        "generated_at": datetime.now().isoformat(),
        "function_metadata": function_metadata,
        "regions_used": list(carbon_forecasts.keys()),
        "failed_regions": failed_regions,
    }

    # Step 3: Save schedule to GCS
    print("\n3. Saving schedule to Cloud Storage...")
    schedule_path = write_to_gcs(schedule, "execution_schedule.json")

    print("\n" + "=" * 60)
    print("Scheduling complete!")
    print("=" * 60)

    return schedule, forecast_path, schedule_path


def create_gcp_app():
    """Create the Flask app for Cloud Run deployment."""
    from flask import Flask, jsonify, request

    app = Flask(__name__)

    @app.route("/run", methods=["POST", "GET"])
    def run():
        """Endpoint to trigger the carbon-aware scheduler."""
        try:
            function_metadata = None
            if request.method == "POST" and request.is_json:
                function_metadata = request.get_json()

            print("Running carbon-aware scheduler...")
            schedule, forecast_path, schedule_path = run_scheduler(function_metadata)

            recommendations = schedule.get("recommendations", [])
            sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))
            top_5 = sorted_recs[:5]

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": "Carbon-aware schedule generated successfully",
                        "schedule_location": schedule_path,
                        "forecast_location": forecast_path,
                        "top_5_recommendations": top_5,
                        "total_recommendations": len(recommendations),
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
                    "service": "carbon-aware-scheduler",
                    "bucket": GCP_BUCKET_NAME,
                    "has_emaps_token": bool(GCP_ELECTRICITYMAPS_TOKEN),
                    "has_gemini_key": bool(GCP_GEMINI_API_KEY),
                }
            ),
            200,
        )

    return app


# ---------------------------------------------------------------------------
# Local planner (from local/ai_agent_local.py)
# ---------------------------------------------------------------------------
SAVE_FORECAST_DATA = True  # Set to True to save raw forecast data to JSON file

# Electricity Maps zones - Using actual zone codes
# Selected: Germany + France (required) + 8 lowest MINIMUM carbon intensity zones
# Rankings based on minimum carbon intensity (best possible performance)
LOCAL_REGIONS = {
    "DE": {"name": "Germany", "emaps_zone": "DE"},  # Min: 368, Avg: 408.2 gCO2eq/kWh
    "FR": {"name": "France", "emaps_zone": "FR"},  # Min: 33, Avg: 37.9 gCO2eq/kWh
    "SE-SE1": {"name": "Sweden North", "emaps_zone": "SE-SE1"},  # Min: 12, Avg: 14.0 gCO2eq/kWh
    "SE-SE2": {"name": "Sweden North-Central", "emaps_zone": "SE-SE2"},  # Min: 14, Avg: 14.4 gCO2eq/kWh
    "SE-SE3": {"name": "Sweden South-Central", "emaps_zone": "SE-SE3"},  # Min: 19, Avg: 22.4 gCO2eq/kWh
    "AU-TAS": {"name": "Australia Tasmania", "emaps_zone": "AU-TAS"},  # Min: 19, Avg: 20.6 gCO2eq/kWh
    "NO-NO2": {"name": "Norway Zone 2", "emaps_zone": "NO-NO2"},  # Min: 24, Avg: 24.8 gCO2eq/kWh
    "NO-NO1": {"name": "Norway Zone 1", "emaps_zone": "NO-NO1"},  # Min: 25, Avg: 25.0 gCO2eq/kWh
    "NO-NO5": {"name": "Norway Zone 5", "emaps_zone": "NO-NO5"},  # Min: 25, Avg: 25.0 gCO2eq/kWh
    "IS": {"name": "Iceland", "emaps_zone": "IS"},  # Min: 28, Avg: 28.0 gCO2eq/kWh
}


def load_function_metadata(filepath=None):
    """Load function metadata from JSON file."""
    metadata_path = Path(filepath) if filepath else DATA_SAMPLE_DIR / "function_metadata.json"
    with open(metadata_path, "r") as file:
        return json.load(file)


def get_carbon_forecast_electricitymaps_local(api_token, zone, horizon_hours=24):
    """
    Fetch carbon intensity forecast from Electricity Maps API.

    Args:
        api_token: Electricity Maps API token
        zone: Zone identifier (e.g., 'DE', 'FR')
        horizon_hours: Forecast horizon (6, 24, 48, or 72)

    Returns:
        List of forecast data points with carbonIntensity and datetime
    """
    if not api_token:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    return _fetch_carbon_forecast(api_token, zone, horizon_hours)


def get_carbon_forecasts_all_regions_local(api_token):
    """Fetch carbon forecasts for all configured regions from Electricity Maps."""
    forecasts = {}
    failed_regions = []

    for region_key, region_info in LOCAL_REGIONS.items():
        try:
            forecast = get_carbon_forecast_electricitymaps_local(api_token, region_info["emaps_zone"])
            forecasts[region_key] = {
                "name": region_info["name"],
                "forecast": forecast,
            }
            print(f"[+] Fetched forecast for {region_key} ({region_info['name']}) - {len(forecast)} data points")
        except Exception as exc:
            print(f"[!] Failed to fetch forecast for {region_key}: {exc}")
            failed_regions.append(region_key)

    if not forecasts:
        raise Exception("Failed to fetch forecasts for all regions. Please check your API token and try again.")

    if failed_regions:
        print(f"\nNote: {len(failed_regions)} region(s) failed: {', '.join(failed_regions)}")
        print("Continuing with available regions...\n")

    return forecasts


def get_gemini_schedule_local(function_metadata, carbon_forecasts):
    """Use Google Gemini to create optimal execution schedule."""
    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)
    prompt = create_local_prompt(function_metadata, carbon_forecasts_formatted, carbon_forecasts)

    log_message = "\n" + "=" * 60 + "\nSending request to Gemini API...\n" + "=" * 60
    return _generate_schedule(os.environ.get("GEMINI_API_KEY"), prompt, log_message=log_message)


def save_schedule(schedule, filepath=None):
    """Save the execution schedule to JSON file."""
    target_path = Path(filepath) if filepath else DATA_SAMPLE_DIR / "execution_schedule.json"
    with open(target_path, "w") as file:
        json.dump(schedule, file, indent=2)
    print(f"\n[+] Schedule saved to {target_path}")


def save_forecast_data(forecasts, filepath=None):
    """Save raw forecast data to JSON file for analysis."""
    target_path = Path(filepath) if filepath else DATA_SAMPLE_DIR / "carbon_forecasts.json"

    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": {},
    }

    for region_key, region_data in forecasts.items():
        forecast_data["regions"][region_key] = {
            "name": region_data["name"],
            "forecast": region_data["forecast"],
        }

    with open(target_path, "w") as file:
        json.dump(forecast_data, file, indent=2)
    print(f"[+] Forecast data saved to {target_path}")


def print_schedule_summary(schedule):
    """Print a human-readable summary of the schedule."""
    print("\n" + "=" * 60)
    print("EXECUTION SCHEDULE SUMMARY (Best to Worst)")
    print("=" * 60)

    recommendations = schedule.get("recommendations", [])

    sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))

    print("\nTop 5 Best Execution Times:")
    print("-" * 60)
    for i, rec in enumerate(sorted_recs[:5], 1):
        dt = rec.get("datetime", "N/A")
        region = rec.get("region", "N/A")
        carbon = rec.get("carbon_intensity", "N/A")
        priority = rec.get("priority", "N/A")

        print(f"{i}. {dt} -> {region:20s} ({carbon} gCO2eq/kWh) [Priority: {priority}]")

    print("\nWorst 3 Execution Times:")
    print("-" * 60)
    for i, rec in enumerate(sorted_recs[-3:], 1):
        dt = rec.get("datetime", "N/A")
        region = rec.get("region", "N/A")
        carbon = rec.get("carbon_intensity", "N/A")
        priority = rec.get("priority", "N/A")

        print(f"{i}. {dt} -> {region:20s} ({carbon} gCO2eq/kWh) [Priority: {priority}]")


def run_planner():
    """
    Entry function that executes the existing planner workflow for local use.
    Mirrors local/ai_agent_local.py main() logic with path updates only.
    """
    if load_dotenv:
        load_dotenv()

    print("=" * 60)
    print("Carbon-Aware Serverless Function Scheduler Agent")
    print("Using Electricity Maps API for Carbon Intensity Data")
    print("=" * 60)

    # Load function metadata
    print("\n1. Loading function metadata...")
    function_metadata = load_function_metadata()
    print(f"   Function: {function_metadata['function_id']}")
    print(f"   Runtime: {function_metadata['runtime_ms']}ms")
    print(f"   Instant execution: {function_metadata['instant_execution']}")

    # Get carbon forecasts from Electricity Maps
    print("\n2. Fetching carbon intensity forecasts from Electricity Maps...")

    emaps_token = os.environ.get("ELECTRICITYMAPS_TOKEN")
    if not emaps_token:
        raise Exception(
            "ELECTRICITYMAPS_TOKEN environment variable not set. Please set it to your Electricity Maps API token."
        )

    print("   Configured regions:")
    for region_key, region_info in LOCAL_REGIONS.items():
        print(f"   - {region_key}: {region_info['name']} (zone: {region_info['emaps_zone']})")
    print()

    carbon_forecasts = get_carbon_forecasts_all_regions_local(emaps_token)

    if SAVE_FORECAST_DATA:
        print("\n   Saving raw forecast data...")
        save_forecast_data(carbon_forecasts)

    # Get schedule from Gemini
    print("\n3. Generating optimal execution schedule with Gemini...")
    schedule = get_gemini_schedule_local(function_metadata, carbon_forecasts)

    # Save schedule
    print("\n4. Saving schedule...")
    save_schedule(schedule)

    # Print summary
    print_schedule_summary(schedule)

    print("\n" + "=" * 60)
    print("Scheduling complete!")
    print("=" * 60)

    return schedule
