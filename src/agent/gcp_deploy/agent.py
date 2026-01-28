#!/usr/bin/env python3
"""
Carbon-Aware Serverless Function Scheduler - Unified Agent

Works both locally and in GCP Cloud Run deployment.
- Local mode: Reads from local_bucket/ directory
- Cloud mode: Reads from GCS bucket

Mode detection:
- If run as main script (__name__ == "__main__"): Local mode
- If run as Flask app in Cloud Run: Cloud mode
"""

import json
import os
import uuid
import asyncio
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import requests
import google.generativeai as genai

# Determine if we're running locally
IS_LOCAL_MODE = False # DO NOT CHANGE WHEN DEPLOY
# LOCAL_BUCKET_PATH will be set when entering local mode (in __main__ block)
LOCAL_BUCKET_PATH = None

# Configuration - will be set either from environment or when entering local mode
BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME", "faas-scheduling-us-east1")
ELECTRICITYMAPS_TOKEN = os.environ.get("ELECTRICITYMAPS_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# MCP Server configuration for function deployment
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8080")
MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

# Forecast caching configuration
MAX_FORECAST_AGE_DAYS = 7  # Regenerate schedule if older than this many days

# ElectricityMaps API mode configuration
# Set to True only if you have premium API access with forecast endpoint
# When False, uses history endpoint data shifted +24h as mock forecast
USE_ACTUAL_FORECASTS = False

# GCS paths for configuration files
STATIC_CONFIG_PATH = "static_config.json"
FUNCTION_METADATA_PATH = "function_metadata.json"

# Metadata defaults - single source of truth
METADATA_DEFAULTS = {
    "runtime_ms": 1000,
    "memory_mb": 512,
    "data_input_gb": 0.0,
    "data_output_gb": 0.0,
    "source_location": "us-east1",
    "invocations_per_day": 1,
    "priority": "balanced",
    "latency_important": False,
    "gpu_required": False,
    "vcpus": None,  # Dynamically determined based on gpu_required
    "allowed_regions": [],
    "allow_schedule_caching": True  # Allow reusing schedules if inputs unchanged and not too old
}

# Cache for static config
_static_config_cache = None


def read_from_storage(blob_name: str) -> dict:
    """
    Read JSON data from storage.
    Uses local_bucket/ in local mode, GCS in cloud mode.
    """
    if IS_LOCAL_MODE:
        filepath = LOCAL_BUCKET_PATH / blob_name
        with open(filepath, 'r') as f:
            return json.load(f)
    else:
        from google.cloud import storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)
        content = blob.download_as_string()
        return json.loads(content)


def write_to_storage(data: dict, blob_name: str) -> str:
    """
    Write JSON data to storage.
    Uses local_bucket/ in local mode, GCS in cloud mode.
    """
    if IS_LOCAL_MODE:
        filepath = LOCAL_BUCKET_PATH / blob_name
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"Written to {filepath}")
        return str(filepath)
    else:
        from google.cloud import storage
        storage_client = storage.Client()
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(data, indent=2),
            content_type="application/json"
        )
        location = f"gs://{BUCKET_NAME}/{blob_name}"
        print(f"Written to {location}")
        return location


def load_static_config() -> dict:
    """Load static configuration from storage."""
    global _static_config_cache
    if _static_config_cache is None:
        source = str(LOCAL_BUCKET_PATH / STATIC_CONFIG_PATH) if IS_LOCAL_MODE else f"gs://{BUCKET_NAME}/{STATIC_CONFIG_PATH}"
        print(f"Loading static_config.json from {source}")
        _static_config_cache = read_from_storage(STATIC_CONFIG_PATH)
    return _static_config_cache


def load_function_metadata() -> dict:
    """Load function metadata from storage."""
    source = str(LOCAL_BUCKET_PATH / FUNCTION_METADATA_PATH) if IS_LOCAL_MODE else f"gs://{BUCKET_NAME}/{FUNCTION_METADATA_PATH}"
    print(f"Loading function_metadata.json from {source}")
    return read_from_storage(FUNCTION_METADATA_PATH)


def apply_defaults(metadata: dict) -> dict:
    """
    Apply default values to function metadata.

    User-provided values override defaults. This ensures all required fields
    exist in the metadata dict, allowing simple direct access without .get().

    Args:
        metadata: User-provided function metadata

    Returns:
        Metadata dict with defaults applied
    """
    return {**METADATA_DEFAULTS, **metadata}


def compute_metadata_hash(metadata: dict) -> str:
    """
    Compute a hash of metadata inputs to detect changes.

    Only includes fields that affect scheduling decisions, excludes allow_schedule_caching.

    Args:
        metadata: Function metadata dict

    Returns:
        SHA256 hash of relevant metadata fields
    """
    # Select only fields that affect scheduling
    relevant_fields = {
        "runtime_ms": metadata.get("runtime_ms"),
        "memory_mb": metadata.get("memory_mb"),
        "data_input_gb": metadata.get("data_input_gb"),
        "data_output_gb": metadata.get("data_output_gb"),
        "source_location": metadata.get("source_location"),
        "invocations_per_day": metadata.get("invocations_per_day"),
        "priority": metadata.get("priority"),
        "latency_important": metadata.get("latency_important"),
        "gpu_required": metadata.get("gpu_required"),
        "vcpus": metadata.get("vcpus"),
        "allowed_regions": tuple(sorted(metadata.get("allowed_regions", [])))  # Sort for consistency
    }

    # Create a consistent JSON string
    metadata_str = json.dumps(relevant_fields, sort_keys=True)

    # Compute SHA256 hash
    return hashlib.sha256(metadata_str.encode()).hexdigest()


def compute_code_hash(code: str) -> str:
    """
    Compute a hash of function code to detect changes.

    Args:
        code: Function source code string

    Returns:
        SHA256 hash of the code
    """
    # Normalize whitespace for consistent hashing
    normalized_code = code.strip()
    return hashlib.sha256(normalized_code.encode()).hexdigest()


def load_deployment_state() -> dict:
    """
    Load deployment state from storage.

    Returns:
        Dict mapping function_id to deployment info:
        {
            "function_id": {
                "code_hash": "sha256...",
                "deployed_region": "us-east1",
                "function_url": "https://...",
                "deployed_at": "ISO timestamp"
            }
        }
    """
    try:
        return read_from_storage("deployment_state.json")
    except (FileNotFoundError, Exception):
        return {}


def save_deployment_state(state: dict) -> str:
    """
    Save deployment state to storage.

    Args:
        state: Deployment state dict

    Returns:
        Path where state was saved
    """
    return write_to_storage(state, "deployment_state.json")



def inject_function_url_into_recommendations(schedule: dict, function_url: str) -> None:
    """
    Inject function_url into each recommendation in the schedule.

    The dispatcher expects each recommendation slot to have a function_url field.
    This function adds it to all recommendations after deployment.

    Args:
        schedule: Schedule dict with recommendations list
        function_url: The deployed function's URL
    """
    recommendations = schedule.get("recommendations", [])
    for rec in recommendations:
        rec["function_url"] = function_url


