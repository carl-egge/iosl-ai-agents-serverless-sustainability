"""
Interactive test script for the full natural language pipeline.

This script tests the complete two-stage process:
1. Natural language → Metadata extraction
2. User review and approval
3. Metadata → Carbon-aware schedule generation

Usage:
    python scripts/test_full_pipeline.py
    python scripts/test_full_pipeline.py "Your custom description here"
"""

import sys
import os
import json
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

# IMPORTANT: Load environment variables BEFORE importing planner
# because planner.py reads env vars at module load time
load_dotenv()

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.planner import (
    parse_natural_language_request,
    get_carbon_forecasts_all_regions_gcp,
    get_gemini_schedule_gcp
)

def print_metadata(metadata):
    """Pretty print extracted metadata"""
    print("\n" + "="*80)
    print("EXTRACTED METADATA")
    print("="*80)

    print(f"\nFunction ID:       {metadata.get('function_id')}")
    print(f"Runtime (ms):      {metadata.get('runtime_ms')}")
    print(f"Memory (MB):       {metadata.get('memory_mb')}")
    print(f"Instant Execution: {metadata.get('instant_execution')}")
    print(f"Description:       {metadata.get('description')}")
    print(f"Data Input (GB):   {metadata.get('data_input_gb')}")
    print(f"Data Output (GB):  {metadata.get('data_output_gb')}")
    print(f"Source Location:   {metadata.get('source_location')}")
    print(f"Invocations/Day:   {metadata.get('invocations_per_day')}")

    confidence = metadata.get('confidence_score', 0)
    print(f"\nConfidence Score:  {confidence:.2f}")

    if confidence < 0.7:
        print("[WARNING] Low confidence - review carefully!")

    assumptions = metadata.get('assumptions', [])
    if assumptions:
        print(f"\nAssumptions ({len(assumptions)}):")
        for i, assumption in enumerate(assumptions, 1):
            print(f"  {i}. {assumption}")

    warnings = metadata.get('warnings', [])
    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for i, warning in enumerate(warnings, 1):
            print(f"  {i}. {warning}")

    print("="*80)

def edit_metadata(metadata):
    """Allow user to edit metadata fields"""
    print("\n" + "="*80)
    print("EDIT METADATA")
    print("="*80)
    print("Press Enter to keep current value, or type new value to change.")
    print("-"*80)

    editable_fields = [
        ('function_id', str),
        ('runtime_ms', int),
        ('memory_mb', int),
        ('instant_execution', lambda x: x.lower() in ('true', 'yes', '1')),
        ('data_input_gb', float),
        ('data_output_gb', float),
        ('source_location', str),
        ('invocations_per_day', int)
    ]

    edited = metadata.copy()

    for field, converter in editable_fields:
        current = metadata.get(field)
        user_input = input(f"{field} [{current}]: ").strip()

        if user_input:
            try:
                edited[field] = converter(user_input)
                print(f"  -> Updated to: {edited[field]}")
            except ValueError:
                print(f"  -> Invalid input, keeping: {current}")
                edited[field] = current
        else:
            edited[field] = current

    return edited

def print_schedule_summary(schedule):
    """Print summary of generated schedule"""
    print("\n" + "="*80)
    print("CARBON-AWARE SCHEDULE GENERATED")
    print("="*80)

    recommendations = schedule.get('recommendations', [])

    if not recommendations:
        print("\n[WARNING] No recommendations generated")
        return

    # Sort by priority
    sorted_recs = sorted(recommendations, key=lambda x: x.get('priority', 999))

    print(f"\nTotal Recommendations: {len(recommendations)}")
    print(f"\nTop 5 Recommendations:\n")

    for i, rec in enumerate(sorted_recs[:5], 1):
        print(f"{i}. Priority {rec.get('priority')}")
        print(f"   Region:     {rec.get('region')}")
        print(f"   Datetime:   {rec.get('datetime')}")
        print(f"   Carbon:     {rec.get('carbon_intensity')} gCO2/kWh")
        print(f"   Transfer:   ${rec.get('transfer_cost_usd', 0):.4f}")
        print(f"   Reasoning:  {rec.get('reasoning', 'N/A')[:100]}...")
        print()

def save_results_locally(metadata, schedule, description):
    """Save results to local data/sample directory"""
    output_dir = Path(__file__).parent.parent / "data" / "sample"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save schedule
    schedule_path = output_dir / "execution_schedule.json"
    with open(schedule_path, 'w') as f:
        json.dump(schedule, f, indent=2)

    # Save metadata with original description
    metadata_path = output_dir / "nl_extracted_metadata.json"
    metadata_record = {
        "timestamp": datetime.now().isoformat(),
        "original_description": description,
        "extracted_metadata": metadata,
        "schedule_location": str(schedule_path)
    }
    with open(metadata_path, 'w') as f:
        json.dump(metadata_record, f, indent=2)

    print(f"\n[SUCCESS] Results saved:")
    print(f"  Schedule:  {schedule_path}")
    print(f"  Metadata:  {metadata_path}")

