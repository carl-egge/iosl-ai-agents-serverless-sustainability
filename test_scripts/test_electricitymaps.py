#!/usr/bin/env python3
"""Fetch carbon intensity for GCP datacenters and zones from Electricity Maps API.
Modes:
python test_scripts/test_electricitymaps.py --forecast    # Forecast for REGIONS
python test_scripts/test_electricitymaps.py --gcp         # 24h history for GCP datacenters
python test_scripts/test_electricitymaps.py              # 24h history for REGIONS
"""

import requests
import json
import sys
import os
import argparse
from datetime import datetime

# Fix encoding for Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

API_TOKEN = "VpIbTuGVPZglfeI34rIT"

# GCP datacenter regions supported by Electricity Maps API (in EU & NA)
# Source: https://github.com/electricitymaps/electricitymaps-contrib/blob/master/config/data_centers/data_centers.json
GCP_DATACENTERS = {
    # Americas
    "us-central1": "Council Bluffs, Iowa (US-CENT-SWPP)",
    "us-east1": "Moncks Corner, South Carolina (US-CAR-SCEG)",
    "us-east4": "Ashburn, Virginia (US-MIDA-PJM)",
    "us-west1": "The Dalles, Oregon (US-NW-PACW)",
    "us-west2": "Los Angeles, California (US-SW-SRP)",
    "northamerica-northeast2": "Toronto, Ontario (US-MIDA-PJM)",
    # Europe
    "europe-west1": "St. Ghislain, Belgium (BE)",
    "europe-west3": "Frankfurt, Germany (DE)",
    "europe-west4": "Eemshaven, Netherlands (NL)",
    "europe-north1": "Hamina, Finland (FI)",
}

# Zone codes that map to supported GCP datacenters
ZONE_TO_GCP = {
    "BE": "europe-west1",
    "NL": "europe-west4",
    "DE": "europe-west3",
    "FI": "europe-north1",
    "US-CENT-SWPP": "us-central1",
    "US-CAR-SCEG": "us-east1",
    "US-NW-PACW": "us-west1",
    "US-MIDA-PJM": "us-east4",
    "US-SW-SRP": "us-west2",
}

# Regions to fetch (zone codes) - original list
REGIONS = [
    "CH",           # Switzerland
    "CA-QC",
    "FR"
]