def deploy_functions_to_optimal_regions(
    schedules: dict,
    functions_metadata: dict
) -> dict:
    """
    Deploy functions to their optimal regions based on generated schedules.

    For each function:
    1. Get the best region from its schedule recommendations
    2. Check if function is already deployed with same code hash
    3. Deploy via MCP if needed (new function or code changed)

    Args:
        schedules: Dict mapping function_name to schedule (with recommendations)
        functions_metadata: Dict mapping function_name to metadata (with code)

    Returns:
        Dict with deployment results for each function
    """
    # Import MCP client
    try:
        from agent.mcp_client import MCPClientSync
    except ImportError:
        from mcp_client import MCPClientSync

    # Initialize MCP client
    mcp_client = MCPClientSync(MCP_SERVER_URL, MCP_API_KEY)

    # Load existing deployment state
    deployment_state = load_deployment_state()

    # Load static config for default values
    static_config = load_static_config()

    deployment_results = {}

    for func_name, schedule in schedules.items():
        print(f"\n  Processing deployment for: {func_name}")

        # Skip if schedule had an error
        if "error" in schedule:
            print(f"    Skipping: schedule generation failed")
            deployment_results[func_name] = {
                "deployed": False,
                "reason": "schedule_error",
                "error": schedule["error"]
            }
            continue

        # Get function metadata
        func_metadata = functions_metadata.get(func_name, {})
        code = func_metadata.get("code")

        if not code:
            print(f"    Skipping: no code provided in metadata")
            deployment_results[func_name] = {
                "deployed": False,
                "reason": "no_code"
            }
            continue

        # Compute code hash
        current_code_hash = compute_code_hash(code)

        # Get best region from schedule
        recommendations = schedule.get("recommendations", [])
        if not recommendations:
            print(f"    Skipping: no recommendations in schedule")
            deployment_results[func_name] = {
                "deployed": False,
                "reason": "no_recommendations"
            }
            continue

        sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))
        optimal_region = sorted_recs[0].get("region", "us-east1")

        print(f"    Optimal region: {optimal_region}")
        print(f"    Code hash: {current_code_hash[:12]}")

        # Check existing deployment state
        existing_deployment = deployment_state.get(func_name, {})
        existing_code_hash = existing_deployment.get("code_hash")
        existing_region = existing_deployment.get("deployed_region")

        # Determine if deployment is needed
        needs_deployment = False
        deployment_reason = None

        if not existing_code_hash:
            needs_deployment = True
            deployment_reason = "new_function"
            print(f"    Status: New function, will deploy")
        elif existing_code_hash != current_code_hash:
            needs_deployment = True
            deployment_reason = "code_changed"
            print(f"    Status: Code changed (was {existing_code_hash[:12]}...), will redeploy")
        elif existing_region != optimal_region:
            needs_deployment = True
            deployment_reason = "region_changed"
            print(f"    Status: Optimal region changed ({existing_region} -> {optimal_region}), will redeploy")
        else:
            # Verify function still exists via MCP
            print(f"    Verifying function exists in {existing_region}")
            try:
                status_result = mcp_client.get_function_status(
                    function_name=func_name,
                    region=existing_region
                )
                if status_result.get("exists") and status_result.get("status") == "ACTIVE":
                    print(f"    Function already deployed and active, skipping")
                    function_url = existing_deployment.get("function_url")

                    # Ensure schedule has deployment info and function_url in recommendations
                    if "deployment" not in schedule or schedule["deployment"].get("function_url") != function_url:
                        schedule["deployment"] = {
                            "function_url": function_url,
                            "region": existing_region,
                            "deployed_at": existing_deployment.get("deployed_at")
                        }
                        # Inject function_url into each recommendation for dispatcher compatibility
                        inject_function_url_into_recommendations(schedule, function_url)
                        schedule_filename = f"schedule_{func_name}.json"
                        write_to_storage(schedule, schedule_filename)
                        print(f"    Schedule updated with deployment info")

                    deployment_results[func_name] = {
                        "deployed": False,
                        "reason": "already_deployed",
                        "function_url": function_url,
                        "region": existing_region
                    }
                    continue
                else:
                    needs_deployment = True
                    deployment_reason = "not_active"
                    print(f"    Status: Function not active (status: {status_result.get('status')}), will redeploy")
            except Exception as e:
                needs_deployment = True
                deployment_reason = "status_check_failed"
                print(f"    Status: Could not verify function ({e}), will deploy")

        if needs_deployment:
            print(f"    Deploying to {optimal_region}")
            try:
                # Get optional fields from metadata
                memory_mb = func_metadata.get("memory_mb", 256)
                timeout_seconds = func_metadata.get("timeout_seconds", 60)
                requirements = func_metadata.get("requirements", "")

                # Calculate vCPUs: use specified value or defaults based on gpu_required
                vcpus = func_metadata.get("vcpus")
                gpu_required = func_metadata.get("gpu_required", False)
                if vcpus is None:
                    if gpu_required:
                        vcpus = static_config.get("agent_defaults", {}).get("vcpus_if_gpu", 8)
                    else:
                        vcpus = static_config.get("agent_defaults", {}).get("vcpus_default", 1)

                deployment_result = mcp_client.deploy_function(
                    function_name=func_name,
                    code=code,
                    region=optimal_region,
                    runtime="python312",
                    memory_mb=memory_mb,
                    cpu=str(vcpus),
                    timeout_seconds=timeout_seconds,
                    entry_point="main",
                    requirements=requirements
                )

                if deployment_result.get("success"):
                    function_url = deployment_result.get("function_url")
                    print(f"    Deployed successfully: {function_url}")

                    # Update deployment state
                    deployment_state[func_name] = {
                        "code_hash": current_code_hash,
                        "deployed_region": optimal_region,
                        "function_url": function_url,
                        "deployed_at": datetime.now().isoformat()
                    }

                    # Update schedule with deployment info and re-save
                    schedule["deployment"] = {
                        "function_url": function_url,
                        "region": optimal_region,
                        "deployed_at": datetime.now().isoformat()
                    }
                    # Inject function_url into each recommendation for dispatcher compatibility
                    inject_function_url_into_recommendations(schedule, function_url)
                    schedule_filename = f"schedule_{func_name}.json"
                    write_to_storage(schedule, schedule_filename)
                    print(f"    Schedule updated with deployment info")

                    deployment_results[func_name] = {
                        "deployed": True,
                        "reason": deployment_reason,
                        "function_url": function_url,
                        "region": optimal_region
                    }
                else:
                    error_msg = deployment_result.get("error", "Unknown error")
                    print(f"    Deployment failed: {error_msg}")
                    deployment_results[func_name] = {
                        "deployed": False,
                        "reason": "deployment_failed",
                        "error": error_msg
                    }

            except Exception as e:
                print(f"    Deployment error: {e}")
                deployment_results[func_name] = {
                    "deployed": False,
                    "reason": "deployment_error",
                    "error": str(e)
                }

    # Save updated deployment state
    save_deployment_state(deployment_state)
    print(f"\n  Deployment state saved")

    return deployment_results


