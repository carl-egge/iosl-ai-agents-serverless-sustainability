#!/usr/bin/env python3
"""
Configuration loader for GCP deployment - simplified version.
This version doesn't try to load static_config.json from local filesystem.
The main gcp_agent.py loads it from GCS instead.
"""

from typing import Dict, Any


def get_region_info(region_code: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get region information including data transfer costs.

    Args:
        region_code: GCP region code (e.g., 'us-east1', 'europe-west1')
        config: Pre-loaded config dict

    Returns:
        Dictionary with region info
    """
    regions = config.get("regions", {})
    return regions.get(region_code, {})


def calculate_transfer_cost(
    region_code: str,
    data_input_gb: float,
    data_output_gb: float,
    source_location: str = None,
    config: Dict[str, Any] = None
) -> float:
    """
    Calculate data transfer cost for a region.

    Args:
        region_code: Target execution region
        data_input_gb: Amount of input data in GB
        data_output_gb: Amount of output data in GB
        source_location: Source data location (if same as target, cost is 0)
        config: Pre-loaded config dict

    Returns:
        Total transfer cost in USD
    """
    if config is None:
        return 0.0

    # If executing in same region as data source, no transfer cost
    if source_location and region_code == source_location:
        return 0.0

    region_info = get_region_info(region_code, config)
    cost_per_gb = region_info.get("data_transfer_cost_per_gb_usd", 0.0)

    total_data_gb = data_input_gb + data_output_gb
    return total_data_gb * cost_per_gb


def format_region_costs_for_llm(
    regions: Dict[str, Any],
    data_input_gb: float,
    data_output_gb: float,
    source_location: str = None,
    config: Dict[str, Any] = None
) -> str:
    """
    Format region cost information for LLM prompt.

    Args:
        regions: Dictionary of region codes to their data
        data_input_gb: Amount of input data in GB
        data_output_gb: Amount of output data in GB
        source_location: Source data location
        config: Pre-loaded config dict

    Returns:
        Formatted string with cost information
    """
    if config is None:
        return ""

    total_data_gb = data_input_gb + data_output_gb

    formatted = (
        f"\nData Transfer Costs:\n"
        f"- Total data volume: {total_data_gb:.2f} GB "
        f"({data_input_gb:.2f} GB input + {data_output_gb:.2f} GB output)\n"
    )

    if source_location:
        formatted += f"- Data source location: {source_location}\n"
        formatted += f"- Note: Executing in {source_location} has ZERO transfer cost\n"

    formatted += "\nCost per region for this workload:\n"

    # Group regions by cost
    cost_groups = {}
    for region_code in regions.keys():
        cost = calculate_transfer_cost(
            region_code, data_input_gb, data_output_gb, source_location, config
        )
        region_info = get_region_info(region_code, config)
        region_name = region_info.get("name", region_code)

        if cost not in cost_groups:
            cost_groups[cost] = []
        cost_groups[cost].append(f"{region_code} ({region_name})")

    # Sort by cost and format
    for cost in sorted(cost_groups.keys()):
        regions_list = ", ".join(cost_groups[cost])
        formatted += f"  ${cost:.4f} USD: {regions_list}\n"

    return formatted
