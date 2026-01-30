"""
MCP Server for Cloud Run service deployment and management.

This server exposes tools for:
- Deploying user-provided Python code as Cloud Run services
- Invoking deployed services
- Checking service status
- Deleting services

The server uses Flask and runs with gunicorn for Cloud Run deployment.
"""

import os
import sys
import logging
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
logger = logging.getLogger("mcp-function-deployer")

from function_deployer import FunctionDeployer

PORT = int(os.environ.get("PORT", 8080))
API_KEY = os.environ.get("MCP_API_KEY", "")
deployer = FunctionDeployer()

async def deploy_function(
    function_name: str,
    region: str,
    code: str,
    runtime: str = "python312",
    memory_mb: int = 256,
    cpu: str = None,
    timeout_seconds: int = 360,
    entry_point: str = "main",
    requirements: str = ""
) -> dict:
    """
    Deploy Python code as a Cloud Run service.

    Args:
        function_name: Unique identifier for the service (e.g., "user-func-a1b2c3d4")
        region: GCP region to deploy to (e.g., "us-east1", "europe-west1", "europe-north1")
        code: Raw Python code to deploy. Should contain a handler/main/run function.
        runtime: Python runtime version (ignored, uses python:3.12-slim container)
        memory_mb: Memory allocation in MB (default: 256, max: 32768)
        cpu: Number of vCPUs as string (e.g., "1", "2", "4", "8"). Defaults to "1".
        timeout_seconds: Request timeout in seconds (default: 360, max: 3600)
        entry_point: Function entry point name (default: "main")
        requirements: Optional requirements.txt content for additional dependencies

    Returns:
        dict with:
            - success: bool indicating if deployment succeeded
            - function_url: HTTPS URL to invoke the service
            - function_name: The deployed service name
            - region: The deployment region
            - status: ACTIVE, DEPLOYING, or FAILED
            - image_uri: The container image URI (if successful)
    """
    logger.info(f"deploy_function called: {function_name} -> {region}")

    if not code:
        return {
            "success": False,
            "error": "'code' parameter is required",
            "function_name": function_name,
            "region": region,
            "status": "FAILED"
        }

    result = await deployer.deploy(
        function_name=function_name,
        code=code,
        region=region,
        runtime=runtime,
        memory_mb=memory_mb,
        cpu=cpu,
        timeout_seconds=timeout_seconds,
        entry_point=entry_point,
        requirements=requirements
    )

    logger.info(f"deploy_function result: success={result.get('success')}")
    return result


async def invoke_function(
    function_url: str,
    payload: dict = None,
    timeout_seconds: int = 300
) -> dict:
    """
    Invoke a deployed Cloud Run service.

    Args:
        function_url: The HTTPS URL of the deployed service
        payload: JSON payload to send to the service (default: empty dict)
        timeout_seconds: Request timeout in seconds (default: 300)

    Returns:
        dict with:
            - success: bool indicating if invocation succeeded
            - response: The service's response data
            - execution_time_ms: Execution time in milliseconds
            - status_code: HTTP status code from the service
    """
    logger.info(f"invoke_function called: {function_url}")

    if payload is None:
        payload = {}

    result = await deployer.invoke(
        function_url=function_url,
        payload=payload,
        timeout_seconds=timeout_seconds
    )

    logger.info(f"invoke_function result: success={result.get('success')}, time={result.get('execution_time_ms')}ms")
    return result


async def get_function_status(
    function_name: str,
    region: str
) -> dict:
    """
    Check the deployment status of a Cloud Run service.

    Args:
        function_name: Name of the service to check
        region: GCP region where the service is deployed

    Returns:
        dict with:
            - exists: bool indicating if service exists
            - status: ACTIVE, DEPLOYING, FAILED, or NOT_FOUND
            - function_url: HTTPS URL if service is active
            - last_updated: ISO timestamp of last update
    """
    logger.info(f"get_function_status called: {function_name} in {region}")

    result = await deployer.get_status(
        function_name=function_name,
        region=region
    )

    logger.info(f"get_function_status result: exists={result.get('exists')}, status={result.get('status')}")
    return result


