#!/usr/bin/env python3
"""
Final Metrics Calculator for GPS-UP Evaluation

Calculates per-invocation energy consumption, carbon emissions, and transfer costs
based on GCP metrics and static configuration.

Output is saved to evaluation/results/{project_id}/ for organization by GCP project.

Usage:
  # Single function mode
  python calculate.py \
    --gcp-metrics evaluation/results/my-project/gcp_metrics_dispatcher_20260111_130943.json \
    --function-name dispatcher \
    --carbon-intensity 400

  # Batch mode (processes all functions in GCP metrics file)
  python calculate.py \
    --gcp-metrics evaluation/results/my-project/gcp_metrics_project-a_20260111_130943.json \
    --carbon-intensity 400
    # Output: final_metrics_all_functions_{timestamp}.json
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

# =============================================================================
# Special function configurations (not in function_metadata.json)
# =============================================================================
# Dispatcher: Routes requests to appropriate functions
DISPATCHER_CONFIG = {
    'allocated_memory_mb': 512,
    'allocated_vcpus': 0.333,
    'gpu_required': False,
}

# Agent: Runs once daily to generate carbon-aware schedule
AGENT_CONFIG = {
    'allocated_memory_mb': 1024,  # 1 GB
    'allocated_vcpus': 1.0,
    'gpu_required': False,
}

# =============================================================================
# Agent API Overhead Constants (from AGENT_API_OVERHEAD.md)
# =============================================================================
# APIs called weekly (52×/year) because inputs stable, forecasts constant over week
# Values are for Gemini API only - Electricity Maps API overhead is negligible (~0.0001 kWh/request)
AGENT_API_OVERHEAD = {
    'per_api_call': {
        'energy_kwh': 0.010,        # 10 Wh = 0.010 kWh (Gemini inference only)
        'emissions_g': 1.0,         # 1.0 gCO2 (100 gCO2/kWh × 0.010 kWh)
        'cost_usd': 0.0054,         # Gemini 1.5 Flash pricing
    },
    'per_year': {
        'api_calls': 52,            # Weekly calls
        'energy_kwh': 0.52,         # 0.010 × 52
        'emissions_g': 52.0,        # 1.0 × 52
        'emissions_kg': 0.052,      # 52g = 0.052 kg
        'cost_usd': 0.28,           # 0.0054 × 52
    },
    'source': 'evaluation/AGENT_API_OVERHEAD.md',
    'note': 'Gemini API only; Electricity Maps overhead negligible'
}


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
    1. Special functions (dispatcher, agent) - use constants at top of file
    2. function_metadata.json (if function_name matches)
    3. Fallback to defaults

    Returns:
        {
            'allocated_memory_mb': int,
            'allocated_vcpus': float,
            'gpu_required': bool
        }
    """
    # Check for special functions first
    if function_name == 'dispatcher':
        return DISPATCHER_CONFIG.copy()
    elif function_name == 'agent':
        return AGENT_CONFIG.copy()

    # Look up in function_metadata.json
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

    Energy Methodology (Cloud Carbon Footprint standard):
    - CPU: Min/Max model using actual utilization from GCP (0.71-4.26 W/vCPU)
    - Memory: Allocation-based (full allocated capacity, "always on" assumption)
    - GPU: Min/Max model (same as CPU, using assumed 50% utilization since GCP doesn't expose GPU metrics)
    - Network: Transfer volume-based (0.001 kWh/GB)

    Memory Utilization Note:
    memory_utilization_actual is captured from GCP metrics but NOT used in the
    power calculation. Memory power is calculated based on allocated capacity
    following standard cloud carbon footprint methodology. This provides:
    - Conservative estimates
    - Consistency with industry standards
    - Predictable results for Greenup/Powerup/Speedup/Costup comparisons

    References:
    - Cloud Carbon Footprint: https://www.cloudcarbonfootprint.org/docs/methodology/
    - Abdulsalam et al. (2015): "Using the Greenup, Powerup, and Speedup metrics
      to evaluate software energy efficiency." IEEE IGSC 2015.

    Args:
        allocated_vcpus: Number of vCPUs allocated to the function
        allocated_memory_mb: Memory allocation in MB
        runtime_ms: Runtime per invocation (billable_instance_time / request_count)
        cpu_utilization_actual: Actual CPU utilization from GCP (0.0-1.0)
        data_received_gb: Total data received (GB)
        data_sent_gb: Total data sent (GB)
        request_count: Total number of requests
        gpu_required: Whether function uses GPU
        static_config: Loaded static_config.json dict

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

    CPU_MIN_WATTS_PER_VCPU = power_constants['cpu_min_watts_per_vcpu']
    CPU_MAX_WATTS_PER_VCPU = power_constants['cpu_max_watts_per_vcpu']
    MEMORY_WATTS_PER_GIB = power_constants['memory_watts_per_gib']
    DATACENTER_PUE = power_constants['datacenter_pue']
    NETWORK_KWH_PER_GB = power_constants['network_kwh_per_gb']

    # GPU constants (load from static_config.json if GPU required)
    # GPU uses same CCF min/max model as CPU
    # GCP doesn't expose GPU utilization, so we assume 50% for compute workloads
    GPU_UTILIZATION_ASSUMED = 0.5
    if gpu_required:
        GPU_MIN_WATTS = power_constants['gpu_min_watts']['nvidia-l4']
        GPU_MAX_WATTS = power_constants['gpu_max_watts']['nvidia-l4']
        GPU_COUNT = static_config['agent_defaults']['gpu_count']
    else:
        GPU_MIN_WATTS = 0
        GPU_MAX_WATTS = 0
        GPU_COUNT = 0

    # Convert units
    allocated_memory_gib = allocated_memory_mb / 1024
    runtime_s = runtime_ms / 1000

    # ============================================================================
    # Power Consumption Calculation
    # ============================================================================
    # Based on industry-standard Cloud Carbon Footprint methodology:
    # https://www.cloudcarbonfootprint.org/docs/methodology/
    #
    # CPU POWER (utilization-based, dynamic):
    #   CCF Min/Max Model: cpu_power_w = vcpus × (min_watts + cpu_util × (max_watts - min_watts))
    #   GCP values: min=0.71W, max=4.26W per vCPU (from SPECPower database)
    #   Uses ACTUAL measured CPU utilization from GCP Cloud Monitoring metrics.
    #   Rationale: CPUs have dynamic power states (DVFS) - power scales with load.
    #   At 50% utilization: 0.71 + 0.5 × 3.55 = 2.485 W/vCPU
    #
    # MEMORY POWER (allocation-based, static):
    #   Formula: memory_power_w = memory_gib × 0.4W/GiB
    #   Uses FULL allocated memory capacity regardless of utilization.
    #   Rationale: DRAM refresh power is largely independent of access patterns.
    #   memory_utilization_actual is captured from GCP but NOT used in calculation.
    #
    #   Industry standard (Cloud Carbon Footprint): ~0.392 W/GB from manufacturer
    #   specs (Crucial: ~0.375 W/GB, Micron: ~0.4083 W/GB). The methodology states:
    #   "allocated bytes rather than utilized bytes, because this is a more accurate
    #   reflection of the energy needed to support that usage. Even if the full
    #   memory isn't used, it still consumes power."
    #
    # Why the asymmetry?
    #   - Hardware characteristics: CPUs have power states, DRAM does not
    #   - Standardization: Allocation-based memory is standard in cloud research
    #   - Evaluation methodology: For Greenup/Powerup/Speedup/Costup comparisons
    #     with same configurations, methodology consistency matters more than
    #     absolute accuracy
    #   - Conservative estimates: Provides upper bound on memory contribution
    #
    # For research requiring utilization-based memory, modify memory_power_w to:
    #   memory_power_w = allocated_memory_gib * MEMORY_WATTS_PER_GIB * memory_utilization_actual
    # ============================================================================

    # CCF min/max formula: vcpus × (min + util × (max - min))
    cpu_power_w = allocated_vcpus * (CPU_MIN_WATTS_PER_VCPU + cpu_utilization_actual * (CPU_MAX_WATTS_PER_VCPU - CPU_MIN_WATTS_PER_VCPU))
    memory_power_w = allocated_memory_gib * MEMORY_WATTS_PER_GIB  # Allocation-based

    # GPU power (only if GPU required) - CCF min/max model
    if gpu_required:
        gpu_power_w = GPU_COUNT * (GPU_MIN_WATTS + GPU_UTILIZATION_ASSUMED * (GPU_MAX_WATTS - GPU_MIN_WATTS))
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
    Scale per-invocation metrics to annual totals.

    Scaling logic:
    - Energy, emissions, transfer costs: × annual_invocations (per_day × 365)
    - Latency: NOT scaled - uses mean from measurements

    Note: API/agent overhead is NOT included here - it's added at project aggregation level only.

    Args:
        per_invocation_metrics: Output from calculate_metrics_for_function()
        function_name: Function name (unused, kept for compatibility)
        function_metadata: Loaded function_metadata.json (unused, kept for compatibility)

    Returns:
        Complete per-year metrics dict
    """
    # Get invocations_per_day from inputs (already calculated in calculate_metrics_for_function)
    invocations_per_day = per_invocation_metrics['inputs']['invocations_per_day']
    annual_invocations = invocations_per_day * 365

    # Get per_invocation values
    per_inv_energy = per_invocation_metrics['per_invocation']['energy']
    per_inv_emissions = per_invocation_metrics['per_invocation']['emissions']
    per_inv_transfer = per_invocation_metrics['per_invocation']['cost_overhead']['transfer_cost_usd']

    # Scale compute energy (per_invocation × annual_invocations)
    annual_compute_energy_kwh = per_inv_energy['compute_energy_kwh'] * annual_invocations
    annual_network_energy_kwh = per_inv_energy['network_energy_kwh'] * annual_invocations

    # Scale compute emissions (per_invocation × annual_invocations, convert g to kg)
    annual_compute_emissions_kg = (per_inv_emissions['compute_emissions_g'] * annual_invocations) / 1000

    # Scale transfer costs
    annual_transfer_cost = per_inv_transfer * annual_invocations

    # Total = compute + network (no API at function level)
    annual_total_energy_kwh = annual_compute_energy_kwh + annual_network_energy_kwh
    annual_total_emissions_kg = annual_compute_emissions_kg

    return {
        'latency': {
            'mean_ms': None,
            'note': 'Mean latency from load testing; not scaled yearly'
        },
        'annual_invocations': annual_invocations,
        'energy': {
            'compute_energy_kwh': annual_compute_energy_kwh,
            'network_energy_kwh': annual_network_energy_kwh,
            'total_energy_kwh': annual_total_energy_kwh
        },
        'emissions': {
            'compute_emissions_kg': annual_compute_emissions_kg,
            'total_carbon_kg': annual_total_emissions_kg
        },
        'cost_overhead': {
            'annual_transfer_cost_usd': annual_transfer_cost,
            'total_annual_cost_overhead_usd': annual_transfer_cost
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
    request_count = gcp_metrics['gcp_metrics']['request_count']
    billable_instance_time_s = gcp_metrics['gcp_metrics'].get('billable_instance_time_s')

    # Runtime calculation: use billable_instance_time / request_count
    # GCP allocates CPU during billable time, which includes container startup.
    # It is likely (though not explicitly documented) that CPU utilization is
    # measured over this same period. Using billable_time ensures consistency
    # between runtime and utilization in the energy formula.
    # Reference: https://docs.cloud.google.com/run/docs/configuring/billing-settings
    # Note: Even if this assumption is imperfect, using the same approach across
    # all scenarios ensures results remain comparable.
    runtime_ms = (billable_instance_time_s / request_count) * 1000

    from_gcp = {
        'region': gcp_metrics['region'],
        'request_count': request_count,
        'runtime_ms': runtime_ms,
        'cpu_utilization_actual': gcp_metrics['gcp_metrics']['cpu_utilization']['mean'],
        'memory_utilization_actual': gcp_metrics['gcp_metrics']['memory_utilization']['mean'],
        'billable_instance_time_s': billable_instance_time_s,
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

    Args:
        function_name: Name of the function
        gcp_metrics: GCP metrics dict for this function
        carbon_intensity_g_per_kwh: Carbon intensity for emissions calculation
        static_config: Loaded static_config.json
        function_metadata: Loaded function_metadata.json (for per_year calculations)

    Returns:
        {
            'function_name': str,
            'inputs': {...},
            'per_invocation': {
                'energy': {...},
                'emissions': {...},
                'cost_overhead': {...}
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

    # 5. Get invocations_per_day for inputs section
    if function_name == 'dispatcher':
        invocations_per_day = sum(
            f['invocations_per_day']
            for f in function_metadata['functions'].values()
            if f['function_id'] != 'dispatcher'
        ) if function_metadata else 100
    elif function_name == 'agent':
        invocations_per_day = 1
    elif function_metadata and function_name in function_metadata['functions']:
        invocations_per_day = function_metadata['functions'][function_name]['invocations_per_day']
    else:
        invocations_per_day = 100

    # 6. Get region-specific transfer rate
    data_transfer_cost_per_gb_usd = static_config['regions'].get(
        config['region'], {}
    ).get('data_transfer_cost_per_gb_usd', 0.02)  # Default to $0.02

    # 7. Build per-invocation result
    result = {
        'function_name': function_name,
        'region': config['region'],
        'inputs': {
            'allocated_memory_mb': config['allocated_memory_mb'],
            'allocated_vcpus': config['allocated_vcpus'],
            'gpu_required': config.get('gpu_required', False),
            'invocations_per_day': invocations_per_day,
            'data_transfer_cost_per_gb_usd': data_transfer_cost_per_gb_usd,
            'carbon_intensity_g_per_kwh': carbon_intensity_g_per_kwh,
            'from_gcp_metrics': {
                'request_count': config['request_count'],
                'runtime_ms_mean': config['runtime_ms'],
                'cpu_utilization_mean': config['cpu_utilization_actual'],
                'memory_utilization_mean': config['memory_utilization_actual'],
                'billable_instance_time_s': config['billable_instance_time_s'],
                'data_received_gb': config['data_received_gb'],
                'data_sent_gb': config['data_sent_gb']
            },
            'latency': {
                'mean_latency_ms': None,
                'note': 'Placeholder - to be measured by loadgen tool'
            }
        },
        'per_invocation': {
            'latency': {
                'mean_ms': None,
                'note': 'Placeholder - to be measured by loadgen tool'
            },
            'energy': {
                'compute_energy_kwh': energy['compute_energy_kwh'],
                'network_energy_kwh': energy['network_energy_kwh'],
                'total_energy_kwh': energy['total_energy_kwh'],
                'breakdown': energy['breakdown']
            },
            'emissions': {
                'compute_emissions_g': emissions_g,
                'total_carbon_g': emissions_g
            },
            'cost_overhead': {
                'transfer_cost_usd': transfer_costs['transfer_cost_usd'],
                'total_cost_overhead_usd': transfer_costs['transfer_cost_usd'],
                'transfer_breakdown': transfer_costs['breakdown']
            }
        }
    }

    # 8. Calculate per-year metrics (if function_metadata provided)
    if function_metadata:
        result['per_year'] = calculate_per_year_metrics(
            per_invocation_metrics=result,
            function_name=function_name,
            function_metadata=function_metadata
        )

    return result


def build_calculation_constants(static_config: Dict) -> Dict:
    """
    Extract calculation constants from static_config for output.

    Returns a dict with power constants and API overhead constants.
    """
    power_constants = static_config['power_constants']

    return {
        'power': {
            'cpu_min_watts_per_vcpu': power_constants['cpu_min_watts_per_vcpu'],
            'cpu_max_watts_per_vcpu': power_constants['cpu_max_watts_per_vcpu'],
            'cpu_formula': 'vcpus × (min_watts + cpu_utilization × (max_watts - min_watts))',
            'memory_watts_per_gib': power_constants['memory_watts_per_gib'],
            'datacenter_pue': power_constants['datacenter_pue'],
            'network_kwh_per_gb': power_constants['network_kwh_per_gb'],
            'gpu_min_watts_nvidia_l4': power_constants['gpu_min_watts']['nvidia-l4'],
            'gpu_max_watts_nvidia_l4': power_constants['gpu_max_watts']['nvidia-l4'],
            'gpu_formula': 'gpu_count × (min_watts + gpu_utilization × (max_watts - min_watts))',
            'gpu_utilization_assumed': 0.5
        },
        'agent_api_overhead': {
            'per_api_call': AGENT_API_OVERHEAD['per_api_call'],
            'per_year': AGENT_API_OVERHEAD['per_year'],
            'source': AGENT_API_OVERHEAD['source'],
            'note': 'API overhead applies only to agent function in Agent approach projects'
        }
    }


def calculate_project_aggregation(function_results: list, project_id: str = None) -> Dict:
    """
    Aggregate per-year metrics across all functions in the project.

    Aggregation rules:
    - Energy, emissions, costs: SUM across all functions
    - Latency: MEAN across all functions (when available)
    - API/agent overhead: Added only at this level for Agent approach projects

    Args:
        function_results: List of function result dicts from calculate_metrics_for_function()
        project_id: GCP project ID (used to detect Agent approach from name)

    Returns:
        Aggregated metrics dict for the entire project
    """
    # Check if any function has per_year metrics
    functions_with_yearly = [f for f in function_results if 'per_year' in f]

    if not functions_with_yearly:
        return None

    # Detect if this is an Agent approach project
    is_agent_approach = project_id is not None and 'agent' in project_id.lower()

    # Initialize aggregation
    total_compute_energy_kwh = 0.0
    total_network_energy_kwh = 0.0
    total_compute_emissions_kg = 0.0
    total_transfer_cost_usd = 0.0

    # Collect latencies for mean calculation (only non-null values)
    latencies = []

    for func in functions_with_yearly:
        per_year = func['per_year']

        # Sum energy
        energy = per_year.get('energy', {})
        total_compute_energy_kwh += energy.get('compute_energy_kwh', 0) or 0
        total_network_energy_kwh += energy.get('network_energy_kwh', 0) or 0

        # Sum emissions
        emissions = per_year.get('emissions', {})
        total_compute_emissions_kg += emissions.get('compute_emissions_kg', 0) or 0

        # Sum costs
        cost_overhead = per_year.get('cost_overhead', {})
        total_transfer_cost_usd += cost_overhead.get('annual_transfer_cost_usd', 0) or 0

        # Collect latency (if available)
        latency = per_year.get('latency', {})
        if latency.get('mean_ms') is not None:
            latencies.append(latency['mean_ms'])

    # Calculate mean latency
    mean_latency_ms = None
    if latencies:
        mean_latency_ms = sum(latencies) / len(latencies)

    # API/agent overhead (only for Agent approach projects)
    if is_agent_approach:
        api_overhead_energy_kwh = AGENT_API_OVERHEAD['per_year']['energy_kwh']
        api_overhead_emissions_kg = AGENT_API_OVERHEAD['per_year']['emissions_kg']
        api_overhead_cost_usd = AGENT_API_OVERHEAD['per_year']['cost_usd']
    else:
        api_overhead_energy_kwh = 0.0
        api_overhead_emissions_kg = 0.0
        api_overhead_cost_usd = 0.0

    # Calculate totals including API overhead
    total_energy_kwh = total_compute_energy_kwh + total_network_energy_kwh + api_overhead_energy_kwh
    total_emissions_kg = total_compute_emissions_kg + api_overhead_emissions_kg
    total_cost_overhead_usd = total_transfer_cost_usd + api_overhead_cost_usd

    return {
        'description': 'Aggregated yearly metrics across all functions in this project',
        'function_count': len(functions_with_yearly),
        'is_agent_approach': is_agent_approach,
        'latency': {
            'mean_ms': mean_latency_ms,
            'note': 'Mean of per-function latencies (null if no latency data available)'
        },
        'energy': {
            'compute_energy_kwh': total_compute_energy_kwh,
            'network_energy_kwh': total_network_energy_kwh,
            'api_overhead_kwh': api_overhead_energy_kwh,
            'total_energy_kwh': total_energy_kwh
        },
        'emissions': {
            'compute_emissions_kg': total_compute_emissions_kg,
            'api_overhead_kg': api_overhead_emissions_kg,
            'total_carbon_kg': total_emissions_kg
        },
        'cost_overhead': {
            'annual_transfer_cost_usd': total_transfer_cost_usd,
            'agent_overhead': {
                'api_overhead_usd': api_overhead_cost_usd,
                'execution_cost_overhead_usd': None,
                'note': 'Placeholder for execution_cost_overhead until defined'
            },
            'total_annual_cost_overhead_usd': total_cost_overhead_usd
        }
    }


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
        help='Output JSON file path. If omitted, auto-generates path in evaluation/results/{project_id}/'
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

    # Extract project_id from GCP metrics (needed for folder organization and API overhead detection)
    project_id = gcp_data.get('project_id', 'unknown_project')

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

    # Calculate project-wide aggregation (only for yearly metrics)
    # API/agent overhead is added here based on project_id
    project_aggregation = calculate_project_aggregation(results, project_id=project_id)

    # Build calculation constants for output
    calculation_constants = build_calculation_constants(static_config)

    # Prepare output
    basis = 'per_invocation_and_per_year' if function_metadata else 'per_invocation'
    output_data = {
        'calculation_metadata': {
            'source_gcp_metrics': args.gcp_metrics,
            'project_id': project_id,
            'static_config_version': static_config.get('config_version', 'unknown'),
            'calculation_timestamp': datetime.now(timezone.utc).isoformat(),
            'basis': basis
        },
        'calculation_constants': calculation_constants,
        'functions': results
    }

    # Add project aggregation if available
    if project_aggregation:
        output_data['project_aggregation'] = project_aggregation

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        # Auto-generate output path in evaluation/results/{project_id}/
        script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.dirname(os.path.dirname(script_dir))
        results_dir = os.path.join(project_root, 'evaluation', 'results', project_id)
        os.makedirs(results_dir, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        if args.function_name:
            filename = f"final_metrics_{project_id}_{args.function_name}_{timestamp}.json"
        elif 'functions' in gcp_data and len(gcp_data['functions']) > 1:
            # Batch mode with multiple functions
            filename = f"final_metrics_{project_id}_all_functions_{timestamp}.json"
        else:
            # Use experiment name from GCP metrics if available
            exp_name = gcp_data.get('experiment_name', 'batch')
            filename = f"final_metrics_{project_id}_{exp_name}_{timestamp}.json"

        output_path = os.path.join(results_dir, filename)

    # Write output
    print(f"\nWriting results to {output_path}...")
    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print(f"[OK] Complete! Processed {len(results)} function(s)")
    print(f"  Output: {output_path}")


if __name__ == '__main__':
    main()
