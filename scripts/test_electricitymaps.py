#!/usr/bin/env python3
"""Quick test to check if API key is valid."""

import requests
import sys

# Fix encoding for Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except:
        pass

API_TOKEN = "VpIbTuGVPZglfeI34rIT"

print("Testing Electricity Maps API key...")
print("=" * 60)

# Test 1: Latest endpoint (simpler, faster)
print("\n1. Testing /latest endpoint for France (FR)...")
url = "https://api.electricitymaps.com/v3/carbon-intensity/latest"
headers = {"auth-token": API_TOKEN}
params = {"zone": "FR"}

try:
    response = requests.get(url, headers=headers, params=params, timeout=10)
    print(f"   HTTP Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"   ✓ SUCCESS - API key is valid!")
        print(f"   Carbon intensity: {data.get('carbonIntensity', 'N/A')} gCO2eq/kWh")
        print(f"   Zone: {data.get('zone', 'N/A')}")
    elif response.status_code == 401:
        print(f"   ✗ UNAUTHORIZED - API key is invalid or expired")
    elif response.status_code == 403:
        print(f"   ✗ FORBIDDEN - API key quota may be exhausted")
    elif response.status_code >= 500:
        print(f"   ⚠ SERVER ERROR - Electricity Maps API is experiencing issues")
        print(f"   This is NOT a key problem - the service is down")
    else:
        print(f"   Response: {response.text[:200]}")

except Exception as e:
    print(f"   ✗ Request failed: {e}")

# Test 2: Forecast endpoint
print("\n2. Testing /forecast endpoint for France (FR)...")
url_forecast = "https://api.electricitymaps.com/v3/carbon-intensity/forecast"
params_forecast = {"zone": "FR", "horizonHours": 24}

try:
    response = requests.get(url_forecast, headers=headers, params=params_forecast, timeout=10)
    print(f"   HTTP Status: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        forecast = data.get('forecast', [])
        print(f"   ✓ SUCCESS - Forecast endpoint working!")
        print(f"   Forecast points: {len(forecast)}")
    elif response.status_code == 401:
        print(f"   ✗ UNAUTHORIZED - API key is invalid or expired")
    elif response.status_code == 403:
        print(f"   ✗ FORBIDDEN - API key quota may be exhausted")
    elif response.status_code >= 500:
        print(f"   ⚠ SERVER ERROR - Forecast endpoint is down")
        print(f"   This is NOT a key problem - the service is down")
    else:
        print(f"   Response: {response.text[:200]}")

except Exception as e:
    print(f"   ✗ Request failed: {e}")

print("\n" + "=" * 60)
print("CONCLUSION:")
print("=" * 60)
print("If you see 401/403 errors: Your API key has a problem")
print("If you see 500+ errors: Electricity Maps service is down (not your fault)")
print("If you see 200 success: Everything is working!")
