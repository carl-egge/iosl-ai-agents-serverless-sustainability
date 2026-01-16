"""
Cloud Functions v2 API wrapper for deploying and managing Python functions.
"""

import os
import io
import zipfile
import uuid
import time
import logging
import aiohttp
from datetime import datetime
from google.cloud import storage
from google.cloud import functions_v2
from google.api_core import exceptions as gcp_exceptions
import google.auth.transport.requests
import google.oauth2.id_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "iosl-faas-scheduling")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "faas-scheduling-us-east1")
DEFAULT_RUNTIME = "python312"
DEFAULT_TIMEOUT = 60
DEFAULT_ENTRY_POINT = "main"


class FunctionDeployer:
    """Handles deployment and management of Cloud Functions."""

    def __init__(self, project_id: str = None, gcs_bucket: str = None, mock_mode: bool = None):
        self.project_id = project_id or PROJECT_ID
        self.gcs_bucket = gcs_bucket or GCS_BUCKET

        if mock_mode is None:
            mock_mode = os.environ.get("MCP_MOCK_MODE", "false").lower() == "true"
        self.mock_mode = mock_mode

        if self.mock_mode:
            logger.info("Running in MOCK mode")
            self.storage_client = None
            self.functions_client = None
        else:
            try:
                self.storage_client = storage.Client()
                self.functions_client = functions_v2.FunctionServiceClient()
            except Exception as e:
                logger.warning(f"Could not initialize GCP clients: {e}. Falling back to MOCK mode")
                self.mock_mode = True
                self.storage_client = None
                self.functions_client = None

    def generate_function_name(self) -> str:
        """Generate a unique function name using UUID."""
        short_uuid = str(uuid.uuid4())[:8]
        return f"user-func-{short_uuid}"

    def _create_function_zip(self, code: str, requirements: str = "", entry_point: str = DEFAULT_ENTRY_POINT) -> bytes:
        """Create a zip archive with wrapped function code and requirements."""
        import re
        zip_buffer = io.BytesIO()

        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Extract from __future__ imports from user code - they must be at the top
            future_imports = []
            remaining_code = []
            for line in code.split('\n'):
                if re.match(r'^\s*from\s+__future__\s+import\s+', line):
                    future_imports.append(line)
                else:
                    remaining_code.append(line)
            
            # Remove shebang and initial docstring from remaining code if present
            clean_code_lines = remaining_code
            if clean_code_lines and clean_code_lines[0].startswith('#!'):
                clean_code_lines = clean_code_lines[1:]
            
            future_section = '\n'.join(future_imports) + '\n' if future_imports else ''
            clean_code = '\n'.join(clean_code_lines)
            
            wrapped_code = f'''{future_section}\"\"\"Auto-generated Cloud Function wrapper.\"\"\"
import functions_framework
from flask import jsonify

{clean_code}

# Store reference to user's handler before defining wrapper
_user_handler = None
if 'handler' in dir():
    _user_handler = handler
elif 'main' in dir():
    _user_handler = main
elif 'run' in dir():
    _user_handler = run

@functions_framework.http
def {entry_point}(request):
    \"\"\"HTTP Cloud Function entry point.\"\"\"
    try:
        if _user_handler is not None:
            # Call user's handler with the Flask request object
            result = _user_handler(request)
            # If result is a tuple (body, status, headers), return as-is
            if isinstance(result, tuple):
                return result
            # Otherwise jsonify the result
            return jsonify(result) if not isinstance(result, str) else result
        else:
            request_json = request.get_json(silent=True) or {{}}
            return jsonify({{"message": "No handler found", "input": request_json}})
    except Exception as e:
        import traceback
        return jsonify({{"error": str(e), "traceback": traceback.format_exc()}}), 500
'''
            zf.writestr("main.py", wrapped_code)

            base_requirements = "functions-framework==3.*\nflask>=2.0.0\n"
            if requirements:
                full_requirements = base_requirements + requirements
            else:
                full_requirements = base_requirements
            zf.writestr("requirements.txt", full_requirements)

        zip_buffer.seek(0)
        return zip_buffer.read()

    def _upload_to_gcs(self, zip_content: bytes, function_name: str) -> str:
        """Upload the function zip to GCS and return the GCS URI."""
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob_name = f"function-source/{function_name}/{function_name}.zip"
        blob = bucket.blob(blob_name)

        blob.upload_from_string(zip_content, content_type="application/zip")

        gcs_uri = f"gs://{self.gcs_bucket}/{blob_name}"
        logger.info(f"Uploaded function source to {gcs_uri}")
        return gcs_uri

    async def deploy(
        self,
        function_name: str,
        code: str,
        region: str,
        runtime: str = DEFAULT_RUNTIME,
        memory_mb: int = 256,
        cpu: str = None,
        timeout_seconds: int = DEFAULT_TIMEOUT,
        entry_point: str = DEFAULT_ENTRY_POINT,
        requirements: str = ""
    ) -> dict:
        """
        Deploy Python code as a Cloud Function.

        Args:
            function_name: Unique identifier for the function
            code: Raw Python code to deploy
            region: GCP region (e.g., "us-east1")
            runtime: Python runtime version (default: python312)
            memory_mb: Memory allocation in MB (default: 256)
            cpu: Number of vCPUs as string (e.g., "1", "2", "4"). If None, GCP calculates from memory.
            timeout_seconds: Function timeout (default: 60)
            entry_point: Function entry point name (default: "main")
            requirements: Optional requirements.txt content

        Returns:
            dict with success status, function URL, and deployment info
        """
        if self.mock_mode:
            logger.info(f"[MOCK] Would deploy {function_name} to {region}")
            mock_url = f"https://{region}-{self.project_id}.cloudfunctions.net/{function_name}"
            return {
                "success": True,
                "function_url": mock_url,
                "function_name": function_name,
                "region": region,
                "status": "ACTIVE",
                "gcs_source": f"gs://{self.gcs_bucket}/function-source/{function_name}/{function_name}.zip",
                "mock": True
            }

        try:
            logger.info(f"Starting deployment of {function_name} to {region}")

            zip_content = self._create_function_zip(code, requirements, entry_point)
            gcs_uri = self._upload_to_gcs(zip_content, function_name)

            parent = f"projects/{self.project_id}/locations/{region}"
            function_path = f"{parent}/functions/{function_name}"
            memory_str = f"{memory_mb}Mi"

            # Build service config with optional CPU
            service_config_kwargs = {
                "available_memory": memory_str,
                "timeout_seconds": timeout_seconds,
                "ingress_settings": functions_v2.ServiceConfig.IngressSettings.ALLOW_ALL,
                "all_traffic_on_latest_revision": True
            }
            if cpu is not None:
                service_config_kwargs["available_cpu"] = str(cpu)

            function = functions_v2.Function(
                name=function_path,
                build_config=functions_v2.BuildConfig(
                    runtime=runtime,
                    entry_point=entry_point,
                    source=functions_v2.Source(
                        storage_source=functions_v2.StorageSource(
                            bucket=self.gcs_bucket,
                            object_=f"function-source/{function_name}/{function_name}.zip"
                        )
                    )
                ),
                service_config=functions_v2.ServiceConfig(**service_config_kwargs)
            )

            try:
                self.functions_client.get_function(name=function_path)
                logger.info(f"Updating existing function {function_name}")
                operation = self.functions_client.update_function(function=function)
            except gcp_exceptions.NotFound:
                logger.info(f"Creating new function {function_name}")
                operation = self.functions_client.create_function(
                    parent=parent,
                    function=function,
                    function_id=function_name
                )

            logger.info("Waiting for deployment to complete...")
            result = operation.result(timeout=300)
            function_url = result.service_config.uri

            logger.info(f"Function deployed successfully: {function_url}")

            return {
                "success": True,
                "function_url": function_url,
                "function_name": function_name,
                "region": region,
                "status": "ACTIVE",
                "gcs_source": gcs_uri
            }

        except gcp_exceptions.PermissionDenied as e:
            logger.error(f"Permission denied: {e}")
            return {
                "success": False,
                "error": f"Permission denied: {str(e)}",
                "function_name": function_name,
                "region": region,
                "status": "FAILED"
            }
        except Exception as e:
            logger.error(f"Deployment failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "function_name": function_name,
                "region": region,
                "status": "FAILED"
            }

    async def invoke(
        self,
        function_url: str,
        payload: dict = None,
        timeout_seconds: int = 300
    ) -> dict:
        """
        Invoke a deployed Cloud Function.

        Args:
            function_url: HTTPS URL of the function
            payload: JSON payload to send
            timeout_seconds: Request timeout

        Returns:
            dict with response, execution time, and status
        """
        payload = payload or {}
        start_time = time.time()

        if self.mock_mode:
            logger.info(f"[MOCK] Would invoke {function_url}")
            return {
                "success": True,
                "response": {"mock": True, "message": "Mock invocation successful", "input": payload},
                "execution_time_ms": 100,
                "status_code": 200
            }

        try:
            auth_req = google.auth.transport.requests.Request()
            id_token = google.oauth2.id_token.fetch_id_token(auth_req, function_url)
            headers = {"Authorization": f"Bearer {id_token}"}
            logger.info(f"Invoking {function_url}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    function_url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds)
                ) as response:
                    execution_time_ms = int((time.time() - start_time) * 1000)

                    try:
                        response_data = await response.json()
                    except:
                        response_data = await response.text()

                    return {
                        "success": response.status == 200,
                        "response": response_data,
                        "execution_time_ms": execution_time_ms,
                        "status_code": response.status
                    }

        except aiohttp.ClientError as e:
            execution_time_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Invocation failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "execution_time_ms": execution_time_ms,
                "status_code": 0
            }

    async def get_status(self, function_name: str, region: str) -> dict:
        """
        Check the deployment status of a function.

        Args:
            function_name: Name of the function
            region: GCP region

        Returns:
            dict with exists flag, status, URL, and last update time
        """
        if self.mock_mode:
            logger.info(f"[MOCK] Would check status of {function_name} in {region}")
            mock_url = f"https://{region}-{self.project_id}.cloudfunctions.net/{function_name}"
            return {
                "exists": True,
                "status": "ACTIVE",
                "function_url": mock_url,
                "last_updated": datetime.now().isoformat(),
                "mock": True
            }

        try:
            function_path = f"projects/{self.project_id}/locations/{region}/functions/{function_name}"
            function = self.functions_client.get_function(name=function_path)

            state_map = {
                functions_v2.Function.State.ACTIVE: "ACTIVE",
                functions_v2.Function.State.DEPLOYING: "DEPLOYING",
                functions_v2.Function.State.DELETING: "DELETING",
                functions_v2.Function.State.FAILED: "FAILED",
            }
            status = state_map.get(function.state, "UNKNOWN")

            return {
                "exists": True,
                "status": status,
                "function_url": function.service_config.uri if function.service_config else None,
                "last_updated": function.update_time.isoformat() if function.update_time else None
            }

        except gcp_exceptions.NotFound:
            return {
                "exists": False,
                "status": "NOT_FOUND",
                "function_url": None,
                "last_updated": None
            }
        except Exception as e:
            logger.error(f"Error getting function status: {e}")
            return {
                "exists": False,
                "status": "ERROR",
                "error": str(e),
                "function_url": None,
                "last_updated": None
            }

    async def delete(self, function_name: str, region: str) -> dict:
        """
        Delete a deployed function.

        Args:
            function_name: Name of the function
            region: GCP region

        Returns:
            dict with success status
        """
        if self.mock_mode:
            logger.info(f"[MOCK] Would delete {function_name} in {region}")
            return {
                "success": True,
                "function_name": function_name,
                "region": region,
                "mock": True
            }

        try:
            function_path = f"projects/{self.project_id}/locations/{region}/functions/{function_name}"

            operation = self.functions_client.delete_function(name=function_path)
            operation.result(timeout=120)

            try:
                bucket = self.storage_client.bucket(self.gcs_bucket)
                blob = bucket.blob(f"function-source/{function_name}/{function_name}.zip")
                blob.delete()
            except Exception as e:
                logger.warning(f"Could not delete GCS source: {e}")

            logger.info(f"Function {function_name} deleted successfully")

            return {
                "success": True,
                "function_name": function_name,
                "region": region
            }

        except gcp_exceptions.NotFound:
            return {
                "success": True,  # Already deleted
                "function_name": function_name,
                "region": region,
                "note": "Function was already deleted"
            }
        except Exception as e:
            logger.error(f"Error deleting function: {e}")
            return {
                "success": False,
                "error": str(e),
                "function_name": function_name,
                "region": region
            }