def main():
    """Main interactive pipeline"""
    print("\n" + "="*80)
    print("NATURAL LANGUAGE TO CARBON-AWARE SCHEDULE - INTERACTIVE PIPELINE")
    print("="*80)

    # Check API keys
    gemini_key = os.getenv("GEMINI_API_KEY")
    emaps_token = os.getenv("ELECTRICITYMAPS_TOKEN")

    if not gemini_key:
        print("\n[ERROR] GEMINI_API_KEY not found in environment")
        print("Make sure you have a .env file with GEMINI_API_KEY set")
        sys.exit(1)

    if not emaps_token:
        print("\n[ERROR] ELECTRICITYMAPS_TOKEN not found in environment")
        print("Make sure you have a .env file with ELECTRICITYMAPS_TOKEN set")
        sys.exit(1)

    print("[OK] API keys found")

    # Get description from user
    if len(sys.argv) > 1:
        description = " ".join(sys.argv[1:])
        print(f"\nUsing provided description:")
        print(f'  "{description}"')
    else:
        print("\nEnter your function description (or press Enter for default example):")
        description = input("> ").strip()

        if not description:
            description = "Process user-uploaded images (5MB each) from us-east1. Resize to 3 sizes. Runs 500 times per day."
            print(f"\nUsing default example:")
            print(f'  "{description}"')

    # Stage 1: Extract metadata
    while True:
        print("\n" + "-"*80)
        print("STAGE 1: EXTRACTING METADATA FROM NATURAL LANGUAGE")
        print("-"*80)

        try:
            metadata = parse_natural_language_request(description, gemini_key)

            if not metadata:
                print("[ERROR] Failed to extract metadata")
                sys.exit(1)

            print("\n[SUCCESS] Metadata extracted!")
            print_metadata(metadata)

        except Exception as e:
            print(f"\n[ERROR] {str(e)}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # User review and decision
        print("\n" + "-"*80)
        print("REVIEW OPTIONS")
        print("-"*80)
        print("1. Approve and continue to scheduling")
        print("2. Edit metadata and continue")
        print("3. Edit description and re-extract")
        print("4. Cancel")

        choice = input("\nYour choice [1]: ").strip() or "1"

        if choice == "1":
            print("\n[APPROVED] Proceeding to scheduling...")
            break

        elif choice == "2":
            metadata = edit_metadata(metadata)
            print("\n[UPDATED] Using edited metadata")
            print_metadata(metadata)

            confirm = input("\nProceed to scheduling with these values? [y/n]: ").strip().lower()
            if confirm in ('y', 'yes', ''):
                break
            else:
                print("\n[INFO] Returning to review options...")
                continue

        elif choice == "3":
            print("\nEnter new description:")
            new_description = input("> ").strip()
            if new_description:
                description = new_description
                print(f"\n[UPDATED] New description: \"{description}\"")
                print("[INFO] Re-extracting metadata...")
            else:
                print("[INFO] Keeping original description")
            continue

        elif choice == "4":
            print("\n[CANCELLED] Exiting without scheduling")
            sys.exit(0)

        else:
            print("[ERROR] Invalid choice, please try again")
            continue

    # Stage 2: Generate schedule
    print("\n" + "-"*80)
    print("STAGE 2: GENERATING CARBON-AWARE SCHEDULE")
    print("-"*80)

    try:
        # Fetch carbon forecasts
        print("\nFetching carbon intensity forecasts...")
        carbon_forecasts, failed_regions = get_carbon_forecasts_all_regions_gcp()

        regions_count = len(carbon_forecasts)
        print(f"[SUCCESS] Fetched forecasts for {regions_count} regions")

        if failed_regions:
            print(f"[WARNING] Failed to fetch {len(failed_regions)} regions: {', '.join(failed_regions)}")

        # Generate schedule
        print("\nGenerating schedule with Gemini AI...")
        schedule = get_gemini_schedule_gcp(metadata, carbon_forecasts)

        # Add metadata to schedule
        schedule['metadata'] = {
            'generated_at': datetime.now().isoformat(),
            'function_metadata': metadata,
            'regions_used': list(carbon_forecasts.keys()),
            'failed_regions': failed_regions,
            'original_description': description,
            'source': 'interactive_pipeline'
        }

        print("[SUCCESS] Schedule generated!")
        print_schedule_summary(schedule)

        # Save results
        save_results_locally(metadata, schedule, description)

        print("\n" + "="*80)
        print("PIPELINE COMPLETED SUCCESSFULLY")
        print("="*80)

    except Exception as e:
        print(f"\n[ERROR] Scheduling failed: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
