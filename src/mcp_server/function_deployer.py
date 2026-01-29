"""
Cloud Run API wrapper for deploying and managing Python functions.

This module deploys user code as Cloud Run services using Cloud Build
for container image creation, providing access to all Cloud Run regions.
"""

import os
import io
import tarfile
import uuid
import time
import logging
import aiohttp
from datetime import datetime
from google.cloud import storage
from google.cloud.devtools import cloudbuild_v1
from google.cloud import run_v2
from google.cloud import artifactregistry_v1
from google.iam.v1 import iam_policy_pb2, policy_pb2
from google.api_core import exceptions as gcp_exceptions
from google.protobuf import duration_pb2
import google.auth.transport.requests
import google.oauth2.id_token

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("PROJECT_ID", "iosl-faas-scheduling")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "faas-scheduling-us-east1")
ARTIFACT_REPO = os.environ.get("ARTIFACT_REPO", "function-images")
DEFAULT_RUNTIME = "python312"
DEFAULT_TIMEOUT = 60
DEFAULT_ENTRY_POINT = "main"

# Dockerfile template for user functions
DOCKERFILE_TEMPLATE = """FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "-m", "gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "300", "main:app"]
"""


class FunctionDeployer:
    """Handles deployment and management of Cloud Run services."""

    def __init__(self, project_id: str = None, gcs_bucket: str = None,
                 artifact_repo: str = None, mock_mode: bool = None):
        self.project_id = project_id or PROJECT_ID
        self.gcs_bucket = gcs_bucket or GCS_BUCKET
        self.artifact_repo = artifact_repo or ARTIFACT_REPO

        if mock_mode is None:
            mock_mode = os.environ.get("MCP_MOCK_MODE", "false").lower() == "true"
        self.mock_mode = mock_mode

        if self.mock_mode:
            logger.info("Running in MOCK mode")
            self.storage_client = None
            self.build_client = None
            self.run_client = None
            self.artifact_client = None
        else:
            try:
                self.storage_client = storage.Client()
                self.build_client = cloudbuild_v1.CloudBuildClient()
                self.run_client = run_v2.ServicesClient()
                self.artifact_client = artifactregistry_v1.ArtifactRegistryClient()
            except Exception as e:
                logger.warning(f"Could not initialize GCP clients: {e}. Falling back to MOCK mode")
                self.mock_mode = True
                self.storage_client = None
                self.build_client = None
                self.run_client = None
                self.artifact_client = None

    def generate_function_name(self) -> str:
        """Generate a unique function name using UUID."""
        short_uuid = str(uuid.uuid4())[:8]
        return f"user-func-{short_uuid}"

    def _sanitize_service_name(self, name: str) -> str:
        """
        Sanitize a name to comply with Cloud Run service naming rules:
        - Only lowercase letters, digits, and hyphens
        - Must begin with a letter
        - Cannot end with a hyphen
        - Must be less than 50 characters
        """
        import re
        # Convert to lowercase
        sanitized = name.lower()
        # Replace underscores and other invalid chars with hyphens
        sanitized = re.sub(r'[^a-z0-9-]', '-', sanitized)
        # Replace multiple consecutive hyphens with single hyphen
        sanitized = re.sub(r'-+', '-', sanitized)
        # Ensure it starts with a letter
        if sanitized and not sanitized[0].isalpha():
            sanitized = 'fn-' + sanitized
        # Remove trailing hyphens
        sanitized = sanitized.rstrip('-')
        # Truncate to 49 characters (leaving room for potential prefix)
        if len(sanitized) > 49:
            sanitized = sanitized[:49].rstrip('-')
        # If empty after sanitization, generate a new name
        if not sanitized:
            sanitized = self.generate_function_name()
        return sanitized

    def _get_artifact_registry_region(self, region: str) -> str:
        """
        Get the appropriate Artifact Registry region.
        Artifact Registry may not be available in all regions, so we map to nearby regions.
        """
        # Multi-regional repositories for common cases
        region_mapping = {
            # US regions -> us (multi-regional)
            "us-central1": "us",
            "us-east1": "us",
            "us-east4": "us",
            "us-east5": "us",
            "us-south1": "us",
            "us-west1": "us",
            "us-west2": "us",
            "us-west3": "us",
            "us-west4": "us",
            # Europe regions -> europe (multi-regional)
            "europe-west1": "europe",
            "europe-west2": "europe",
            "europe-west3": "europe",
            "europe-west4": "europe",
            "europe-west6": "europe",
            "europe-west8": "europe",
            "europe-west9": "europe",
            "europe-west10": "europe",
            "europe-west12": "europe",
            "europe-north1": "europe",
            "europe-central2": "europe",
            "europe-southwest1": "europe",
            # Asia regions -> asia (multi-regional)
            "asia-east1": "asia",
            "asia-east2": "asia",
            "asia-northeast1": "asia",
            "asia-northeast2": "asia",
            "asia-northeast3": "asia",
            "asia-south1": "asia",
            "asia-south2": "asia",
            "asia-southeast1": "asia",
            "asia-southeast2": "asia",
            # Australia
            "australia-southeast1": "australia-southeast1",
            "australia-southeast2": "australia-southeast2",
            # Middle East
            "me-central1": "me-central1",
            "me-central2": "me-central2",
            "me-west1": "me-west1",
            # South America
            "southamerica-east1": "southamerica-east1",
            "southamerica-west1": "southamerica-west1",
            # North America (non-US)
            "northamerica-northeast1": "northamerica-northeast1",
            "northamerica-northeast2": "northamerica-northeast2",
        }
        return region_mapping.get(region, "us")

    def _create_source_archive(self, code: str, requirements: str = "",
                                entry_point: str = DEFAULT_ENTRY_POINT) -> bytes:
        """
        Create a tar.gz archive with Flask app code, requirements, and Dockerfile.

        The archive contains:
        - main.py: Flask app wrapping user code
        - requirements.txt: Dependencies
        - Dockerfile: Container build instructions
        """
        import re
        tar_buffer = io.BytesIO()

        # Sanitize entry_point to be a valid Python identifier
        entry_point = self._sanitize_entry_point(entry_point)

        with tarfile.open(fileobj=tar_buffer, mode='w:gz') as tf:
            # Extract __future__ imports from user code - they must be at the top
            future_imports = []
            remaining_code = []
            for line in code.split('\n'):
                if re.match(r'^\s*from\s+__future__\s+import\s+', line):
                    future_imports.append(line)
                else:
                    remaining_code.append(line)

            # Remove shebang if present
            clean_code_lines = remaining_code
            if clean_code_lines and clean_code_lines[0].startswith('#!'):
                clean_code_lines = clean_code_lines[1:]

            future_section = '\n'.join(future_imports) + '\n' if future_imports else ''
            clean_code = '\n'.join(clean_code_lines)

            # Create Flask app wrapper for the user's code
            wrapped_code = f'''{future_section}"""Auto-generated Cloud Run service wrapper."""
from flask import Flask, request, jsonify
import traceback

app = Flask(__name__)

# User's code
{clean_code}

# Store reference to user's handler
_user_handler = None
if 'handler' in dir():
    _user_handler = handler
elif 'main' in dir():
    _user_handler = main
elif 'run' in dir():
    _user_handler = run

@app.route('/', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
def {entry_point}():
    """HTTP Cloud Run entry point."""
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
        return jsonify({{"error": str(e), "traceback": traceback.format_exc()}}), 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({{"status": "healthy"}})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
'''
            # Set common tarinfo attributes
            current_time = time.time()

            # Add main.py
            main_info = tarfile.TarInfo(name="main.py")
            main_bytes = wrapped_code.encode('utf-8')
            main_info.size = len(main_bytes)
            main_info.mtime = current_time
            main_info.mode = 0o644
            tf.addfile(main_info, io.BytesIO(main_bytes))

            # Create requirements.txt
            base_requirements = "flask>=3.0.0\ngunicorn>=21.2.0\n"
            if requirements:
                # Ensure requirements string ends with newline for proper concatenation
                req_str = requirements.strip()
                if req_str:
                    full_requirements = base_requirements + req_str + "\n"
                else:
                    full_requirements = base_requirements
            else:
                full_requirements = base_requirements

            req_info = tarfile.TarInfo(name="requirements.txt")
            req_bytes = full_requirements.encode('utf-8')
            req_info.size = len(req_bytes)
            req_info.mtime = current_time
            req_info.mode = 0o644
            tf.addfile(req_info, io.BytesIO(req_bytes))

            # Add Dockerfile
            dockerfile_info = tarfile.TarInfo(name="Dockerfile")
            dockerfile_bytes = DOCKERFILE_TEMPLATE.encode('utf-8')
            dockerfile_info.size = len(dockerfile_bytes)
            dockerfile_info.mtime = current_time
            dockerfile_info.mode = 0o644
            tf.addfile(dockerfile_info, io.BytesIO(dockerfile_bytes))

        tar_buffer.seek(0)
        return tar_buffer.read()

    def _upload_to_gcs(self, archive_content: bytes, function_name: str) -> str:
        """Upload the source archive to GCS and return the GCS URI."""
        bucket = self.storage_client.bucket(self.gcs_bucket)
        blob_name = f"function-source/{function_name}/{function_name}.tar.gz"
        blob = bucket.blob(blob_name)

        blob.upload_from_string(archive_content, content_type="application/gzip")

        gcs_uri = f"gs://{self.gcs_bucket}/{blob_name}"
        logger.info(f"Uploaded function source to {gcs_uri}")
        return gcs_uri

    def _build_container_image(self, function_name: str, region: str) -> str:
        """
        Use Cloud Build to build a container image from source.

        Args:
            function_name: Name of the function
            region: Target deployment region (used to select Artifact Registry location)

        Returns:
            The container image URI
        """
        ar_region = self._get_artifact_registry_region(region)
        image_uri = f"{ar_region}-docker.pkg.dev/{self.project_id}/{self.artifact_repo}/{function_name}:latest"

        # Create Cloud Build configuration
        build = cloudbuild_v1.Build(
            source=cloudbuild_v1.Source(
                storage_source=cloudbuild_v1.StorageSource(
                    bucket=self.gcs_bucket,
                    object_=f"function-source/{function_name}/{function_name}.tar.gz"
                )
            ),
            steps=[
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/docker",
                    args=["build", "-t", image_uri, "."]
                ),
                cloudbuild_v1.BuildStep(
                    name="gcr.io/cloud-builders/docker",
                    args=["push", image_uri]
                )
            ],
            images=[image_uri],
            timeout=duration_pb2.Duration(seconds=600)
        )

        logger.info(f"Starting Cloud Build for {function_name}...")
        operation = self.build_client.create_build(
            project_id=self.project_id,
            build=build
        )

        # Wait for build to complete
        result = operation.result(timeout=600)

        if result.status != cloudbuild_v1.Build.Status.SUCCESS:
            raise Exception(f"Cloud Build failed with status: {result.status.name}")

        logger.info(f"Container image built: {image_uri}")
        return image_uri

    def _set_public_invoker(self, function_name: str, region: str) -> bool:
        """
        Set IAM policy to allow unauthenticated access to the Cloud Run service.

        Args:
            function_name: Name of the deployed service
            region: GCP region where the service is deployed

        Returns:
            True if successful, False otherwise
        """
        try:
            service_name = f"projects/{self.project_id}/locations/{region}/services/{function_name}"

            # Get current IAM policy
            request = iam_policy_pb2.GetIamPolicyRequest(resource=service_name)
            policy = self.run_client.get_iam_policy(request=request)

            # Check if allUsers already has run.invoker role
            invoker_binding = None
            for binding in policy.bindings:
                if binding.role == "roles/run.invoker":
                    invoker_binding = binding
                    break

            # Add allUsers to the invoker role
            if invoker_binding is None:
                invoker_binding = policy_pb2.Binding()
                invoker_binding.role = "roles/run.invoker"
                invoker_binding.members.append("allUsers")
                policy.bindings.append(invoker_binding)
            elif "allUsers" not in invoker_binding.members:
                invoker_binding.members.append("allUsers")
            else:
                logger.info(f"Service {function_name} already has public access")
                return True

            # Set the updated policy
            set_request = iam_policy_pb2.SetIamPolicyRequest(
                resource=service_name,
                policy=policy
            )
            self.run_client.set_iam_policy(request=set_request)
            logger.info(f"Set public invoker access for {function_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to set public invoker access: {e}")
            return False

    def _sanitize_entry_point(self, entry_point: str) -> str:
        """
        Sanitize entry point to be a valid Python identifier.
        """
        import re
        # Replace invalid chars with underscores
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', entry_point)
        # Ensure it starts with a letter or underscore
        if sanitized and sanitized[0].isdigit():
            sanitized = '_' + sanitized
        # Default to 'main' if empty
        if not sanitized:
            sanitized = 'main'
        return sanitized

    def _ensure_artifact_registry_repo(self, region: str) -> None:
        """
        Ensure the Artifact Registry repository exists for storing container images.
        Creates it if it doesn't exist.
        """
        ar_region = self._get_artifact_registry_region(region)
        repo_name = f"projects/{self.project_id}/locations/{ar_region}/repositories/{self.artifact_repo}"

        try:
            self.artifact_client.get_repository(name=repo_name)
            logger.info(f"Artifact Registry repository exists: {repo_name}")
        except gcp_exceptions.NotFound:
            logger.info(f"Creating Artifact Registry repository: {repo_name}")
            parent = f"projects/{self.project_id}/locations/{ar_region}"
            # Don't set 'name' in constructor - it's computed from parent + repository_id
            repository = artifactregistry_v1.Repository(
                format_=artifactregistry_v1.Repository.Format.DOCKER,
                description="Container images for dynamically deployed functions"
            )
            operation = self.artifact_client.create_repository(
                parent=parent,
                repository=repository,
                repository_id=self.artifact_repo
            )
            operation.result(timeout=120)
            logger.info(f"Created Artifact Registry repository: {repo_name}")

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
        Deploy Python code as a Cloud Run service.

        Args:
            function_name: Unique identifier for the service
            code: Raw Python code to deploy
            region: GCP region (e.g., "us-east1", "europe-north1")
            runtime: Python runtime version (ignored, uses python:3.12-slim)
            memory_mb: Memory allocation in MB (default: 256)
            cpu: Number of vCPUs as string (e.g., "1", "2", "4"). If None, defaults to "1".
            timeout_seconds: Request timeout (default: 60)
            entry_point: Function entry point name (default: "main")
            requirements: Optional requirements.txt content

        Returns:
            dict with success status, service URL, and deployment info
        """
        # Sanitize function name to comply with Cloud Run naming rules
        original_name = function_name
        function_name = self._sanitize_service_name(function_name)
        if original_name != function_name:
            logger.info(f"Sanitized function name: '{original_name}' -> '{function_name}'")

        if self.mock_mode:
            logger.info(f"[MOCK] Would deploy {function_name} to {region}")
            mock_url = f"https://{function_name}-{self.project_id[:8]}.{region}.run.app"
            return {
                "success": True,
                "function_url": mock_url,
                "function_name": function_name,
                "region": region,
                "status": "ACTIVE",
                "gcs_source": f"gs://{self.gcs_bucket}/function-source/{function_name}/{function_name}.tar.gz",
                "mock": True
            }

        try:
            logger.info(f"Starting deployment of {function_name} to {region}")

            # Step 1: Create and upload source archive
            archive_content = self._create_source_archive(code, requirements, entry_point)
            gcs_uri = self._upload_to_gcs(archive_content, function_name)

            # Step 2: Ensure Artifact Registry repository exists
            self._ensure_artifact_registry_repo(region)

            # Step 3: Build container image with Cloud Build
            image_uri = self._build_container_image(function_name, region)

            # Step 4: Deploy to Cloud Run
            parent = f"projects/{self.project_id}/locations/{region}"
            service_name = f"{parent}/services/{function_name}"

            # Configure resources
            memory_str = f"{memory_mb}Mi"
            cpu_str = cpu if cpu is not None else "1"

            # Create the Cloud Run service configuration
            container = run_v2.Container(
                image=image_uri,
                ports=[run_v2.ContainerPort(container_port=8080)],
                resources=run_v2.ResourceRequirements(
                    limits={"memory": memory_str, "cpu": cpu_str}
                ),
            )

            revision_template = run_v2.RevisionTemplate(
                containers=[container],
                timeout=duration_pb2.Duration(seconds=timeout_seconds),
                max_instance_request_concurrency=80,
            )

            # Check if service exists and update or create
            try:
                existing = self.run_client.get_service(name=service_name)
                logger.info(f"Updating existing service {function_name}")
                # For updates, include the full service name
                service = run_v2.Service(
                    name=service_name,
                    template=revision_template,
                    ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_ALL,
                )
                operation = self.run_client.update_service(service=service)
            except gcp_exceptions.NotFound:
                logger.info(f"Creating new service {function_name}")
                # For creates, service.name must be empty - name is passed via service_id
                service = run_v2.Service(
                    template=revision_template,
                    ingress=run_v2.IngressTraffic.INGRESS_TRAFFIC_ALL,
                )
                operation = self.run_client.create_service(
                    parent=parent,
                    service=service,
                    service_id=function_name
                )

            logger.info("Waiting for deployment to complete...")
            result = operation.result(timeout=300)
            service_url = result.uri

            logger.info(f"Service deployed successfully: {service_url}")

            # Step 5: Set public invoker access
            public_access_set = self._set_public_invoker(function_name, region)
            if not public_access_set:
                logger.warning(f"Service deployed but public access could not be set")

            return {
                "success": True,
                "function_url": service_url,
                "function_name": function_name,
                "region": region,
                "status": "ACTIVE",
                "gcs_source": gcs_uri,
                "image_uri": image_uri,
                "public_access": public_access_set
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
        Invoke a deployed Cloud Run service.

        Args:
            function_url: HTTPS URL of the service
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
            # Try unauthenticated first (if public access is set)
            logger.info(f"Invoking {function_url}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    function_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds)
                ) as response:
                    execution_time_ms = int((time.time() - start_time) * 1000)

                    try:
                        response_data = await response.json()
                    except:
                        response_data = await response.text()

                    if response.status == 200:
                        return {
                            "success": True,
                            "response": response_data,
                            "execution_time_ms": execution_time_ms,
                            "status_code": response.status
                        }

                    # If unauthorized, try with ID token
                    if response.status == 403:
                        logger.info("Retrying with authentication...")
                        auth_req = google.auth.transport.requests.Request()
                        id_token = google.oauth2.id_token.fetch_id_token(auth_req, function_url)
                        headers = {"Authorization": f"Bearer {id_token}"}

                        async with session.post(
                            function_url,
                            json=payload,
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=timeout_seconds)
                        ) as auth_response:
                            execution_time_ms = int((time.time() - start_time) * 1000)
                            try:
                                response_data = await auth_response.json()
                            except:
                                response_data = await auth_response.text()

                            return {
                                "success": auth_response.status == 200,
                                "response": response_data,
                                "execution_time_ms": execution_time_ms,
                                "status_code": auth_response.status
                            }

                    return {
                        "success": False,
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
        Check the deployment status of a Cloud Run service.

        Args:
            function_name: Name of the service
            region: GCP region

        Returns:
            dict with exists flag, status, URL, and last update time
        """
        # Sanitize function name to match deployed service name
        function_name = self._sanitize_service_name(function_name)

        if self.mock_mode:
            logger.info(f"[MOCK] Would check status of {function_name} in {region}")
            mock_url = f"https://{function_name}-{self.project_id[:8]}.{region}.run.app"
            return {
                "exists": True,
                "status": "ACTIVE",
                "function_url": mock_url,
                "last_updated": datetime.now().isoformat(),
                "mock": True
            }

        try:
            service_name = f"projects/{self.project_id}/locations/{region}/services/{function_name}"
            service = self.run_client.get_service(name=service_name)

            # Map Cloud Run conditions to status
            status = "UNKNOWN"
            for condition in service.conditions:
                if condition.type_ == "Ready":
                    if condition.state == run_v2.Condition.State.CONDITION_SUCCEEDED:
                        status = "ACTIVE"
                    elif condition.state == run_v2.Condition.State.CONDITION_RECONCILING:
                        status = "DEPLOYING"
                    elif condition.state == run_v2.Condition.State.CONDITION_FAILED:
                        status = "FAILED"
                    break

            return {
                "exists": True,
                "status": status,
                "function_url": service.uri,
                "last_updated": service.update_time.isoformat() if service.update_time else None
            }

        except gcp_exceptions.NotFound:
            return {
                "exists": False,
                "status": "NOT_FOUND",
                "function_url": None,
                "last_updated": None
            }
        except Exception as e:
            logger.error(f"Error getting service status: {e}")
            return {
                "exists": False,
                "status": "ERROR",
                "error": str(e),
                "function_url": None,
                "last_updated": None
            }

    async def delete(self, function_name: str, region: str) -> dict:
        """
        Delete a deployed Cloud Run service.

        Args:
            function_name: Name of the service
            region: GCP region

        Returns:
            dict with success status
        """
        # Sanitize function name to match deployed service name
        function_name = self._sanitize_service_name(function_name)

        if self.mock_mode:
            logger.info(f"[MOCK] Would delete {function_name} in {region}")
            return {
                "success": True,
                "function_name": function_name,
                "region": region,
                "mock": True
            }

        try:
            service_name = f"projects/{self.project_id}/locations/{region}/services/{function_name}"

            operation = self.run_client.delete_service(name=service_name)
            operation.result(timeout=120)

            # Clean up GCS source
            try:
                bucket = self.storage_client.bucket(self.gcs_bucket)
                blob = bucket.blob(f"function-source/{function_name}/{function_name}.tar.gz")
                blob.delete()
            except Exception as e:
                logger.warning(f"Could not delete GCS source: {e}")

            # Note: We don't delete the container image to allow for rollbacks
            # and because Artifact Registry has lifecycle policies

            logger.info(f"Service {function_name} deleted successfully")

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
                "note": "Service was already deleted"
            }
        except Exception as e:
            logger.error(f"Error deleting service: {e}")
            return {
                "success": False,
                "error": str(e),
                "function_name": function_name,
                "region": region
            }
