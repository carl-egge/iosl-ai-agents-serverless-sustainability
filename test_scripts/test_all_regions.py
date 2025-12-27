#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprehensive test of Electricity Maps zones - includes sub-regions for countries that need them."""

import requests
import sys
import json
from datetime import datetime
from pathlib import Path

# Fix encoding for Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_SAMPLE_DIR = PROJECT_ROOT / "data" / "sample"
OUTPUT_PATH = DATA_SAMPLE_DIR / "all_regions_forecast.json"

# Comprehensive list - country level first, then sub-regions for countries that don't work at country level
TEST_ZONES = [
    # Europe - Country level
    ("DE", "Germany"),
    ("FR", "France"),
    ("GB", "Great Britain"),
    ("IT", "Italy"),
    ("ES", "Spain"),
    ("NL", "Netherlands"),
    ("BE", "Belgium"),
    ("PL", "Poland"),
    ("AT", "Austria"),
    ("CH", "Switzerland"),
    ("DK", "Denmark"),
    ("SE", "Sweden"),
    ("NO", "Norway"),
    ("FI", "Finland"),
    ("PT", "Portugal"),
    ("GR", "Greece"),
    ("IE", "Ireland"),
    ("CZ", "Czech Republic"),
    ("RO", "Romania"),
    ("HU", "Hungary"),
    ("SK", "Slovakia"),
    ("BG", "Bulgaria"),
    ("HR", "Croatia"),
    ("SI", "Slovenia"),
    ("EE", "Estonia"),
    ("LV", "Latvia"),
    ("LT", "Lithuania"),
    ("IS", "Iceland"),
    ("RS", "Serbia"),
    ("BA", "Bosnia"),
    ("ME", "Montenegro"),
    ("MK", "North Macedonia"),
    ("AL", "Albania"),

    # Europe - Sub-regions (for countries that don't work at country level)
    ("IT-NO", "Italy North"),
    ("IT-CNO", "Italy Central North"),
    ("IT-CSO", "Italy Central South"),
    ("IT-SO", "Italy South"),
    ("IT-SAR", "Italy Sardinia"),
    ("IT-SIC", "Italy Sicily"),
    ("ES-IB", "Spain Balearic Islands"),
    ("ES-CN-FVLZ", "Spain Canary Islands"),
    ("DK-DK1", "Denmark West"),
    ("DK-DK2", "Denmark East"),
    ("SE-SE1", "Sweden North"),
    ("SE-SE2", "Sweden North-Central"),
    ("SE-SE3", "Sweden South-Central"),
    ("SE-SE4", "Sweden South"),
    ("NO-NO1", "Norway Zone 1"),
    ("NO-NO2", "Norway Zone 2"),
    ("NO-NO3", "Norway Zone 3"),
    ("NO-NO4", "Norway Zone 4"),
    ("NO-NO5", "Norway Zone 5"),
    ("GB-NIR", "Northern Ireland"),

    # Americas
    ("US", "United States"),
    ("CA", "Canada"),
    ("MX", "Mexico"),
    ("BR", "Brazil"),
    ("CL", "Chile"),
    ("AR", "Argentina"),
    ("UY", "Uruguay"),

    # US Sub-regions (country level doesn't work)
    ("US-CAL-CISO", "US California"),
    ("US-NY-NYIS", "US New York"),
    ("US-NE-ISNE", "US New England"),
    ("US-MIDA-PJM", "US Mid-Atlantic (PJM)"),
    ("US-TEX-ERCO", "US Texas"),
    ("US-NW-PACW", "US Pacific Northwest"),
    ("US-SE-SERC", "US Southeast"),
    ("US-MIDW-MISO", "US Midwest (MISO)"),

    # Canada Sub-regions
    ("CA-AB", "Canada Alberta"),
    ("CA-BC", "Canada British Columbia"),
    ("CA-MB", "Canada Manitoba"),
    ("CA-NB", "Canada New Brunswick"),
    ("CA-NS", "Canada Nova Scotia"),
    ("CA-ON", "Canada Ontario"),
    ("CA-QC", "Canada Quebec"),
    ("CA-SK", "Canada Saskatchewan"),

    # Asia-Pacific
    ("JP", "Japan"),
    ("AU", "Australia"),
    ("NZ", "New Zealand"),
    ("SG", "Singapore"),
    ("IN", "India"),
    ("KR", "South Korea"),
    ("TW", "Taiwan"),
    ("MY", "Malaysia"),
    ("TH", "Thailand"),
    ("VN", "Vietnam"),
    ("ID", "Indonesia"),
    ("PH", "Philippines"),

    # Australia Sub-regions
    ("AU-NSW", "Australia NSW"),
    ("AU-VIC", "Australia Victoria"),
    ("AU-QLD", "Australia Queensland"),
    ("AU-SA", "Australia South Australia"),
    ("AU-TAS", "Australia Tasmania"),

    # New Zealand Sub-regions
    ("NZ-NZN", "New Zealand North"),
    ("NZ-NZS", "New Zealand South"),

    # India Sub-regions
    ("IN-SO", "India South"),
    ("IN-NO", "India North"),
    ("IN-EA", "India East"),
    ("IN-WE", "India West"),

    # Middle East & Africa
    ("IL", "Israel"),
    ("TR", "Turkey"),
    ("ZA", "South Africa"),
    ("EG", "Egypt"),
]

