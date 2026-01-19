# Final Metrics Calculator

Calculates energy, emissions, and costs from GCP metrics.

For methodology, formulas, and evaluation context, see [../EVALUATION.md](../EVALUATION.md).

## Prerequisites

- GCP metrics file from `fetch_gcp_metrics.py`
- `local_bucket/function_metadata.json`
- `local_bucket/static_config.json`

## Usage

```bash
python calculate.py \
  --gcp-metrics ../results/{project_id}/gcp_metrics_*.json \
  --carbon-intensity 400
```

Optional:
- `--function-name` - process single function
- `--output` - custom output path

## Output

Files saved to `evaluation/results/{project_id}/`:
- `final_metrics_{project_id}_{name}_{timestamp}.json`

Output includes:
- `calculation_constants` - power constants, API placeholders
- `functions[]` - per-function inputs and calculated metrics
- `project_aggregation` - summed yearly values across all functions