def get_region_info(region_code: str, config: dict) -> dict:
    """
    Get region information from config.

    Args:
        region_code: GCP region code (e.g., 'us-east1', 'europe-west1')
        config: Static config dict

    Returns:
        Dictionary with region info including name, pricing tier, transfer costs, etc.
    """
    regions = config.get("regions", {})
    return regions.get(region_code, {})


def calculate_transfer_cost(
    region_code: str,
    data_input_gb: float,
    data_output_gb: float,
    source_location: str,
    config: dict
) -> float:
    """
    Calculate data transfer cost for a region.

    Args:
        region_code: Target execution region
        data_input_gb: Amount of input data in GB
        data_output_gb: Amount of output data in GB
        source_location: Source data location (if same as target, cost is 0)
        config: Static config dict

    Returns:
        Total transfer cost in USD
    """
    # If executing in same region as data source, no transfer cost
    if source_location and region_code == source_location:
        return 0.0

    region_info = get_region_info(region_code, config)
    cost_per_gb = region_info.get("data_transfer_cost_per_gb_usd", 0.0)

    total_data_gb = data_input_gb + data_output_gb
    return total_data_gb * cost_per_gb


def calculate_emissions_per_execution(
    runtime_ms: float,
    memory_mb: float,
    data_input_gb: float,
    data_output_gb: float,
    carbon_intensity: float,
    config: dict,
    vcpus: int = 1,
    gpu_count: int = 0,
    gpu_type: str = "nvidia-l4"
) -> float:
    """
    Calculate CO2 emissions for a single function execution using static_config formulas.

    Args:
        runtime_ms: Function runtime in milliseconds
        memory_mb: Function memory allocation in MB
        data_input_gb: Input data in GB
        data_output_gb: Output data in GB
        carbon_intensity: Carbon intensity in gCO2/kWh
        config: Static config dict with power_constants
        vcpus: Number of vCPUs (default: 1)
        gpu_count: Number of GPUs (default: 0)
        gpu_type: GPU type if used (default: "nvidia-l4")

    Returns:
        CO2 emissions in grams for one execution

    Formula from static_config.json:
    - cpu_power_w = vcpus × cpu_watts_per_vcpu × cpu_utilization_factor
    - memory_power_w = memory_gib × memory_watts_per_gib
    - gpu_power_w = gpu_count × gpu_tdp_watts × gpu_utilization_factor
    - compute_energy_kwh = (cpu_power + memory_power + gpu_power) × (runtime_s / 3600) × PUE
    - transfer_energy_kwh = (data_input + data_output) × network_kwh_per_gb
    - total_energy_kwh = compute_energy + transfer_energy
    - emissions = total_energy_kwh × carbon_intensity
    """
    power_constants = config.get("power_constants", {})

    # Convert runtime to seconds
    runtime_s = runtime_ms / 1000

    # Convert memory to GiB
    memory_gib = memory_mb / 1024

    # Calculate CPU power (Watts)
    cpu_watts_per_vcpu = power_constants.get("cpu_watts_per_vcpu", 2.5)
    cpu_utilization_factor = power_constants.get("cpu_utilization_factor", 0.5)
    cpu_power_w = vcpus * cpu_watts_per_vcpu * cpu_utilization_factor

    # Calculate memory power (Watts)
    memory_watts_per_gib = power_constants.get("memory_watts_per_gib", 0.4)
    memory_power_w = memory_gib * memory_watts_per_gib

    # Calculate GPU power (Watts) if applicable
    gpu_power_w = 0
    if gpu_count > 0:
        gpu_tdp_watts = power_constants.get("gpu_tdp_watts", {}).get(gpu_type, 72)
        gpu_utilization_factor = power_constants.get("gpu_utilization_factor", 0.8)
        gpu_power_w = gpu_count * gpu_tdp_watts * gpu_utilization_factor

    # Calculate compute energy (kWh)
    total_power_w = cpu_power_w + memory_power_w + gpu_power_w
    datacenter_pue = power_constants.get("datacenter_pue", 1.1)
    compute_energy_kwh = (total_power_w * (runtime_s / 3600)) * datacenter_pue

    # Calculate transfer energy (kWh)
    network_kwh_per_gb = power_constants.get("network_kwh_per_gb", 0.002)
    total_data_gb = data_input_gb + data_output_gb
    transfer_energy_kwh = total_data_gb * network_kwh_per_gb

    # Total energy
    total_energy_kwh = compute_energy_kwh + transfer_energy_kwh

    # Calculate emissions (grams CO2)
    emissions_grams = total_energy_kwh * carbon_intensity

    return emissions_grams


def get_carbon_history_electricitymaps(zone: str) -> list:
    """Fetch past 24 hours of carbon intensity data from Electricity Maps API."""
    if not ELECTRICITYMAPS_TOKEN:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    history_url = "https://api.electricitymaps.com/v3/carbon-intensity/history"
    headers = {"auth-token": ELECTRICITYMAPS_TOKEN}
    params = {"zone": zone}

    response = requests.get(history_url, headers=headers, params=params)

    if response.status_code == 200:
        data = response.json()
        return data.get("history", [])
    else:
        raise Exception(
            f"Electricity Maps History API failed for zone {zone}: "
            f"{response.status_code} - {response.text}"
        )


def transform_history_to_mock_forecast(history: list, shift_hours: int = 24) -> list:
    """
    Transform historical data into mock forecast by shifting timestamps.

    History response format (extra fields ignored):
        {"zone": "BE", "carbonIntensity": 264, "datetime": "2026-01-27T17:00:00.000Z", ...}

    Output format (matches forecast endpoint):
        {"carbonIntensity": 264, "datetime": "2026-01-28T17:00:00.000Z"}
    """
    mock_forecast = []
    shift_delta = timedelta(hours=shift_hours)

    for point in history:
        original_dt_str = point["datetime"]
        if original_dt_str.endswith("Z"):
            original_dt = datetime.fromisoformat(original_dt_str.replace("Z", "+00:00"))
        else:
            original_dt = datetime.fromisoformat(original_dt_str)

        shifted_dt = original_dt + shift_delta
        shifted_dt_str = shifted_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        # Only include the two fields that the forecast endpoint returns
        mock_forecast.append({
            "carbonIntensity": point["carbonIntensity"],
            "datetime": shifted_dt_str
        })

    return mock_forecast


def get_carbon_forecast_electricitymaps(zone: str, horizon_hours: int = 24) -> list:
    """
    Fetch carbon intensity forecast from Electricity Maps API.

    When USE_ACTUAL_FORECASTS is False (default), fetches historical data
    and transforms it into a mock forecast by shifting timestamps +24 hours.
    """
    if not ELECTRICITYMAPS_TOKEN:
        raise Exception("ELECTRICITYMAPS_TOKEN environment variable not set")

    if USE_ACTUAL_FORECASTS:
        # Use actual forecast endpoint (requires premium API access)
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
    else:
        # Mock forecast mode: use history data shifted +24 hours
        history = get_carbon_history_electricitymaps(zone)
        return transform_history_to_mock_forecast(history, shift_hours=24)


