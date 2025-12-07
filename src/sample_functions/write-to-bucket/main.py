import os
import json
import uuid
import datetime as dt

from flask import Request
from google.cloud import storage

# Create the client once; Cloud Functions will reuse it across invocations.
storage_client = storage.Client()

# Environment variables come from Terraform
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
REGION = os.environ.get("REGION", "unknown")


def write_to_bucket(request: Request):
    """
    HTTP entry point for the Cloud Function (Gen2).

    - Accepts an optional JSON payload.
    - Writes a JSON object into a shared GCS bucket under a "collection" prefix.
    - Returns a simple text response.
    """

    # Try to read JSON input; it's optional
    try:
        payload_in = request.get_json(silent=True) or {}
    except Exception:
        payload_in = {}

    now = dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    run_id = str(uuid.uuid4())

    # Create a "collection" prefix to prove a new path per invocation
    # Example: runs/2025-01-01T12-00-00Z-europe-west1/run-<uuid>/result.json
    safe_timestamp = now.replace(":", "-")
    collection_prefix = f"runs/{safe_timestamp}-{REGION}/run-{run_id}"
    object_name = f"{collection_prefix}/result.json"

    bucket = storage_client.bucket(OUTPUT_BUCKET)
    blob = bucket.blob(object_name)

    data = {
        "timestamp_utc": now,
        "region": REGION,
        "run_id": run_id,
        "input_payload": payload_in,
    }

    blob.upload_from_string(
        data=json.dumps(data, indent=2),
        content_type="application/json",
    )

    return (
        f"Wrote object to gs://{OUTPUT_BUCKET}/{object_name}\n",
        200,
        {"Content-Type": "text/plain"},
    )