async def delete_function(
    function_name: str,
    region: str
) -> dict:
    """
    Delete a deployed Cloud Run service.

    Args:
        function_name: Name of the service to delete
        region: GCP region where the service is deployed

    Returns:
        dict with:
            - success: bool indicating if deletion succeeded
            - function_name: The deleted service name
            - region: The region
    """
    logger.info(f"delete_function called: {function_name} in {region}")

    result = await deployer.delete(
        function_name=function_name,
        region=region
    )

    logger.info(f"delete_function result: success={result.get('success')}")
    return result


def generate_function_name() -> dict:
    """
    Generate a unique function name using UUID.

    Returns:
        dict with:
            - function_name: A unique function name (e.g., "user-func-a1b2c3d4")
    """
    name = deployer.generate_function_name()
    logger.info(f"generate_function_name: {name}")
    return {"function_name": name}


def get_server_config() -> dict:
    """Get MCP server configuration."""
    return {
        "name": "function-deployer",
        "version": "2.0.0",
        "backend": "cloud-run",
        "project_id": deployer.project_id,
        "gcs_bucket": deployer.gcs_bucket,
        "artifact_repo": deployer.artifact_repo,
        "available_tools": [
            "deploy_function",
            "invoke_function",
            "get_function_status",
            "delete_function",
            "generate_function_name"
        ]
    }


def create_http_app():
    """Create a Flask app with MCP endpoints and optional API key authentication."""
    from flask import Flask, request, jsonify
    import asyncio

    app = Flask(__name__)

    def verify_api_key():
        if not API_KEY:
            return True
        return request.headers.get("X-API-Key", "") == API_KEY

    @app.before_request
    def check_auth():
        if request.path == "/health":
            return None
        if not verify_api_key():
            return jsonify({"error": "Unauthorized", "message": "Invalid or missing API key"}), 401

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "healthy",
            "service": "mcp-function-deployer",
            "backend": "cloud-run",
            "project_id": deployer.project_id
        })

    @app.route("/mcp", methods=["POST"])
    def mcp_endpoint():
        """Handle MCP JSON-RPC requests."""
        try:
            data = request.get_json()
            if not data:
                return jsonify({"error": "Invalid request", "message": "No JSON body"}), 400

            method = data.get("method", "")
            params = data.get("params", {})
            request_id = data.get("id", 1)

            if method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                tool_map = {
                    "deploy_function": deploy_function,
                    "invoke_function": invoke_function,
                    "get_function_status": get_function_status,
                    "delete_function": delete_function,
                    "generate_function_name": generate_function_name
                }

                if tool_name not in tool_map:
                    return jsonify({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
                    }), 400

                tool_func = tool_map[tool_name]
                if asyncio.iscoroutinefunction(tool_func):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        result = loop.run_until_complete(tool_func(**arguments))
                    finally:
                        loop.close()
                else:
                    result = tool_func(**arguments)

                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result
                })

            elif method == "tools/list":
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "tools": [
                            {
                                "name": "deploy_function",
                                "description": "Deploy Python code as a Cloud Run service"
                            },
                            {
                                "name": "invoke_function",
                                "description": "Invoke a deployed Cloud Run service"
                            },
                            {
                                "name": "get_function_status",
                                "description": "Check the deployment status of a service"
                            },
                            {
                                "name": "delete_function",
                                "description": "Delete a deployed Cloud Run service"
                            },
                            {
                                "name": "generate_function_name",
                                "description": "Generate a unique function name"
                            }
                        ]
                    }
                })

            else:
                return jsonify({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Unknown method: {method}"}
                }), 400

        except Exception as e:
            logger.error(f"Error handling MCP request: {e}", exc_info=True)
            return jsonify({
                "jsonrpc": "2.0",
                "id": data.get("id", 1) if data else 1,
                "error": {"code": -32603, "message": str(e)}
            }), 500

    return app


app = create_http_app()

if __name__ == "__main__":
    logger.info(f"Starting MCP Function Deployer server on port {PORT}")
    logger.info(f"Backend: Cloud Run")
    logger.info(f"Project ID: {deployer.project_id}")
    logger.info(f"GCS Bucket: {deployer.gcs_bucket}")
    logger.info(f"Artifact Registry: {deployer.artifact_repo}")
    logger.info(f"API Key configured: {'Yes' if API_KEY else 'No'}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