def get_carbon_forecasts_all_regions(allowed_regions: Optional[list] = None) -> tuple:
    """
    Fetch carbon forecasts for configured regions from Electricity Maps.

    Args:
        allowed_regions: Optional list of region codes to fetch. If None, fetches all European regions.

    Returns:
        Tuple of (forecasts dict, failed_regions list)
    """
    # Log forecast mode
    if USE_ACTUAL_FORECASTS:
        print("Fetching actual carbon intensity forecasts from Electricity Maps")
    else:
        print("Using mock forecasts (historical data shifted +24h) - USE_ACTUAL_FORECASTS=False")

    static_config = load_static_config()

    # Determine which regions to fetch
    regions = {}

    if allowed_regions:
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


def format_forecast_for_llm(forecasts: dict) -> str:
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


def _generate_with_gemini(prompt: str, log_message: Optional[str] = None) -> dict:
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
4. description: Clean technical summary of the function (one sentence)
5. data_input_gb: Estimate input data size per invocation (in GB)
6. data_output_gb: Estimate output data size per invocation (in GB)
7. source_location: Extract if mentioned (e.g., us-east1, europe-west1), default to "us-east1"
8. invocations_per_day: Extract frequency or estimate based on use case
9. priority: Optimization priority - "balanced" (default), "costs" (minimize costs), or "emissions" (minimize emissions)
   Extract if mentioned (keywords: cost-sensitive → "costs", green/sustainable → "emissions"), otherwise default to "balanced"
10. latency_important: true if low latency/real-time response is critical, false otherwise (default: false)
   Extract if mentioned (keywords: latency-sensitive, real-time, interactive → true), otherwise default to false
11. gpu_required: true if GPU acceleration is needed, false otherwise (default: false)
   Extract if mentioned (keywords: GPU, machine learning, AI inference, training → true), otherwise default to false
12. vcpus: Number of vCPUs to allocate (optional, defaults: 1 for non-GPU, 8 for GPU workloads)
   Only specify if different from defaults. Must be integer between 1-8.
13. allowed_regions: Extract if mentioned, otherwise leave empty array []

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
  "description": "string",
  "data_input_gb": number,
  "data_output_gb": number,
  "source_location": "string",
  "invocations_per_day": number,
  "priority": "balanced|costs|emissions",
  "latency_important": boolean,
  "gpu_required": boolean,
  "vcpus": number (optional, defaults: 1 for non-GPU, 8 for GPU),
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
  "description": "Resize user-uploaded images to multiple thumbnail sizes",
  "data_input_gb": 0.008,
  "data_output_gb": 0.012,
  "source_location": "us-east1",
  "invocations_per_day": 500,
  "priority": "balanced",
  "latency_important": false,
  "gpu_required": false,
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

    print(f"Parsing natural language request with Gemini")
    return _generate_with_gemini(prompt, log_message="Extracting function metadata from natural language")


def calculate_region_metrics(
    carbon_forecasts: dict,
    runtime_ms: float,
    memory_mb: float,
    data_input_gb: float,
    data_output_gb: float,
    invocations_per_day: int,
    source_location: str,
    static_config: dict,
    gpu_required: bool = False,
    vcpus: int = None
) -> dict:
    """
    Calculate yearly costs and emissions for each region.

    Args:
        carbon_forecasts: Dictionary of region forecasts with carbon intensity data
        runtime_ms: Function runtime in milliseconds
        memory_mb: Function memory in MB
        data_input_gb: Input data per invocation in GB
        data_output_gb: Output data per invocation in GB
        invocations_per_day: Number of invocations per day
        source_location: Source data location
        static_config: Static config dict

    Returns:
        Dict mapping region_code to metrics:
        {
            "region_code": {
                "transfer_cost_per_execution": float,
                "transfer_cost_yearly": float,
                "emissions_per_execution": float (gCO2),
                "emissions_yearly": float (kgCO2),
                "avg_carbon_intensity": float (gCO2/kWh)
            }
        }
    """
    region_metrics = {}

    for region_code, forecast_data in carbon_forecasts.items():
        # Calculate average carbon intensity for this region
        forecasts = forecast_data.get("forecast", [])
        if forecasts:
            avg_carbon_intensity = sum(f["carbonIntensity"] for f in forecasts) / len(forecasts)
        else:
            avg_carbon_intensity = 0

        # Calculate transfer cost per execution
        transfer_cost_per_exec = calculate_transfer_cost(
            region_code,
            data_input_gb,
            data_output_gb,
            source_location,
            static_config
        )

        # Calculate emissions per execution (in grams CO2)
        # Determine vCPU count: use specified value or defaults from agent_defaults
        if vcpus is None:
            if gpu_required:
                vcpus_to_use = static_config.get("agent_defaults", {}).get("vcpus_if_gpu", 8)
            else:
                vcpus_to_use = static_config.get("agent_defaults", {}).get("vcpus_default", 1)
        else:
            vcpus_to_use = vcpus

        # GPU count
        if gpu_required:
            gpu_count = static_config.get("agent_defaults", {}).get("gpu_count", 1)
        else:
            gpu_count = 0

        emissions_per_exec = calculate_emissions_per_execution(
            runtime_ms,
            memory_mb,
            data_input_gb,
            data_output_gb,
            avg_carbon_intensity,
            static_config,
            vcpus=vcpus_to_use,
            gpu_count=gpu_count
        )

        # Calculate yearly totals
        yearly_invocations = invocations_per_day * 365
        transfer_cost_yearly = transfer_cost_per_exec * yearly_invocations
        emissions_yearly_kg = (emissions_per_exec * yearly_invocations) / 1000  # Convert g to kg

        region_metrics[region_code] = {
            "transfer_cost_per_execution": transfer_cost_per_exec,
            "transfer_cost_yearly": transfer_cost_yearly,
            "emissions_per_execution": emissions_per_exec,
            "emissions_yearly": emissions_yearly_kg,
            "avg_carbon_intensity": avg_carbon_intensity
        }

    return region_metrics


def format_region_metrics_for_llm(
    region_metrics: dict,
    data_input_gb: float,
    data_output_gb: float,
    invocations_per_day: int,
    source_location: str,
    static_config: dict
) -> str:
    """
    Format region costs and emissions for LLM prompt.

    Args:
        region_metrics: Pre-calculated metrics from calculate_region_metrics()
        data_input_gb: Input data per invocation
        data_output_gb: Output data per invocation
        invocations_per_day: Daily invocations
        source_location: Source data location
        static_config: Static config

    Returns:
        Formatted string with cost and emissions information
    """
    total_data_gb = data_input_gb + data_output_gb

    info = f"\nFunction Execution Profile:\n"
    info += f"- Data transfer per execution: {total_data_gb:.2f} GB ({data_input_gb:.2f} GB input + {data_output_gb:.2f} GB output)\n"
    info += f"- Invocations per day: {invocations_per_day}\n"
    info += f"- Data source location: {source_location or 'not specified'}\n"
    if source_location:
        info += f"- Note: Executing in {source_location} has ZERO transfer cost\n"

    info += f"\n{'='*80}\n"
    info += f"REGION COMPARISON - Yearly Costs and Emissions ({invocations_per_day * 365:,} executions/year)\n"
    info += f"{'='*80}\n\n"

    # Sort regions by total yearly cost (transfer + emissions)
    sorted_regions = sorted(
        region_metrics.items(),
        key=lambda x: x[1]["transfer_cost_yearly"]
    )

    for region_code, metrics in sorted_regions:
        region_info = get_region_info(region_code, static_config)
        region_name = region_info.get("name", region_code)

        info += f"{region_code} ({region_name}):\n"
        info += f"  Transfer Cost: ${metrics['transfer_cost_per_execution']:.4f}/exec → ${metrics['transfer_cost_yearly']:,.0f}/year\n"
        info += f"  CO2 Emissions: {metrics['emissions_per_execution']:.2f}g/exec → {metrics['emissions_yearly']:.1f}kg/year\n"
        info += f"  Avg Carbon Intensity: {metrics['avg_carbon_intensity']:.0f} gCO2/kWh\n"
        info += "\n"

    return info