def fetch_history(zone: str) -> dict:
    """Fetch last 24h carbon intensity history for a zone."""
    url = "https://api.electricitymaps.com/v3/carbon-intensity/history"
    headers = {"auth-token": API_TOKEN}
    params = {"zone": zone}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_datacenter_latest(provider: str, region: str) -> dict:
    """Fetch latest carbon intensity for a cloud provider datacenter."""
    url = "https://api.electricitymaps.com/v3/carbon-intensity/latest"
    headers = {"auth-token": API_TOKEN}
    params = {
        "dataCenterProvider": provider,
        "dataCenterRegion": region
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_datacenter_history(provider: str, region: str) -> dict:
    """Fetch last 24h carbon intensity history for a cloud provider datacenter."""
    url = "https://api.electricitymaps.com/v3/carbon-intensity/history"
    headers = {"auth-token": API_TOKEN}
    params = {
        "dataCenterProvider": provider,
        "dataCenterRegion": region
    }

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_all_gcp_datacenters():
    """Fetch 24h carbon intensity history for all GCP datacenters."""
    print("Fetching 24h carbon intensity for ALL GCP datacenters...")
    print("=" * 70)

    results = {}
    successful = []

    for region, location in GCP_DATACENTERS.items():
        print(f"\n{region} ({location})...")
        try:
            data = fetch_datacenter_history("gcp", region)
            history = data.get('history', [])
            results[region] = {
                "location": location,
                "data": data
            }
            if history:
                intensities = [h['carbonIntensity'] for h in history if h.get('carbonIntensity')]
                if intensities:
                    min_i, max_i, avg_i = min(intensities), max(intensities), sum(intensities) / len(intensities)
                    successful.append((region, location, min_i, max_i, avg_i))
                    print(f"  {len(history)} points | Range: {min_i:.0f} - {max_i:.0f} | Avg: {avg_i:.0f} gCO2eq/kWh")
                else:
                    print(f"  No intensity data")
            else:
                print(f"  No history data")
        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: {e.response.status_code} - {e.response.text[:100]}")
            results[region] = {"location": location, "error": str(e)}
        except Exception as e:
            print(f"  ERROR: {e}")
            results[region] = {"location": location, "error": str(e)}

    # Summary sorted by average intensity
    print("\n" + "=" * 70)
    print("SUMMARY - Sorted by Average Carbon Intensity (lowest first):")
    print("-" * 70)
    print(f"{'Region':28} {'Min':>6} {'Max':>6} {'Avg':>6}  gCO2eq/kWh")
    print("-" * 70)
    successful.sort(key=lambda x: x[4])  # Sort by average
    for region, location, min_i, max_i, avg_i in successful:
        bar = "#" * int(avg_i / 15)
        print(f"{region:28} {min_i:6.0f} {max_i:6.0f} {avg_i:6.0f}  {bar}")

    return results


def fetch_zones_history():
    """Fetch history for predefined zones (original behavior)."""
    print("Fetching carbon intensity history from Electricity Maps...")
    print("=" * 60)

    results = {}

    for zone in REGIONS:
        print(f"\nFetching {zone}...")
        try:
            data = fetch_history(zone)
            results[zone] = data
            history = data.get('history', [])
            print(f"  OK - {len(history)} data points")
            if history:
                intensities = [h['carbonIntensity'] for h in history if h.get('carbonIntensity')]
                if intensities:
                    print(f"  Range: {min(intensities):.0f} - {max(intensities):.0f} gCO2eq/kWh")
        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: {e.response.status_code} - {e.response.text[:100]}")
            results[zone] = {"error": str(e)}
        except Exception as e:
            print(f"  ERROR: {e}")
            results[zone] = {"error": str(e)}

    return results


def fetch_forecast(zone: str) -> dict:
    """Fetch carbon intensity forecast for a zone."""
    url = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
    headers = {"auth-token": API_TOKEN}
    params = {"zone": zone}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_zones_forecast():
    """Fetch forecast for predefined zones (requires paid API tier)."""
    print("Fetching carbon intensity FORECAST from Electricity Maps...")
    print("(Note: Forecast requires paid API tier)")
    print("=" * 70)

    results = {}
    successful = []

    for zone in REGIONS:
        print(f"\n{zone}...")
        try:
            data = fetch_forecast(zone)
            forecast = data.get('forecast', [])
            results[zone] = data
            if forecast:
                intensities = [f['carbonIntensity'] for f in forecast if f.get('carbonIntensity')]
                if intensities:
                    min_i, max_i, avg_i = min(intensities), max(intensities), sum(intensities) / len(intensities)
                    successful.append((zone, min_i, max_i, avg_i, len(forecast)))
                    first_dt = forecast[0].get('datetime', '')[:16]
                    last_dt = forecast[-1].get('datetime', '')[:16]
                    print(f"  {len(forecast)} points | Range: {min_i:.0f} - {max_i:.0f} | Avg: {avg_i:.0f} gCO2eq/kWh")
                    print(f"  Period: {first_dt} to {last_dt}")
                else:
                    print(f"  No intensity data in forecast")
            else:
                print(f"  No forecast data")
        except requests.exceptions.HTTPError as e:
            print(f"  ERROR: {e.response.status_code} - {e.response.text[:100]}")
            results[zone] = {"error": str(e)}
        except Exception as e:
            print(f"  ERROR: {e}")
            results[zone] = {"error": str(e)}

    if successful:
        print("\n" + "=" * 70)
        print("FORECAST SUMMARY - Sorted by Average (lowest first):")
        print("-" * 70)
        print(f"{'Zone':12} {'Min':>6} {'Max':>6} {'Avg':>6}  {'Points':>6}")
        print("-" * 70)
        successful.sort(key=lambda x: x[3])
        for zone, min_i, max_i, avg_i, points in successful:
            bar = "#" * int(avg_i / 15)
            print(f"{zone:12} {min_i:6.0f} {max_i:6.0f} {avg_i:6.0f}  {points:6}  {bar}")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Fetch carbon intensity data from Electricity Maps API"
    )
    parser.add_argument(
        "--gcp", "-g",
        action="store_true",
        help="Fetch 24h history for all GCP datacenters"
    )
    parser.add_argument(
        "--forecast", "-f",
        action="store_true",
        help="Fetch forecast for REGIONS (requires paid API tier)"
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Don't save results to JSON file"
    )
    args = parser.parse_args()

    if args.gcp:
        results = fetch_all_gcp_datacenters()
        filename_prefix = "gcp_intensities"
    elif args.forecast:
        results = fetch_zones_forecast()
        filename_prefix = "zones_forecast"
    else:
        results = fetch_zones_history()
        filename_prefix = "carbon_history"

    # Save to JSON
    if not args.no_save:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(script_dir, f"{filename_prefix}_{timestamp}.json")

        mode = "gcp_datacenters" if args.gcp else ("zones_forecast" if args.forecast else "zones_history")
        with open(output_file, 'w') as f:
            json.dump({
                "fetched_at": datetime.now().isoformat(),
                "mode": mode,
                "results": results
            }, f, indent=2)

        print("\n" + "=" * 70)
        print(f"Saved to: {output_file}")


if __name__ == "__main__":
    main()
