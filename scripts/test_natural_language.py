"""
Test script for natural language function description parsing.

This script tests the parse_natural_language_request() function with various
example descriptions to validate the AI's ability to extract technical metadata.

Usage:
    python scripts/test_natural_language.py [example_number]

    If no example number is provided, tests all examples from natural_language_examples.json
"""

import sys
import os
import json
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent.planner import parse_natural_language_request
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def load_examples():
    """Load example descriptions from natural_language_examples.json"""
    examples_path = Path(__file__).parent.parent / "data" / "sample" / "natural_language_examples.json"

    if not examples_path.exists():
        print(f"[ERROR] {examples_path} not found")
        sys.exit(1)

    with open(examples_path, 'r') as f:
        data = json.load(f)

    return data.get("examples", [])

def print_comparison(extracted, expected):
    """Print a comparison between extracted and expected metadata"""
    print("\nComparison with Expected Values:")
    print("-" * 80)

    fields_to_compare = [
        "function_id", "runtime_ms", "memory_mb", "instant_execution",
        "data_input_gb", "data_output_gb", "source_location", "invocations_per_day"
    ]

    for field in fields_to_compare:
        extracted_val = extracted.get(field, "N/A")
        expected_val = expected.get(field, "N/A")

        if extracted_val == expected_val:
            status = "[MATCH]"
        elif field in ["runtime_ms", "memory_mb", "data_input_gb", "data_output_gb"]:
            # Allow some variance for estimated values
            try:
                if abs(float(extracted_val) - float(expected_val)) / float(expected_val) < 0.3:
                    status = "[CLOSE]"
                else:
                    status = "[DIFF]"
            except (ValueError, ZeroDivisionError):
                status = "[DIFF]"
        else:
            status = "[DIFF]"

        print(f"{status} {field:20} | Extracted: {extracted_val:20} | Expected: {expected_val}")

    print("-" * 80)

def test_example(example, show_full_output=True):
    """Test a single example description"""
    print("\n" + "="*80)
    print(f"Testing: {example['name']}")
    print("="*80)

    print(f"\nDescription:")
    print(f"   \"{example['description']}\"")

    print(f"\nCalling Gemini to extract metadata...")

    try:
        # Get API key from environment
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("[ERROR] GEMINI_API_KEY not found in environment")
            print("   Make sure you have a .env file with GEMINI_API_KEY set")
            return False

        # Parse the natural language description
        extracted_metadata = parse_natural_language_request(example['description'], api_key)

        if not extracted_metadata:
            print("[ERROR] No metadata extracted")
            return False

        print(f"\n[SUCCESS] Metadata extracted successfully!")

        if show_full_output:
            print(f"\nExtracted Metadata:")
            print(json.dumps(extracted_metadata, indent=2))

        # Compare with expected values if available
        if "expected_metadata" in example:
            print_comparison(extracted_metadata, example["expected_metadata"])

        # Show confidence and assumptions
        confidence = extracted_metadata.get("confidence_score", 0)
        print(f"\nConfidence Score: {confidence:.2f}")

        if confidence < 0.7:
            print("[WARNING] Low confidence - review assumptions carefully")

        assumptions = extracted_metadata.get("assumptions", [])
        if assumptions:
            print(f"\nAssumptions Made ({len(assumptions)}):")
            for i, assumption in enumerate(assumptions, 1):
                print(f"   {i}. {assumption}")

        warnings = extracted_metadata.get("warnings", [])
        if warnings:
            print(f"\nWarnings ({len(warnings)}):")
            for i, warning in enumerate(warnings, 1):
                print(f"   {i}. {warning}")

        return True

    except Exception as e:
        print(f"[ERROR] {str(e)}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Main test function"""
    print("\nNatural Language Function Description Parser - Test Suite")
    print("="*80)

    # Load examples
    examples = load_examples()
    print(f"\nLoaded {len(examples)} example descriptions")

    # Check if specific example requested
    if len(sys.argv) > 1:
        try:
            example_num = int(sys.argv[1])
            if 1 <= example_num <= len(examples):
                example = examples[example_num - 1]
                success = test_example(example, show_full_output=True)
                sys.exit(0 if success else 1)
            else:
                print(f"[ERROR] Example number must be between 1 and {len(examples)}")
                sys.exit(1)
        except ValueError:
            print(f"[ERROR] Invalid example number '{sys.argv[1]}'")
            sys.exit(1)

    # Test all examples
    print("\nTesting all examples...")
    results = []

    for i, example in enumerate(examples, 1):
        print(f"\n{'='*80}")
        print(f"Test {i}/{len(examples)}")
        success = test_example(example, show_full_output=False)
        results.append((example['name'], success))

        if i < len(examples):
            input("\nPress Enter to continue to next example...")

    # Print summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)

    for name, success in results:
        status = "[PASS]" if success else "[FAIL]"
        print(f"{status} - {name}")

    passed = sum(1 for _, success in results if success)
    print(f"\n{passed}/{len(results)} tests passed")

    sys.exit(0 if passed == len(results) else 1)

if __name__ == "__main__":
    main()