def get_gemini_schedule(function_metadata: dict, carbon_forecasts: dict) -> dict:
    """Use Google Gemini to create optimal execution schedule."""
    # Import using absolute or relative depending on context
    try:
        from agent.prompts import create_prompt
    except ImportError:
        from prompts import create_prompt

    carbon_forecasts_formatted = format_forecast_for_llm(carbon_forecasts)

    # Load static config
    static_config = load_static_config()

    # Extract region metrics (costs and emissions)
    # Defaults already applied via apply_defaults(), so direct access is safe
    runtime_ms = function_metadata["runtime_ms"]
    memory_mb = function_metadata["memory_mb"]
    data_input_gb = function_metadata["data_input_gb"]
    data_output_gb = function_metadata["data_output_gb"]
    invocations_per_day = function_metadata["invocations_per_day"]
    source_location = function_metadata["source_location"]
    priority = function_metadata["priority"]
    latency_important = function_metadata["latency_important"]
    gpu_required = function_metadata["gpu_required"]
    vcpus = function_metadata["vcpus"]  # None if not specified, dynamically determined later

    # Build latency context if applicable
    latency_context = ""
    if latency_important:
        source_region_info = get_region_info(source_location, static_config)
        source_continent = source_region_info.get("continent", "north-america")
        latency_context = f"\nLATENCY REQUIREMENT: This function is latency-sensitive. Only {source_continent} regions are included to minimize cross-continent latency. All scheduling decisions must consider low-latency requirement.\n"

    region_metrics = calculate_region_metrics(
        carbon_forecasts,
        runtime_ms,
        memory_mb,
        data_input_gb,
        data_output_gb,
        invocations_per_day,
        source_location,
        static_config,
        gpu_required=gpu_required,
        vcpus=vcpus
    )

    # Format metrics for LLM
    metrics_info = format_region_metrics_for_llm(
        region_metrics,
        data_input_gb,
        data_output_gb,
        invocations_per_day,
        source_location,
        static_config
    )

    prompt = create_prompt(
        function_metadata,
        carbon_forecasts_formatted,
        metrics_info + latency_context,
        region_metrics,
        priority
    )

    return _generate_with_gemini(prompt, log_message="Sending request to Gemini API")


def is_cached_schedule_valid(function_name: str, function_metadata: dict) -> tuple:
    """
    Check if a cached schedule exists and is still valid.

    A cached schedule is valid if:
    1. allow_schedule_caching is True in metadata
    2. Schedule file exists
    3. Metadata hash matches current metadata
    4. Schedule is not older than MAX_FORECAST_AGE_DAYS

    Returns:
        tuple: (is_valid: bool, cached_schedule: dict or None, schedule_path: str or None)
    """
    # Check if caching is allowed
    if not function_metadata["allow_schedule_caching"]:
        return False, None, None

    # Try to load existing schedule
    schedule_filename = f"schedule_{function_name}.json"
    try:
        cached_schedule = read_from_storage(schedule_filename)
    except FileNotFoundError:
        # No cached schedule exists
        return False, None, None
    except Exception:
        # Error reading cached schedule, treat as invalid
        return False, None, None

    # Check if metadata has changed
    current_hash = compute_metadata_hash(function_metadata)
    cached_hash = cached_schedule.get("metadata", {}).get("metadata_hash")

    if cached_hash != current_hash:
        # Metadata changed, cache invalid
        return False, None, None

    # Check if schedule is too old
    created_at_str = cached_schedule.get("metadata", {}).get("created_at")
    if not created_at_str:
        # No creation timestamp, cache invalid
        return False, None, None

    try:
        created_at = datetime.fromisoformat(created_at_str)
        age_days = (datetime.now() - created_at).days

        if age_days > MAX_FORECAST_AGE_DAYS:
            # Schedule too old
            return False, None, None
    except Exception:
        # Error parsing timestamp, cache invalid
        return False, None, None

    # Cache is valid!
    if IS_LOCAL_MODE:
        schedule_path = str(LOCAL_BUCKET_PATH / schedule_filename)
    else:
        schedule_path = f"gs://{BUCKET_NAME}/{schedule_filename}"

    return True, cached_schedule, schedule_path


def run_scheduler_for_function(function_name: str, function_metadata: dict, carbon_forecasts: dict, metadata_hash: str = None) -> tuple:
    """Generate schedule for a single function.

    Args:
        function_name: Name of the function
        function_metadata: Function metadata (may have filtered regions)
        carbon_forecasts: Carbon forecast data for regions
        metadata_hash: Pre-computed hash based on ORIGINAL unfiltered metadata (optional, will compute if not provided)
    """
    print(f"\nGenerating schedule for function: {function_name}")
    print(f"  Runtime: {function_metadata.get('runtime_ms')}ms")
    print(f"  Memory: {function_metadata.get('memory_mb')}MB")

    # Generate schedule
    schedule = get_gemini_schedule(function_metadata, carbon_forecasts)

    # Add metadata
    schedule["metadata"] = {
        "generated_at": datetime.now().isoformat(),
        "function_metadata": function_metadata,
        "regions_used": list(carbon_forecasts.keys()),
        "metadata_hash": metadata_hash if metadata_hash else compute_metadata_hash(function_metadata),
        "created_at": datetime.now().isoformat(),
    }

    # Save schedule to storage
    schedule_filename = f"schedule_{function_name}.json"
    schedule_path = write_to_storage(schedule, schedule_filename)

    return schedule, schedule_path


