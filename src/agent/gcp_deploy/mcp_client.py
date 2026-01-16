"""
MCP Client wrapper for communicating with the MCP Function Deployer server.

This client provides a simple interface for the agent to:
- Deploy functions to Cloud Functions
- Invoke deployed functions
- Check function status
- Delete functions
"""

import os
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

# Default MCP server URL (override with environment variable)
DEFAULT_MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "http://localhost:8080"
)


class MCPClient:
    """Client for communicating with the MCP Function Deployer server."""

    def __init__(self, server_url: str = None, api_key: str = None):
        """
        Initialize the MCP client.

        Args:
            server_url: URL of the MCP server (default: from MCP_SERVER_URL env var)
            api_key: API key for authentication (default: from MCP_API_KEY env var)
        """
        self.server_url = server_url or DEFAULT_MCP_SERVER_URL
        self.api_key = api_key or os.environ.get("MCP_API_KEY", "")

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """
        Call an MCP tool via HTTP.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            dict with the tool result or error
        """
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.server_url}/mcp",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600)  # 10 min timeout for deployments
                ) as response:
                    result = await response.json()

                    if response.status == 401:
                        logger.error("MCP server authentication failed")
                        return {"error": "Authentication failed", "success": False}

                    if "error" in result:
                        logger.error(f"MCP tool error: {result['error']}")
                        return {"error": result["error"], "success": False}

                    return result.get("result", {})

        except aiohttp.ClientError as e:
            logger.error(f"MCP client error: {e}")
            return {"error": str(e), "success": False}

    async def deploy_function(
        self,
        function_name: str,
        code: str,
        region: str,
        runtime: str = "python312",
        memory_mb: int = 256,
        cpu: str = None,
        timeout_seconds: int = 60,
        entry_point: str = "main",
        requirements: str = ""
    ) -> dict:
        """
        Deploy Python code as a Cloud Function.

        Args:
            function_name: Unique identifier for the function
            code: Raw Python code to deploy
            region: GCP region (e.g., "us-east1")
            runtime: Python runtime version
            memory_mb: Memory allocation in MB
            cpu: Number of vCPUs as string (e.g., "1", "2", "4"). If None, GCP calculates from memory.
            timeout_seconds: Function timeout
            entry_point: Function entry point name
            requirements: Optional requirements.txt content

        Returns:
            dict with success, function_url, status, etc.
        """
        args = {
            "function_name": function_name,
            "code": code,
            "region": region,
            "runtime": runtime,
            "memory_mb": memory_mb,
            "timeout_seconds": timeout_seconds,
            "entry_point": entry_point,
            "requirements": requirements
        }
        if cpu is not None:
            args["cpu"] = str(cpu)
        return await self.call_tool("deploy_function", args)

    async def invoke_function(
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
            dict with success, response, execution_time_ms, status_code
        """
        return await self.call_tool("invoke_function", {
            "function_url": function_url,
            "payload": payload or {},
            "timeout_seconds": timeout_seconds
        })

    async def get_function_status(
        self,
        function_name: str,
        region: str
    ) -> dict:
        """
        Check the deployment status of a function.

        Args:
            function_name: Name of the function
            region: GCP region

        Returns:
            dict with exists, status, function_url, last_updated
        """
        return await self.call_tool("get_function_status", {
            "function_name": function_name,
            "region": region
        })

    async def delete_function(
        self,
        function_name: str,
        region: str
    ) -> dict:
        """
        Delete a deployed function.

        Args:
            function_name: Name of the function
            region: GCP region

        Returns:
            dict with success status
        """
        return await self.call_tool("delete_function", {
            "function_name": function_name,
            "region": region
        })

    async def generate_function_name(self) -> str:
        """
        Generate a unique function name.

        Returns:
            A unique function name string
        """
        result = await self.call_tool("generate_function_name", {})
        return result.get("function_name", "")

    async def list_tools(self) -> dict:
        """
        List available tools on the MCP server.

        Returns:
            dict with list of available tools
        """
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.server_url}/mcp",
                    headers=headers,
                    json=payload
                ) as response:
                    result = await response.json()
                    return result.get("result", {})
        except Exception as e:
            logger.error(f"Error listing tools: {e}")
            return {"error": str(e)}

    async def health_check(self) -> dict:
        """
        Check the health of the MCP server.

        Returns:
            dict with health status
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.server_url}/health",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        return {"status": "unhealthy", "code": response.status}
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {"status": "unreachable", "error": str(e)}


# Synchronous wrapper for use in non-async contexts
class MCPClientSync:
    """Synchronous wrapper for MCPClient."""

    def __init__(self, server_url: str = None, api_key: str = None):
        self.async_client = MCPClient(server_url, api_key)

    def _run_async(self, coro):
        """Run an async coroutine synchronously."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        return loop.run_until_complete(coro)

    def deploy_function(self, **kwargs) -> dict:
        return self._run_async(self.async_client.deploy_function(**kwargs))

    def invoke_function(self, **kwargs) -> dict:
        return self._run_async(self.async_client.invoke_function(**kwargs))

    def get_function_status(self, **kwargs) -> dict:
        return self._run_async(self.async_client.get_function_status(**kwargs))

    def delete_function(self, **kwargs) -> dict:
        return self._run_async(self.async_client.delete_function(**kwargs))

    def generate_function_name(self) -> str:
        return self._run_async(self.async_client.generate_function_name())

    def health_check(self) -> dict:
        return self._run_async(self.async_client.health_check())
