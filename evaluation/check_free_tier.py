#!/usr/bin/env python3
"""
Check if a project's usage would be within GCP Cloud Run free tier limits.

Free tier limits (monthly):
- Requests: 2,000,000
- vCPU-seconds: 180,000
- GiB-seconds: 360,000
- GPU-seconds: 0 (no free tier for GPU)

Usage:
    python check_free_tier.py --gcp-metrics results/iosl-project-static-green/gcp_metrics_*.json
"""

import argparse
import json
import os
import sys

# Free tier limits (monthly)
FREE_TIER = {
    'requests': 2_000_000,
    'vcpu_seconds': 180_000,
    'gib_seconds': 360_000,
    'gpu_seconds': 0,
}


def normalize_function_name(function_name: str) -> str:
    """Convert hyphens to underscores for function_metadata lookup."""
    return function_name.replace('-', '_')


def load_json(file_path: str) -> dict:
    with open(file_path, 'r') as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description='Check if usage is within free tier')
    parser.add_argument('--gcp-metrics', required=True, help='Path to GCP metrics JSON')
    parser.add_argument('--days', type=int, default=30, help='Days per month (default: 30)')
    args = parser.parse_args()

    # Load files
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)

    gcp_metrics = load_json(args.gcp_metrics)
    function_metadata = load_json(os.path.join(project_root, 'local_bucket', 'function_metadata.json'))
    static_config = load_json(os.path.join(project_root, 'local_bucket', 'static_config.json'))

    print(f"Project: {gcp_metrics.get('project_id', 'unknown')}")
    print(f"Experiment: {gcp_metrics.get('experiment_name', 'unknown')}")
    print(f"Scaling to: {args.days} days/month")
    print("=" * 80)

    # Totals
    total_requests = 0
    total_vcpu_seconds = 0
    total_gib_seconds = 0
    total_gpu_seconds = 0

    print(f"\n{'Function':<25} {'Invoc/day':>10} {'Billable(s)':>12} {'vCPUs':>6} {'Mem(GiB)':>9} {'GPU':>4}")
    print("-" * 80)

    for func_name, func_data in gcp_metrics.get('functions', {}).items():
        metrics = func_data.get('gcp_metrics', {})

        # Get billable time per request (what GCP actually charges)
        billable_time_total_s = metrics.get('billable_instance_time_s', 0)
        request_count = metrics.get('request_count', 1)
        billable_time_per_req_s = billable_time_total_s / request_count if request_count > 0 else 0

        # Get function allocation from function_metadata
        normalized_name = normalize_function_name(func_name)

        if normalized_name in function_metadata['functions']:
            func_meta = function_metadata['functions'][normalized_name]
            memory_mb = func_meta.get('memory_mb', 512)
            invocations_per_day = func_meta.get('invocations_per_day', 1)
            gpu_required = func_meta.get('gpu_required', False)

            # Get vCPUs: prefer explicit, fall back to defaults
            if 'vcpus' in func_meta:
                vcpus = func_meta['vcpus']
            elif gpu_required:
                vcpus = static_config['agent_defaults']['vcpus_if_gpu']
            else:
                vcpus = static_config['agent_defaults']['vcpus_default']
        else:
            # Fallback for special functions (dispatcher, agent)
            if func_name == 'dispatcher':
                memory_mb = 256
                vcpus = 0.333
                invocations_per_day = sum(
                    f['invocations_per_day']
                    for f in function_metadata['functions'].values()
                )
                gpu_required = False
            elif func_name == 'agent':
                memory_mb = 1024
                vcpus = 1.0
                invocations_per_day = 1
                gpu_required = False
            else:
                print(f"  Warning: {func_name} not found in function_metadata, using defaults")
                memory_mb = 512
                vcpus = 1
                invocations_per_day = 1
                gpu_required = False

        memory_gib = memory_mb / 1024.0
        invocations_per_month = invocations_per_day * args.days

        # Calculate monthly usage for this function
        requests_month = invocations_per_month
        vcpu_seconds_month = vcpus * billable_time_per_req_s * invocations_per_month
        gib_seconds_month = memory_gib * billable_time_per_req_s * invocations_per_month
        gpu_seconds_month = billable_time_per_req_s * invocations_per_month if gpu_required else 0

        total_requests += requests_month
        total_vcpu_seconds += vcpu_seconds_month
        total_gib_seconds += gib_seconds_month
        total_gpu_seconds += gpu_seconds_month

        gpu_str = "Yes" if gpu_required else "No"
        print(f"{func_name:<25} {invocations_per_day:>10} {billable_time_per_req_s:>12.2f} {vcpus:>6} {memory_gib:>9.2f} {gpu_str:>4}")

    print("-" * 80)
    print(f"\n{'MONTHLY TOTALS':^80}")
    print("=" * 80)

    # Check against free tier
    results = [
        ('Requests', total_requests, FREE_TIER['requests']),
        ('vCPU-seconds', total_vcpu_seconds, FREE_TIER['vcpu_seconds']),
        ('GiB-seconds', total_gib_seconds, FREE_TIER['gib_seconds']),
        ('GPU-seconds', total_gpu_seconds, FREE_TIER['gpu_seconds']),
    ]

    all_within_free_tier = True

    print(f"\n{'Metric':<20} {'Usage':>15} {'Free Tier':>15} {'%Used':>10} {'Status':>12}")
    print("-" * 80)

    for name, usage, limit in results:
        if limit > 0:
            pct = (usage / limit) * 100
            status = "FREE" if usage <= limit else "PAID"
        else:
            pct = float('inf') if usage > 0 else 0
            status = "PAID" if usage > 0 else "FREE"

        if usage > limit:
            all_within_free_tier = False

        if limit > 0:
            print(f"{name:<20} {usage:>15,.0f} {limit:>15,} {pct:>9.1f}% {status:>12}")
        else:
            print(f"{name:<20} {usage:>15,.0f} {'N/A':>15} {'N/A':>10} {status:>12}")

    print("-" * 80)

    if all_within_free_tier:
        print("\nResult: ALL USAGE WITHIN FREE TIER - No compute charges expected")
    else:
        print("\nResult: USAGE EXCEEDS FREE TIER - Compute charges will apply")

        # Calculate overage costs (simplified)
        tier1 = static_config['pricing']['tier1']

        overage_requests = max(0, total_requests - FREE_TIER['requests'])
        overage_vcpu = max(0, total_vcpu_seconds - FREE_TIER['vcpu_seconds'])
        overage_gib = max(0, total_gib_seconds - FREE_TIER['gib_seconds'])

        cost_requests = overage_requests * tier1['invocation_usd']
        cost_vcpu = overage_vcpu * tier1['vcpu_second_usd']
        cost_gib = overage_gib * tier1['memory_gib_second_usd']
        cost_gpu = total_gpu_seconds * static_config['pricing']['gpu']['nvidia-l4']['tier1_gpu_second_usd']

        total_cost = cost_requests + cost_vcpu + cost_gib + cost_gpu

        print(f"\nEstimated monthly cost (Tier 1 pricing):")
        print(f"  Requests overage:    ${cost_requests:>10.4f}")
        print(f"  vCPU-seconds overage: ${cost_vcpu:>10.4f}")
        print(f"  GiB-seconds overage:  ${cost_gib:>10.4f}")
        print(f"  GPU-seconds (no free): ${cost_gpu:>10.4f}")
        print(f"  {'TOTAL':>22}: ${total_cost:>10.4f}")


if __name__ == '__main__':
    main()