def run_scheduler() -> tuple:
    """
    Main scheduling logic.
    Works for both local and cloud deployments.
    """
    mode = "LOCAL" if IS_LOCAL_MODE else "CLOUD"
    print("=" * 60)
    print(f"Carbon-Aware Serverless Function Scheduler - {mode} Mode")
    print("=" * 60)

    # Step 1: Load function metadata from storage
    print("\n1. Loading function metadata from storage")
    try:
        function_metadata_file = load_function_metadata()
    except Exception as exc:
        print(f"Error loading function_metadata.json: {exc}")
        raise Exception(
            f"Could not load function_metadata.json. "
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
    print("\n1.5. Processing function metadata")
    functions_to_schedule = {}

    for func_name, func_data in functions_raw.items():
        if isinstance(func_data, str):
            # Natural language description - parse it
            print(f"  {func_name}: Detected natural language description, parsing with Gemini")
            try:
                parsed_metadata = parse_natural_language_request(func_data)
                # Override function_id with the key name from JSON
                parsed_metadata["function_id"] = func_name
                # Apply defaults
                functions_to_schedule[func_name] = apply_defaults(parsed_metadata)
                print(f"    Parsed successfully (confidence: {parsed_metadata.get('confidence_score', 0):.2f})")

                # Show extracted info
                if parsed_metadata.get("assumptions"):
                    print(f"    Assumptions: {', '.join(parsed_metadata['assumptions'][:2])}")
                if parsed_metadata.get("warnings"):
                    print(f"    Warnings: {', '.join(parsed_metadata['warnings'][:2])}")
            except Exception as exc:
                print(f"    Failed to parse natural language: {exc}")
                raise Exception(f"Could not parse natural language description for function '{func_name}': {exc}")
        elif isinstance(func_data, dict):
            # Structured metadata - use directly and apply defaults
            print(f"  {func_name}: Using structured metadata directly")
            functions_to_schedule[func_name] = apply_defaults(func_data)
        else:
            raise Exception(
                f"Invalid format for function '{func_name}': must be either a string (natural language) "
                f"or object (structured metadata), got {type(func_data).__name__}"
            )

    # Compute and store metadata hashes BEFORE any filtering (GPU, latency, etc.)
    # This ensures hash is based on original input metadata, not filtered regions
    metadata_hashes = {}
    for func_name, func_metadata in functions_to_schedule.items():
        metadata_hashes[func_name] = compute_metadata_hash(func_metadata)

    # Step 2: Check cache validity for each function BEFORE fetching forecasts
    print("\n2. Checking cached schedules")
    static_config = load_static_config()

    # Track which functions can use cache vs need new schedules
    cached_functions = {}  # func_name -> (schedule, path)
    functions_needing_schedule = {}  # func_name -> metadata

    for func_name, func_metadata in functions_to_schedule.items():
        is_valid, cached_schedule, cached_path = is_cached_schedule_valid(func_name, func_metadata)

        if is_valid:
            print(f"  {func_name}: Valid cache found (age: {(datetime.now() - datetime.fromisoformat(cached_schedule['metadata']['created_at'])).days} days)")
            cached_functions[func_name] = (cached_schedule, cached_path)
        else:
            print(f"  {func_name}: No valid cache, will generate new schedule")
            functions_needing_schedule[func_name] = func_metadata

    # If all functions have valid cache, skip forecast fetch entirely!
    if not functions_needing_schedule:
        print("\nAll functions have valid cached schedules - skipping carbon forecast fetch")
        print("\n3. Updating cached schedules with today's date")
        schedules = {}
        schedule_paths = {}

        for func_name, (cached_schedule, cached_path) in cached_functions.items():
            # Update dates in cached schedule
            now = datetime.now()
            created_at_str = cached_schedule["metadata"]["created_at"]
            created_at = datetime.fromisoformat(created_at_str)
            age_days = (now - created_at).days

            print(f"\n  {func_name}:")
            print(f"    Originally created: {created_at_str}")
            print(f"    Age: {age_days} day(s)")

            # Update recommendation dates to today
            if "recommendations" in cached_schedule:
                today = now.date()
                for rec in cached_schedule["recommendations"]:
                    if "datetime" in rec:
                        dt_str = rec["datetime"]
                        # Standard format: "YYYY-MM-DD HH:MM"
                        original_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                        new_dt = datetime.combine(today, original_dt.time())
                        rec["datetime"] = new_dt.strftime("%Y-%m-%d %H:%M")

                print(f"    Updated {len(cached_schedule['recommendations'])} recommendation dates to {today.isoformat()}")

            # Update metadata timestamps
            cached_schedule["metadata"]["generated_at"] = now.isoformat()

            # Save updated schedule
            schedule_filename = f"schedule_{func_name}.json"
            schedule_path = write_to_storage(cached_schedule, schedule_filename)

            schedules[func_name] = cached_schedule
            schedule_paths[func_name] = schedule_path

        print("\n" + "=" * 60)
        print("Scheduling complete!")
        print("=" * 60)

        # Step 4 (cached path): Deploy functions to optimal regions via MCP
        print("\n4. Deploying functions to optimal regions")
        deployment_results = deploy_functions_to_optimal_regions(schedules, functions_to_schedule)

        print("\n" + "=" * 60)
        print("Deployment complete!")
        print("=" * 60)

        return schedules, schedule_paths, None, deployment_results  # No forecast path since we didn't fetch

    # Step 3: Collect unique regions from functions that need new schedules
    print(f"\n3. Determining regions to fetch (for {len(functions_needing_schedule)} function(s))")
    all_allowed_regions = set()

    for func_name, func_metadata in functions_needing_schedule.items():
        # Defaults already applied, safe to access directly
        allowed_regions = func_metadata["allowed_regions"]
        latency_important = func_metadata["latency_important"]
        gpu_required = func_metadata["gpu_required"]
        source_location = func_metadata["source_location"]

        # Get source continent
        source_region_info = get_region_info(source_location, static_config)
        source_continent = source_region_info.get("continent", "north-america")

        # If latency_important, filter to same-continent regions only
        if latency_important:
            if allowed_regions:
                # Filter allowed_regions to same continent
                filtered_regions = [
                    r for r in allowed_regions
                    if get_region_info(r, static_config).get("continent") == source_continent
                ]
                func_metadata["allowed_regions"] = filtered_regions
                all_allowed_regions.update(filtered_regions)
                excluded_count = len(allowed_regions) - len(filtered_regions)
                print(f"  {func_name} -> latency-important, filtered to {source_continent}: {filtered_regions}")
                if excluded_count > 0:
                    print(f"    (excluded {excluded_count} cross-continent region(s))")
            else:
                # No allowed_regions specified - use all same-continent regions
                same_continent_regions = [
                    region_code for region_code, region_data in static_config["regions"].items()
                    if region_data.get("continent") == source_continent
                ]
                func_metadata["allowed_regions"] = same_continent_regions
                all_allowed_regions.update(same_continent_regions)
                print(f"  {func_name} -> latency-important, using all {source_continent} regions: {len(same_continent_regions)} regions")
        else:
            # No latency filtering
            if allowed_regions:
                all_allowed_regions.update(allowed_regions)
                print(f"  {func_name} -> regions: {allowed_regions}")
            else:
                print(f"  {func_name} -> no region filter (will use all available regions)")

        # Apply GPU filtering if gpu_required=True
        # NOTE: We only update the function's allowed_regions, NOT all_allowed_regions
        # This ensures we fetch forecasts for all regions needed by ANY function
        if gpu_required:
            current_regions = func_metadata["allowed_regions"]
            if current_regions:
                # Filter to GPU-available regions for THIS function only
                gpu_regions = [
                    r for r in current_regions
                    if get_region_info(r, static_config).get("gpu_available", False)
                ]
                excluded_count = len(current_regions) - len(gpu_regions)
                func_metadata["allowed_regions"] = gpu_regions
                # Do NOT remove from all_allowed_regions - other functions may need them
                print(f"  {func_name} -> GPU-required, filtered to GPU-capable regions: {gpu_regions}")
                if excluded_count > 0:
                    print(f"    (excluded {excluded_count} non-GPU region(s))")
            else:
                # No allowed_regions - use all GPU-capable regions
                gpu_regions = [
                    region_code for region_code, region_data in static_config["regions"].items()
                    if region_data.get("gpu_available", False)
                ]
                func_metadata["allowed_regions"] = gpu_regions
                all_allowed_regions.update(gpu_regions)
                print(f"  {func_name} -> GPU-required, using all GPU-capable regions: {len(gpu_regions)} regions")

    # Step 4: Fetch carbon forecasts for functions needing new schedules
    print(f"\n4. Fetching carbon intensity forecasts from Electricity Maps")
    if all_allowed_regions:
        carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions(list(all_allowed_regions))
    else:
        carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions()

    # Save raw forecast data to storage
    forecast_data = {
        "timestamp": datetime.now().isoformat(),
        "regions": carbon_forecasts,
        "failed_regions": failed_regions,
    }
    forecast_path = write_to_storage(forecast_data, "carbon_forecasts.json")

    # Step 5: Generate schedules for functions needing new schedules, update cached ones
    print(f"\n5. Processing schedules")
    schedules = {}
    schedule_paths = {}

    # First, add cached functions with updated dates
    print(f"\n  Updating {len(cached_functions)} cached schedule(s)")
    for func_name, (cached_schedule, _) in cached_functions.items():
        now = datetime.now()
        created_at_str = cached_schedule["metadata"]["created_at"]
        created_at = datetime.fromisoformat(created_at_str)
        age_days = (now - created_at).days

        print(f"\n  Using cached schedule for {func_name}")
        print(f"    Originally created: {created_at_str}")
        print(f"    Age: {age_days} day(s)")
        print(f"    Reason: Metadata unchanged and forecast still fresh (< {MAX_FORECAST_AGE_DAYS} days)")

        # Update recommendation dates to today
        if "recommendations" in cached_schedule:
            today = now.date()
            for rec in cached_schedule["recommendations"]:
                if "datetime" in rec:
                    dt_str = rec["datetime"]
                    # Standard format: "YYYY-MM-DD HH:MM"
                    original_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
                    new_dt = datetime.combine(today, original_dt.time())
                    rec["datetime"] = new_dt.strftime("%Y-%m-%d %H:%M")

            print(f"    Updated {len(cached_schedule['recommendations'])} recommendation dates to {today.isoformat()}")

        # Update metadata timestamps
        cached_schedule["metadata"]["generated_at"] = now.isoformat()

        # Save updated schedule
        schedule_filename = f"schedule_{func_name}.json"
        schedule_path = write_to_storage(cached_schedule, schedule_filename)

        schedules[func_name] = cached_schedule
        schedule_paths[func_name] = schedule_path

    # Then, generate new schedules
    print(f"\n  Generating {len(functions_needing_schedule)} new schedule(s) with Gemini")
    for function_name, function_metadata in functions_needing_schedule.items():
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
                function_name, function_metadata, filtered_forecasts, metadata_hashes[function_name]
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

    # Step 6: Deploy functions to optimal regions via MCP
    print("\n6. Deploying functions to optimal regions")
    deployment_results = deploy_functions_to_optimal_regions(schedules, functions_to_schedule)

    print("\n" + "=" * 60)
    print("Deployment complete!")
    print("=" * 60)

    return schedules, schedule_paths, forecast_path, deployment_results


def create_flask_app():
    """Create Flask app for Cloud Run deployment."""
    from flask import Flask, jsonify

    app = Flask(__name__)

    @app.route("/run", methods=["POST", "GET"])
    def run():
        """Endpoint to trigger the carbon-aware scheduler."""
        try:
            print("Running carbon-aware scheduler")
            schedules, schedule_paths, forecast_path, deployment_results = run_scheduler()

            # Prepare response with top recommendations and deployment info for each function
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

                    # Get deployment result for this function
                    deployment = deployment_results.get(function_name, {})

                    results[function_name] = {
                        "status": "success",
                        "schedule_location": schedule_paths[function_name],
                        "top_5_recommendations": top_5,
                        "total_recommendations": len(recommendations),
                        "deployment": deployment,
                    }

            return (
                jsonify(
                    {
                        "status": "success",
                        "message": "Carbon-aware schedules generated and functions deployed",
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
        mode = "LOCAL" if IS_LOCAL_MODE else "CLOUD"
        return (
            jsonify(
                {
                    "status": "healthy",
                    "service": "agent",
                    "mode": mode,
                    "bucket": BUCKET_NAME if not IS_LOCAL_MODE else str(LOCAL_BUCKET_PATH),
                    "has_emaps_token": bool(ELECTRICITYMAPS_TOKEN),
                    "has_gemini_key": bool(GEMINI_API_KEY),
                    "mcp_server_url": MCP_SERVER_URL,
                    "has_mcp_api_key": bool(MCP_API_KEY),
                }
            ),
            200,
        )

    @app.route("/submit", methods=["POST"])
    def submit_function():
        """
        Submit a function for carbon-aware deployment and execution.

        Request body:
        {
            "code": "def handler(data): return {'result': data['x'] * 2}",
            "deadline": "2025-01-03T18:00:00Z",
            "requirements": "numpy>=1.0.0",  // optional
            "description": "Multiply input by 2",  // optional, for metadata
            "memory_mb": 256,  // optional, default 256
            "timeout_seconds": 60,  // optional, default 60
            "priority": "balanced"  // optional: balanced, costs, emissions
        }

        Response:
        {
            "status": "success",
            "submission_id": "uuid",
            "function_name": "user-func-a1b2c3d4",
            "deployment": { ... },
            "schedule": { ... },
            "optimal_execution": { ... }
        }
        """
        from flask import request

        try:
            data = request.get_json()
            if not data:
                return jsonify({"status": "error", "message": "No JSON body provided"}), 400

            # Extract required fields
            code = data.get("code")
            deadline = data.get("deadline")

            if not code:
                return jsonify({"status": "error", "message": "Missing 'code' field"}), 400
            if not deadline:
                return jsonify({"status": "error", "message": "Missing 'deadline' field"}), 400

            # Extract optional fields
            requirements = data.get("requirements", "")
            description = data.get("description", "User-submitted function")
            memory_mb = data.get("memory_mb", 256)
            vcpus = data.get("vcpus")  # None means use default based on gpu_required
            gpu_required = data.get("gpu_required", False)
            timeout_seconds = data.get("timeout_seconds", 60)
            priority = data.get("priority", "balanced")

            # Generate submission ID and function name
            submission_id = str(uuid.uuid4())
            function_name = f"user-func-{submission_id[:8]}"

            print(f"\n{'='*60}")
            print(f"New function submission: {submission_id}")
            print(f"Function name: {function_name}")
            print(f"Deadline: {deadline}")
            print(f"Priority: {priority}")
            print(f"{'='*60}")

            # Step 1: Parse the code to estimate metadata (if description not provided)
            # For now, use provided metadata or defaults
            function_metadata = {
                "function_id": function_name,
                "description": description,
                "runtime_ms": 1000,  # Default estimate
                "memory_mb": memory_mb,
                "vcpus": vcpus,
                "gpu_required": gpu_required,
                "data_input_gb": 0.001,
                "data_output_gb": 0.001,
                "source_location": "us-east1",
                "invocations_per_day": 1,
                "priority": priority,
                "latency_important": False,
                "allowed_regions": [],  # Will use all available regions
            }

            # Step 2: Generate carbon-aware schedule
            print("\n2. Generating carbon-aware schedule")
            static_config = load_static_config()

            # Fetch carbon forecasts
            carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions()

            # Generate schedule
            schedule = get_gemini_schedule(function_metadata, carbon_forecasts)

            # Find optimal region from schedule
            recommendations = schedule.get("recommendations", [])
            if not recommendations:
                return jsonify({
                    "status": "error",
                    "message": "Failed to generate schedule recommendations"
                }), 500

            # Sort by priority and get best recommendation
            sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))
            optimal_rec = sorted_recs[0]
            optimal_region = optimal_rec.get("region", "us-east1")

            print(f"\n3. Optimal region selected: {optimal_region}")
            print(f"   Carbon intensity: {optimal_rec.get('carbon_intensity')} gCO2/kWh")

            # Step 3: Deploy function to optimal region via MCP
            print(f"\n4. Deploying function to {optimal_region} via MCP server")

            # Import MCP client
            try:
                from agent.mcp_client import MCPClientSync
            except ImportError:
                from mcp_client import MCPClientSync

            mcp_client = MCPClientSync(MCP_SERVER_URL, MCP_API_KEY)

            # Check MCP server health
            health_status = mcp_client.health_check()
            if health_status.get("status") != "healthy":
                print(f"Warning: MCP server health check: {health_status}")

            # Calculate vCPUs: use specified value or defaults based on gpu_required
            if vcpus is None:
                if gpu_required:
                    vcpus_to_use = static_config.get("agent_defaults", {}).get("vcpus_if_gpu", 8)
                else:
                    vcpus_to_use = static_config.get("agent_defaults", {}).get("vcpus_default", 1)
            else:
                vcpus_to_use = vcpus

            # Deploy the function
            deployment_result = mcp_client.deploy_function(
                function_name=function_name,
                code=code,
                region=optimal_region,
                runtime="python312",
                memory_mb=memory_mb,
                cpu=str(vcpus_to_use),
                timeout_seconds=timeout_seconds,
                entry_point="main",
                requirements=requirements
            )

            if not deployment_result.get("success"):
                return jsonify({
                    "status": "error",
                    "message": f"Deployment failed: {deployment_result.get('error', 'Unknown error')}",
                    "submission_id": submission_id,
                    "function_name": function_name
                }), 500

            function_url = deployment_result.get("function_url")
            print(f"   Deployed successfully: {function_url}")

            # Step 4: Save schedule in dispatcher-compatible format
            schedule_path = write_to_storage(schedule, f"schedule_{function_name}.json")
            print(f"   Schedule saved: {schedule_path}")

            # Step 5: Save submission info for tracking
            submission_info = {
                "submission_id": submission_id,
                "function_name": function_name,
                "deadline": deadline,
                "submitted_at": datetime.now().isoformat(),
                "optimal_region": optimal_region,
                "function_url": function_url,
                "schedule": schedule,
                "metadata": function_metadata
            }

            submission_path = write_to_storage(submission_info, f"submission_{submission_id}.json")

            # Prepare response
            response = {
                "status": "success",
                "submission_id": submission_id,
                "function_name": function_name,
                "deployment": {
                    "success": True,
                    "function_url": function_url,
                    "region": optimal_region,
                    "status": deployment_result.get("status", "ACTIVE")
                },
                "schedule": {
                    "total_recommendations": len(recommendations),
                    "top_5": sorted_recs[:5]
                },
                "optimal_execution": {
                    "datetime": optimal_rec.get("datetime"),
                    "region": optimal_region,
                    "carbon_intensity": optimal_rec.get("carbon_intensity"),
                    "reasoning": optimal_rec.get("reasoning")
                },
                "submission_location": submission_path
            }

            print(f"\n{'='*60}")
            print(f"Submission complete: {submission_id}")
            print(f"{'='*60}")

            return jsonify(response), 200

        except Exception as exc:
            print(f"Error in /submit: {exc}")
            import traceback
            traceback.print_exc()
            return jsonify({
                "status": "error",
                "message": str(exc)
            }), 500

    return app


if __name__ == "__main__":
    import sys

    # Add src directory to path for imports
    src_dir = Path(__file__).resolve().parents[1]
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    # Load environment variables for local execution
    try:
        from dotenv import load_dotenv
        # Load from project root
        env_path = Path(__file__).resolve().parents[2] / ".env"
        # Use override=True to force .env values to take precedence over system env vars
        load_dotenv(dotenv_path=env_path, override=True)
        print(f"Loaded environment variables from {env_path}")
    except ImportError:
        print("Warning: dotenv not available, using existing environment variables")

    # Local mode execution - set after loading env vars
    IS_LOCAL_MODE = True
    LOCAL_BUCKET_PATH = Path(__file__).resolve().parents[2] / "local_bucket"

    # Reload configuration with newly loaded environment variables
    # At module level, we can directly reassign module-level variables
    ELECTRICITYMAPS_TOKEN = os.environ.get("ELECTRICITYMAPS_TOKEN")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8080")
    MCP_API_KEY = os.environ.get("MCP_API_KEY", "")

    print("Running in LOCAL mode")
    print(f"Using local_bucket at: {LOCAL_BUCKET_PATH}")
    print(f"MCP Server URL: {MCP_SERVER_URL}")

    # Run the scheduler
    schedules, schedule_paths, forecast_path, deployment_results = run_scheduler()

    # Print summary for each function
    for function_name, schedule in schedules.items():
        if "error" in schedule:
            print(f"\n{function_name}: ERROR - {schedule['error']}")
            continue

        print(f"\n{'=' * 60}")
        print(f"Schedule Summary for {function_name}")
        print('=' * 60)

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

        # Print deployment info
        deployment = deployment_results.get(function_name, {})
        print(f"\nDeployment Status:")
        print("-" * 60)
        if deployment.get("deployed"):
            print(f"  Deployed: Yes (reason: {deployment.get('reason')})")
            print(f"  Region: {deployment.get('region')}")
            print(f"  URL: {deployment.get('function_url')}")
        else:
            reason = deployment.get("reason", "unknown")
            if reason == "already_deployed":
                print(f"  Deployed: Skipped (already deployed and active)")
                print(f"  Region: {deployment.get('region')}")
                print(f"  URL: {deployment.get('function_url')}")
            else:
                print(f"  Deployed: No (reason: {reason})")
                if deployment.get("error"):
                    print(f"  Error: {deployment.get('error')}")
