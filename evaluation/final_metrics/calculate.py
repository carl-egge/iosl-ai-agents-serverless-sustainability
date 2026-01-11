#!/usr/bin/env python3
"""
Final Metrics Calculator for GPS-UP Evaluation

Calculates per-invocation energy consumption, carbon emissions, and transfer costs
based on GCP metrics and static configuration.

Usage:
  # Single function mode
  python calculate.py \
    --gcp-metrics evaluation/data/gcp_metrics_dispatcher_20260111_130943.json \
    --function-name dispatcher \
    --carbon-intensity 400 \
    --output evaluation/data/final_metrics_dispatcher_20260111_150000.json

  # Batch mode (processes all functions in GCP metrics file)
  python calculate.py \
    --gcp-metrics evaluation/data/gcp_metrics_project-a_20260111_130943.json \
    --carbon-intensity 400 \
    --output evaluation/data/final_metrics_project-a_20260111_150000.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Optional


# Cache for loaded configuration files
_static_config_cache = None
_function_metadata_cache = None


def load_static_config(file_path: str = None) -> Dict:
    """Load static configuration from local_bucket/static_config.json."""
    global _static_config_cache

    if _static_config_cache is not None:
        return _static_config_cache

    if file_path is None:
        # Default path relative to script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        file_path = os.path.join(project_root, 'local_bucket', 'static_config.json')

    with open(file_path, 'r') as f:
        _static_config_cache = json.load(f)

    return _static_config_cache


def load_function_metadata(file_path: str = None) -> Dict:
    """Load function metadata from local_bucket/function_metadata.json."""
    global _function_metadata_cache

    if _function_metadata_cache is not None:
        return _function_metadata_cache

    if file_path is None:
        # Default path relative to script location
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        file_path = os.path.join(project_root, 'local_bucket', 'function_metadata.json')

    with open(file_path, 'r') as f:
        _function_metadata_cache = json.load(f)

    return _function_metadata_cache


def load_gcp_metrics(file_path: str) -> Dict:
    """Load raw GCP metrics from JSON file."""
    with open(file_path, 'r') as f:
        return json.load(f)


def get_function_allocation(function_name: str) -> Dict:
    """
    Get allocated memory, vCPUs, and GPU requirement for a function.

    Priority:
    1. function_metadata.json (if function_name matches)
    2. static_config.json agent_defaults (vcpus_default=1 for non-GPU, vcpus_if_gpu=8 for GPU)

    Returns:
        {
            'allocated_memory_mb': int,
            'allocated_vcpus': float,
            'gpu_required': bool
        }
    """
    metadata = load_function_metadata()
    static_config = load_static_config()

    if function_name in metadata['functions']:
        func_data = metadata['functions'][function_name]
        gpu_required = func_data.get('gpu_required', False)

        # Get vCPUs from static_config defaults based on GPU requirement
        if gpu_required:
            vcpus = static_config['agent_defaults']['vcpus_if_gpu']  # 8
        else:
            vcpus = static_config['agent_defaults']['vcpus_default']  # 1

        return {
            'allocated_memory_mb': func_data['memory_mb'],
            'allocated_vcpus': vcpus,
            'gpu_required': gpu_required
        }
    else:
        # Fallback to defaults
        return {
            'allocated_memory_mb': 512,  # Default
            'allocated_vcpus': static_config['agent_defaults']['vcpus_default'],
            'gpu_required': False
        }


def calculate_energy_per_invocation(
    allocated_vcpus: float,
    allocated_memory_mb: int,
    runtime_ms: float,
    cpu_utilization_actual: float,
    data_received_gb: float,
    data_sent_gb: float,
    request_count: int,
    gpu_required: bool = False,
    static_config: Dict = None
) -> Dict:
    """
    Calculate energy consumption per invocation.

    Returns:
        {
            'compute_energy_kwh': float,
            'network_energy_kwh': float,
            'total_energy_kwh': float,
            'breakdown': {
                'cpu_power_w': float,
                'memory_power_w': float,
                'gpu_power_w': float,
                'runtime_s': float
            }
        }
    """
    # Load constants from static_config.json
    power_constants = static_config['power_constants']

    CPU_WATTS_PER_VCPU = power_constants['cpu_watts_per_vcpu']
    MEMORY_WATTS_PER_GIB = power_constants['memory_watts_per_gib']
    DATACENTER_PUE = power_constants['datacenter_pue']
    NETWORK_KWH_PER_GB = power_constants['network_kwh_per_gb']

    # GPU constants (load from static_config.json if GPU required)
    if gpu_required:
        GPU_TDP_WATTS = power_constants['gpu_tdp_watts']['nvidia-l4']
        GPU_UTILIZATION_FACTOR = power_constants['gpu_utilization_factor']
        GPU_COUNT = static_config['agent_defaults']['gpu_count']
    else:
        GPU_TDP_WATTS = 0
        GPU_UTILIZATION_FACTOR = 0
        GPU_COUNT = 0

    # Convert units
    allocated_memory_gib = allocated_memory_mb / 1024
    runtime_s = runtime_ms / 1000

    # Power consumption (uses ACTUAL CPU utilization from GCP)
    cpu_power_w = allocated_vcpus * CPU_WATTS_PER_VCPU * cpu_utilization_actual
    memory_power_w = allocated_memory_gib * MEMORY_WATTS_PER_GIB

    # GPU power (only if GPU required)
    if gpu_required:
        gpu_power_w = GPU_COUNT * GPU_TDP_WATTS * GPU_UTILIZATION_FACTOR
    else:
        gpu_power_w = 0

    # Compute energy (per invocation) - includes GPU if present
    total_power_w = cpu_power_w + memory_power_w + gpu_power_w
    compute_energy_kwh = total_power_w * (runtime_s / 3600) * DATACENTER_PUE

    # Network energy (total for all requests, divided by request count)
    total_transfer_gb = data_received_gb + data_sent_gb
    network_energy_kwh_total = total_transfer_gb * NETWORK_KWH_PER_GB
    network_energy_kwh = network_energy_kwh_total / request_count

    return {
        'compute_energy_kwh': compute_energy_kwh,
        'network_energy_kwh': network_energy_kwh,
        'total_energy_kwh': compute_energy_kwh + network_energy_kwh,
        'breakdown': {
            'cpu_power_w': cpu_power_w,
            'memory_power_w': memory_power_w,
            'gpu_power_w': gpu_power_w,
            'runtime_s': runtime_s
        }
    }


def calculate_emissions_per_invocation(
    energy_kwh: float,
    carbon_intensity_g_per_kwh: float
) -> float:
    """
    Calculate CO2 emissions per invocation.

    Args:
        energy_kwh: Total energy per invocation (from calculate_energy_per_invocation)
        carbon_intensity_g_per_kwh: Average carbon intensity for the region/time

    Returns:
        float: grams of CO2 per invocation
    """
    return energy_kwh * carbon_intensity_g_per_kwh


def calculate_transfer_cost_per_invocation(
    data_received_gb: float,
    data_sent_gb: float,
    request_count: int,
    region: str,
    static_config: Dict
) -> Dict:
    """
    Calculate TRANSFER COST ONLY per invocation.

    Note: Compute costs (CPU, memory, invocation, GPU) are NOT included.
    Only data transfer costs are calculated for regional comparison.

    Returns:
        {
            'transfer_cost_usd': float,
            'breakdown': {
                'total_transfer_gb': float,
                'transfer_rate_per_gb': float
            }
        }
    """
    # Get transfer rate for region
    transfer_rate = static_config['regions'][region]['data_transfer_cost_per_gb_usd']

    # Transfer cost (total for all requests, divided by request count)
    total_transfer_gb = data_received_gb + data_sent_gb
    transfer_cost_total = total_transfer_gb * transfer_rate
    transfer_cost_per_invocation = transfer_cost_total / request_count

    return {
        'transfer_cost_usd': transfer_cost_per_invocation,
        'breakdown': {
            'total_transfer_gb': total_transfer_gb,
            'transfer_rate_per_gb': transfer_rate
        }
    }


def calculate_per_year_metrics(
    per_invocation_metrics: Dict,
    function_name: str,
    function_metadata: Dict
) -> Dict:
    """
    Scale per-invocation metrics to annual totals (TRANSFER COSTS ONLY).

    Args:
        per_invocation_metrics: Output from calculate_metrics_for_function()
        function_name: Function name to look up invocations_per_day
        function_metadata: Loaded function_metadata.json

    Returns:
        {
            'annual_invocations': int,
            'invocations_per_day': int,
            'energy': {
                'total_energy_kwh': float,
                'compute_energy_kwh': float,
                'network_energy_kwh': float
            },
            'emissions': {
                'total_carbon_kg': float
            },
            'transfer_costs': {
                'annual_transfer_cost_usd': float
            }
        }
    """
    # Get invocations_per_day from function_metadata.json
    if function_name in function_metadata['functions']:
        invocations_per_day = function_metadata['functions'][function_name]['invocations_per_day']
    else:
        # Fallback to conservative estimate
        invocations_per_day = 100

    annual_invocations = invocations_per_day * 365

    # Scale energy
    per_inv_energy = per_invocation_metrics['per_invocation']['energy']
    annual_energy = {
        'total_energy_kwh': per_inv_energy['total_energy_kwh'] * annual_invocations,
        'compute_energy_kwh': per_inv_energy['compute_energy_kwh'] * annual_invocations,
        'network_energy_kwh': per_inv_energy['network_energy_kwh'] * annual_invocations
    }

    # Scale emissions (convert grams to kg)
    per_inv_carbon_g = per_invocation_metrics['per_invocation']['emissions']['total_carbon_g']
    annual_emissions = {
        'total_carbon_kg': (per_inv_carbon_g * annual_invocations) / 1000
    }

    # Scale transfer costs (ONLY - no container costs)
    per_inv_transfer = per_invocation_metrics['per_invocation']['transfer_costs']['transfer_cost_usd']
    annual_transfer_cost = per_inv_transfer * annual_invocations

    return {
        'annual_invocations': annual_invocations,
        'invocations_per_day': invocations_per_day,
        'energy': annual_energy,
        'emissions': annual_emissions,
        'transfer_costs': {
            'annual_transfer_cost_usd': annual_transfer_cost
        }
    }


def resolve_function_config(function_name: str, gcp_metrics: Dict) -> Dict:
    """
    Resolve complete function configuration.

    Combines:
    - GCP metrics (runtime, CPU utilization, network, region)
    - function_metadata.json (memory, vCPUs via defaults)

    Returns:
        Complete configuration dict for calculations
    """
    # 1. Get allocation from function_metadata.json
    allocation = get_function_allocation(function_name)

    # 2. Extract from GCP metrics
    from_gcp = {
        'region': gcp_metrics['region'],
        'request_count': gcp_metrics['gcp_metrics']['request_count'],
        'runtime_ms': gcp_metrics['gcp_metrics']['request_latencies_ms']['mean'],
        'cpu_utilization_actual': gcp_metrics['gcp_metrics']['cpu_utilization']['mean'],
        'memory_utilization_actual': gcp_metrics['gcp_metrics']['memory_utilization']['mean'],
        'data_received_gb': gcp_metrics['gcp_metrics']['network']['received_gb'],
        'data_sent_gb': gcp_metrics['gcp_metrics']['network']['sent_gb']
    }

    # 3. Combine
    return {
        **allocation,
        **from_gcp
    }


def calculate_metrics_for_function(
    function_name: str,
    gcp_metrics: Dict,
    carbon_intensity_g_per_kwh: float,
    static_config: Dict,
    function_metadata: Dict = None
) -> Dict:
    """
    Complete calculation pipeline for one function.

    Returns:
        {
            'function_name': str,
            'inputs': {...},
            'per_invocation': {
                'energy': {...},
                'emissions': {...},
                'transfer_costs': {...}
            },
            'per_year': {...}  # Only if function_metadata provided
        }
    """
    # 1. Resolve function config
    config = resolve_function_config(function_name, gcp_metrics)

    # 2. Calculate energy
    energy = calculate_energy_per_invocation(
        allocated_vcpus=config['allocated_vcpus'],
        allocated_memory_mb=config['allocated_memory_mb'],
        runtime_ms=config['runtime_ms'],
        cpu_utilization_actual=config['cpu_utilization_actual'],
        data_received_gb=config['data_received_gb'],
        data_sent_gb=config['data_sent_gb'],
        request_count=config['request_count'],
        gpu_required=config['gpu_required'],
        static_config=static_config
    )

    # 3. Calculate emissions
    emissions_g = calculate_emissions_per_invocation(
        energy_kwh=energy['total_energy_kwh'],
        carbon_intensity_g_per_kwh=carbon_intensity_g_per_kwh
    )

    # 4. Calculate transfer costs
    transfer_costs = calculate_transfer_cost_per_invocation(
        data_received_gb=config['data_received_gb'],
        data_sent_gb=config['data_sent_gb'],
        request_count=config['request_count'],
        region=config['region'],
        static_config=static_config
    )

    # 5. Build per-invocation result
    result = {
        'function_name': function_name,
        'region': config['region'],
        'inputs': {
            'allocated_memory_mb': config['allocated_memory_mb'],
            'allocated_vcpus': config['allocated_vcpus'],
            'gpu_required': config.get('gpu_required', False),
            'carbon_intensity_g_per_kwh': carbon_intensity_g_per_kwh,
            'from_gcp_metrics': {
                'request_count': config['request_count'],
                'runtime_ms': config['runtime_ms'],
                'cpu_utilization_actual': config['cpu_utilization_actual'],
                'memory_utilization_actual': config['memory_utilization_actual'],
                'data_received_gb': config['data_received_gb'],
                'data_sent_gb': config['data_sent_gb']
            }
        },
        'per_invocation': {
            'energy': energy,
            'emissions': {'total_carbon_g': emissions_g},
            'transfer_costs': transfer_costs
        }
    }

    # 6. Calculate per-year metrics (if function_metadata provided)
    if function_metadata:
        result['per_year'] = calculate_per_year_metrics(
            per_invocation_metrics=result,
            function_name=function_name,
            function_metadata=function_metadata
        )

    return result


def main():
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        description='Calculate per-invocation energy, emissions, and transfer costs from GCP metrics'
    )

    parser.add_argument(
        '--gcp-metrics',
        required=True,
        help='Path to GCP metrics JSON file'
    )

    parser.add_argument(
        '--function-name',
        help='Function name (for single-function mode). If omitted, processes all functions in metrics file.'
    )

    parser.add_argument(
        '--carbon-intensity',
        type=float,
        required=True,
        help='Average carbon intensity in gCO2/kWh (e.g., 400)'
    )

    parser.add_argument(
        '--output',
        help='Output JSON file path. If omitted, auto-generates path in evaluation/data/'
    )

    parser.add_argument(
        '--include-annual',
        action='store_true',
        default=True,
        help='Include per-year metrics (default: True)'
    )

    args = parser.parse_args()

    # Load configuration
    print("Loading configuration files...")
    static_config = load_static_config()

    # Load function metadata for annual calculations (if enabled)
    function_metadata = None
    if args.include_annual:
        try:
            function_metadata = load_function_metadata()
            print("Loaded function metadata for per-year calculations")
        except Exception as e:
            print(f"Warning: Could not load function metadata: {e}")
            print("Per-year metrics will not be included")

    # Load GCP metrics
    print(f"Loading GCP metrics from {args.gcp_metrics}...")
    gcp_data = load_gcp_metrics(args.gcp_metrics)

    # Determine mode (single function vs batch)
    results = []

    if args.function_name:
        # Single function mode
        print(f"Processing function: {args.function_name}")

        # Find function in GCP metrics
        if 'function' in gcp_data:
            # CLI mode output (single function)
            function_metrics = gcp_data['function']
            result = calculate_metrics_for_function(
                function_name=args.function_name,
                gcp_metrics=function_metrics,
                carbon_intensity_g_per_kwh=args.carbon_intensity,
                static_config=static_config,
                function_metadata=function_metadata
            )
            results.append(result)
        elif 'functions' in gcp_data:
            # Config mode output (multiple functions)
            if args.function_name in gcp_data['functions']:
                function_metrics = gcp_data['functions'][args.function_name]
                result = calculate_metrics_for_function(
                    function_name=args.function_name,
                    gcp_metrics=function_metrics,
                    carbon_intensity_g_per_kwh=args.carbon_intensity,
                    static_config=static_config,
                    function_metadata=function_metadata
                )
                results.append(result)
            else:
                print(f"Error: Function '{args.function_name}' not found in GCP metrics file")
                sys.exit(1)
        else:
            print("Error: Unrecognized GCP metrics file format")
            sys.exit(1)

    else:
        # Batch mode - process all functions
        print("Processing all functions in batch mode...")

        if 'functions' in gcp_data:
            for func_name, function_metrics in gcp_data['functions'].items():
                print(f"  Processing: {func_name}")
                result = calculate_metrics_for_function(
                    function_name=func_name,
                    gcp_metrics=function_metrics,
                    carbon_intensity_g_per_kwh=args.carbon_intensity,
                    static_config=static_config,
                    function_metadata=function_metadata
                )
                results.append(result)
        elif 'function' in gcp_data:
            # Single function in CLI mode without --function-name specified
            function_metrics = gcp_data['function']
            func_name = function_metrics.get('service_name', 'unknown')
            print(f"  Processing: {func_name}")
            result = calculate_metrics_for_function(
                function_name=func_name,
                gcp_metrics=function_metrics,
                carbon_intensity_g_per_kwh=args.carbon_intensity,
                static_config=static_config,
                function_metadata=function_metadata
            )
            results.append(result)
        else:
            print("Error: Unrecognized GCP metrics file format")
            sys.exit(1)

    # Prepare output
    basis = 'per_invocation_and_per_year' if function_metadata else 'per_invocation'
    output_data = {
        'calculation_metadata': {
            'source_gcp_metrics': args.gcp_metrics,
            'static_config_version': static_config.get('config_version', 'unknown'),
            'calculation_timestamp': datetime.now(timezone.utc).isoformat(),
            'basis': basis
        },
        'functions': results
    }

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Auto-generate output path
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        data_dir = os.path.join(project_root, 'evaluation', 'data')
        os.makedirs(data_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        if args.function_name:
            filename = f"final_metrics_{args.function_name}_{timestamp}.json"
        else:
            # Use experiment name from GCP metrics if available
            exp_name = gcp_data.get('experiment_name', 'batch')
            filename = f"final_metrics_{exp_name}_{timestamp}.json"

        output_path = os.path.join(data_dir, filename)

    # Write output
    print(f"\nWriting results to {output_path}...")
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"[OK] Complete! Processed {len(results)} function(s)")
    print(f"  Output: {output_path}")


if __name__ == '__main__':
    main()
