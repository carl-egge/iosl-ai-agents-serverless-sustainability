#!/usr/bin/env python3
"""
Test script to run all 10 example functions through the scheduler.
Shows how different workload characteristics lead to different region selection strategies.

IMPORTANT: The 'expected_strategy' and 'scenario' fields in example_functions.json are
for documentation/validation only. They are NOT passed to the AI agent. The agent makes
decisions based solely on: runtime, memory, data volume, invocations, and source location.

This allows us to validate that the AI independently arrives at the expected strategies
without being told what to choose.
"""

import sys
import json
from pathlib import Path

# Fix encoding for Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.planner import (
    get_carbon_forecasts_all_regions_local,
    get_gemini_schedule_local,
    load_static_config,
)


def load_example_functions():
    """Load the 10 example functions."""
    examples_path = Path(__file__).parent.parent / "data" / "sample" / "example_functions.json"
    with open(examples_path, "r") as f:
        data = json.load(f)
    return data["functions"]


def print_function_summary(func):
    """Print a summary of the function characteristics."""
    print("\n" + "=" * 80)
    print(f"SCENARIO: {func['scenario']}")
    print("=" * 80)
    print(f"Function: {func['name']} ({func['function_id']})")
    print(f"Runtime: {func['runtime_ms']}ms | Memory: {func['memory_mb']}MB")
    print(f"Data: {func['data_input_gb']}GB in + {func['data_output_gb']}GB out = {func['data_input_gb'] + func['data_output_gb']}GB total")
    print(f"Invocations: {func['invocations_per_day']}/day")
    print(f"Daily volume: {(func['data_input_gb'] + func['data_output_gb']) * func['invocations_per_day']:.1f} GB/day")
    print(f"Source: {func['source_location']}")
    print(f"Instant execution: {func['instant_execution']}")
    print(f"\nExpected strategy: {func['expected_strategy']}")


def print_recommendation(rec, rank):
    """Print a single recommendation."""
    print(f"\n{rank}. {rec['datetime']} | {rec['region']:15s} | {rec['carbon_intensity']:3d} gCO2/kWh | ${rec['transfer_cost_usd']:.4f}")
    print(f"   Priority: {rec['priority']}")
    print(f"   Reasoning: {rec['reasoning']}")


def run_example_function(func, api_token, gemini_key):
    """Run a single example function through the scheduler."""
    print_function_summary(func)

    print("\n" + "-" * 80)
    print("RUNNING SCHEDULER...")
    print("-" * 80)

    try:
        # Get carbon forecasts (use cached if possible to save API calls)
        print("Fetching carbon forecasts...")
        forecasts = get_carbon_forecasts_all_regions_local(api_token)

        # Run scheduler
        print("Generating schedule with Gemini...")
        schedule = get_gemini_schedule_local(func, forecasts)

        # Show top 3 recommendations
        recommendations = schedule.get("recommendations", [])
        sorted_recs = sorted(recommendations, key=lambda x: x.get("priority", 999))

        print("\n" + "-" * 80)
        print("TOP 3 RECOMMENDATIONS:")
        print("-" * 80)

        for i, rec in enumerate(sorted_recs[:3], 1):
            print_recommendation(rec, i)

        # Check if there's variety in the recommendations
        unique_regions = set(rec.get("region") for rec in sorted_recs)
        unique_priorities = set(rec.get("priority") for rec in sorted_recs)

        if len(unique_regions) > 1 or len(unique_priorities) > 1:
            # Show the worst recommendation only if there's actual variation
            print("\n" + "-" * 80)
            print("WORST RECOMMENDATION (for comparison):")
            print("-" * 80)
            print_recommendation(sorted_recs[-1], len(sorted_recs))
        else:
            # All recommendations are identical
            print("\n" + "-" * 80)
            print("NOTE: All 24 recommendations are identical")
            print("-" * 80)
            print(f"All {len(sorted_recs)} hours recommend: {sorted_recs[0].get('region')}")
            print(f"This means the workload characteristics (data volume, invocation count)")
            print(f"make this region optimal regardless of time-of-day carbon variations.")

        return True

    except Exception as exc:
        print(f"\n✗ ERROR: {exc}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all example functions."""
    import os

    # Check for API keys
    api_token = os.environ.get("ELECTRICITYMAPS_TOKEN")
    gemini_key = os.environ.get("GEMINI_API_KEY")

    if not api_token:
        print("✗ ERROR: ELECTRICITYMAPS_TOKEN environment variable not set")
        print("Set it in your .env file or export it")
        return 1

    if not gemini_key:
        print("✗ ERROR: GEMINI_API_KEY environment variable not set")
        print("Set it in your .env file or export it")
        return 1

    # Load example functions
    functions = load_example_functions()

    print("\n" + "=" * 80)
    print("SERVERLESS FUNCTION SCHEDULING - 10 SCENARIO TEST")
    print("=" * 80)
    print(f"Testing {len(functions)} example functions covering all major scenarios")
    print("Each function demonstrates how workload characteristics affect region selection")

    # Run each function
    results = {}
    for func in functions:
        success = run_example_function(func, api_token, gemini_key)
        results[func['id']] = "✓" if success else "✗"

        # Add a separator between functions
        print("\n" + "█" * 80 + "\n")

    # Print summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    for func in functions:
        status = results[func['id']]
        print(f"{status} {func['scenario']:35s} - {func['name']}")

    success_count = sum(1 for v in results.values() if v == "✓")
    print(f"\nCompleted: {success_count}/{len(functions)} scenarios")

    return 0 if success_count == len(functions) else 1


if __name__ == "__main__":
    try:
        # Load dotenv if available
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        sys.exit(main())

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(130)