def get_forecast_data(api_token, zone):
    """Get forecast data for a zone."""
    url = 'https://api.electricitymaps.com/v3/carbon-intensity/forecast'
    headers = {'auth-token': api_token}
    params = {'zone': zone}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            forecast = data.get('forecast', [])
            if forecast:
                values = [p['carbonIntensity'] for p in forecast]
                avg = sum(values) / len(values)
                min_val = min(values)
                max_val = max(values)

                return {
                    'zone': zone,
                    'avg': avg,
                    'min': min_val,
                    'max': max_val,
                    'forecast': forecast
                }
        return None
    except Exception as e:
        print(f"Error fetching data for zone {zone}: {e}")
        return None

def main():
    if len(sys.argv) < 2:
        print("Usage: python test_all_regions.py <API_TOKEN>")
        exit(1)

    api_token = sys.argv[1]

    print("="*90)
    print("Comprehensive Electricity Maps Zone Testing")
    print("Testing country-level AND sub-regions...")
    print("="*90)
    print()

    results = []
    all_forecasts = {
        "timestamp": datetime.now().isoformat(),
        "zones": {}
    }

    for zone, name in TEST_ZONES:
        result = get_forecast_data(api_token, zone)

        if result:
            print(f"✓ {zone:20s} {name:35s} - Avg: {result['avg']:6.1f}, Min: {result['min']:4.0f}, Max: {result['max']:4.0f} gCO2eq/kWh")
            results.append({
                'zone': zone,
                'name': name,
                'avg': result['avg'],
                'min': result['min'],
                'max': result['max']
            })
            all_forecasts['zones'][zone] = {
                'name': name,
                'forecast': result['forecast']
            }
        else:
            print(f"✗ {zone:20s} {name:35s} - No forecast data")

    # Save all forecast data
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(all_forecasts, f, indent=2)
    print(f"\n✓ Saved forecast data for {len(all_forecasts['zones'])} zones to {OUTPUT_PATH}")

    print()
    print("="*90)
    print(f"RANKING BY MINIMUM CARBON INTENSITY ({len(results)} zones with forecast data)")
    print("="*90)

    # Sort by minimum value
    results.sort(key=lambda x: x['min'])

    print("\nTOP 15 LOWEST MINIMUM CARBON INTENSITY ZONES:")
    print("-" * 90)
    for i, r in enumerate(results[:15], 1):
        print(f"{i:2d}. {r['zone']:20s} {r['name']:35s} - Min: {r['min']:4.0f}, Avg: {r['avg']:6.1f} gCO2eq/kWh")

    print("\n15 HIGHEST MINIMUM CARBON INTENSITY ZONES:")
    print("-" * 90)
    for i, r in enumerate(results[-15:], 1):
        print(f"{i:2d}. {r['zone']:20s} {r['name']:35s} - Min: {r['min']:4.0f}, Avg: {r['avg']:6.1f} gCO2eq/kWh")

    # Recommend regions for ai_agent.py
    print()
    print("="*90)
    print("RECOMMENDED REGIONS FOR AI_AGENT.PY:")
    print("="*90)
    print("Germany and France (required) + 8 lowest MINIMUM carbon intensity zones:\n")

    # Ensure DE and FR are included, then add 8 lowest others (by minimum)
    recommended = []

    # Add DE and FR first
    for r in results:
        if r['zone'] == 'DE':
            recommended.append(r)
            break
    for r in results:
        if r['zone'] == 'FR':
            recommended.append(r)
            break

    # Add 8 lowest zones by MINIMUM (excluding DE and FR if already added)
    count = 0
    for r in results:
        if r['zone'] not in ['DE', 'FR'] and count < 8:
            recommended.append(r)
            count += 1

    print("REGIONS = {")
    for r in recommended:
        print(f'    "{r["zone"]}": {{"name": "{r["name"]}", "emaps_zone": "{r["zone"]}"}},  # Min: {r["min"]:.0f}, Avg: {r["avg"]:.1f} gCO2eq/kWh')
    print("}")

if __name__ == "__main__":
    main()
