from __future__ import annotations
import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import sys
from typing import Any
from typing_extensions import Self
from proto_harness.mcp.config import MCPConfig, MCPServerConfig, load_mcp_config

logger = logging.getLogger(__name__)
MCP_PROTOCOL_VERSION = "2024-11-05"

@dataclass(frozen=True)
class MCPToolInfo:
    server_name: str                  # e.g. "sqlite"
    name: str                         # e.g. "query"
    namespaced_name: str              # e.g. "sqlite__query" (prevents name collisions)
    description: str                  # Schema description for the LLM
    input_schema: dict[str, Any]      # JSON Schema for tool parameters
    is_read_only: bool = False        # From config read_only_tools


class MCPClient:
    def __init__(self, server_name: str, config: MCPServerConfig) -> None:
        self.server_name = server_name
        self.config = config
        self.tools: list[MCPToolInfo] = []
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending_requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._closed = False
    
    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None


    async def start(self) -> None:
        """Spawn the server process, complete the handshake, and list tools."""
        if not self.config.command:
            raise ValueError(f"Server '{self.server_name}' has no command specified.")
        # On Windows, resolve .cmd / .exe wrappers (e.g. npx -> npx.cmd, uvx -> uvx.exe)
        cmd_exec = shutil.which(self.config.command) or self.config.command
        cmd_args = [cmd_exec, *self.config.args]
        env = dict(os.environ)
        if self.config.env:
            env.update(self.config.env)
        logger.debug("Starting MCP server '%s': %s", self.server_name, cmd_args)
        try:
            self._process = await asyncio.create_subprocess_exec(
                cmd_args[0],
                *cmd_args[1:],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=16 * 1024 * 1024,
            )
        except Exception as exc:
            logger.error("Failed to spawn MCP server '%s': %s", self.server_name, exc)
            raise

        self._reader_task = asyncio.create_task(self._read_loop())

        # 1. MCP Handshake: initialize
        init_res = await self._send_request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "clientInfo": {"name": "proto-harness", "version": "0.1.0"},
                "capabilities": {},
            },
        )
        logger.debug("MCP server '%s' initialized: %s", self.server_name, init_res.get("serverInfo", {}))
        
        # 2. MCP Handshake: notifications/initialized
        await self._send_notification("notifications/initialized")
        
        # 3. Discover available tools
        await self.refresh_tools()
    

    async def refresh_tools(self) -> list[MCPToolInfo]:
        """Fetch the tools list from the server via tools/list."""
        response = await self._send_request("tools/list", {})
        raw_tools = response.get("tools", [])
        discovered: list[MCPToolInfo] = []
        for t in raw_tools:
            name = t.get("name", "")
            if not name:
                continue
            namespaced = f"{self.server_name}__{name}"
            desc = t.get("description", "") or f"Tool {name} from {self.server_name}"
            schema = t.get("inputSchema", {"type": "object", "properties": {}})
            ro_set = {r.lower() for r in self.config.read_only_tools}
            is_read_only = "*" in self.config.read_only_tools or name.lower() in ro_set
            discovered.append(
                MCPToolInfo(
                    server_name=self.server_name,
                    name=name,
                    namespaced_name=namespaced,
                    description=desc,
                    input_schema=schema,
                    is_read_only=is_read_only,
                )
            )
        self.tools = discovered
        logger.info("MCP server '%s' registered %d tools: %s", self.server_name, len(self.tools), [t.namespaced_name for t in self.tools])
        return self.tools


    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool via tools/call and return the output text."""
        response = await self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )
        content = response.get("content", [])
        is_error = response.get("isError", False)
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        out = "\n".join(parts).strip() if parts else json.dumps(response, default=str)
        if is_error:
            return f"[MCP Error from {self.server_name}]: {out}"
        return out


    async def _send_request(self, method: str, params: dict[str, Any], timeout_s: float = 30.0) -> dict[str, Any]:
        """Send a JSON-RPC request and await matching response."""
        if not self._process or not self._process.stdin:
            raise RuntimeError(f"Server '{self.server_name}' process is not running.")
        req_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future
        msg = json.dumps(payload) + "\n"
        self._process.stdin.write(msg.encode("utf-8"))
        await self._process.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            raise TimeoutError(f"MCP request '{method}' to '{self.server_name}' timed out after {timeout_s}s.")


    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process or not self._process.stdin:
            return
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        msg = json.dumps(payload) + "\n"
        self._process.stdin.write(msg.encode("utf-8"))
        await self._process.stdin.drain()


    async def _read_loop(self) -> None:
        """Background loop reading JSON-RPC responses from stdout."""
        if not self._process or not self._process.stdout:
            return
        while not self._closed:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("[%s stdout raw]: %s", self.server_name, text)
                    continue
                # Match response to pending request future
                req_id = data.get("id")
                if req_id in self._pending_requests:
                    fut = self._pending_requests.pop(req_id)
                    if not fut.done():
                        if "error" in data:
                            fut.set_exception(RuntimeError(f"MCP Error: {data['error']}"))
                        else:
                            fut.set_result(data.get("result", {}))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("[%s reader loop error]: %s", self.server_name, exc)
                break



    async def close(self) -> None:
        """Gracefully terminate the server process."""
        self._closed = True
        # Cancel pending response futures
        for fut in self._pending_requests.values():
            if not fut.done():
                fut.cancel()
        self._pending_requests.clear()
        if self._reader_task:
            self._reader_task.cancel()
            self._reader_task = None
        if self._process:
            try:
                if self._process.stdin:
                    self._process.stdin.close()
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except Exception:
                try:
                    self._process.kill()
                    await asyncio.wait_for(self._process.wait(), timeout=1.0)
                except Exception:
                    pass
            finally:
                self._process = None
            await asyncio.sleep(0.05)
        logger.debug("Closed MCP server '%s'", self.server_name)



class MCPManager:
    """Manages all configured MCP server connections across the session."""
    def __init__(self, config: MCPConfig | None = None, cwd: Path | None = None) -> None:
        self.config = config or load_mcp_config(cwd)
        self._clients: dict[str, MCPClient] = {}


    @property
    def servers_count(self) -> int:
        return len(self._clients)


    async def start_all(self) -> None:
        """Start all enabled MCP servers concurrently."""
        tasks = []
        for name, srv_config in self.config.mcp_servers.items():
            if not srv_config.enabled:
                continue
            client = MCPClient(server_name=name, config=srv_config)
            self._clients[name] = client
            tasks.append(self._start_safe(client))
        if tasks:
            await asyncio.gather(*tasks)


    async def _start_safe(self, client: MCPClient) -> None:
        try:
            await client.start()
        except Exception as exc:
            logger.warning("Could not connect to MCP server '%s': %s", client.server_name, exc)


    def get_all_tools(self) -> list[MCPToolInfo]:
        """Aggregate all tools across all active servers."""
        all_tools: list[MCPToolInfo] = []
        for client in self._clients.values():
            if client.is_running:
                all_tools.extend(client.tools)
        return all_tools


    def find_tool(self, namespaced_name: str) -> tuple[MCPClient, MCPToolInfo] | None:
        """Lookup a tool by its namespaced name (e.g. 'sqlite__query')."""
        if "__" not in namespaced_name:
            return None
        server_name, _ = namespaced_name.split("__", 1)
        client = self._clients.get(server_name)
        if not client or not client.is_running:
            return None
        for t in client.tools:
            if t.namespaced_name == namespaced_name:
                return client, t
        return None


    async def call_tool(self, namespaced_name: str, arguments: dict[str, Any]) -> str:
        """Dispatch a tool call to the matching MCP server."""
        found = self.find_tool(namespaced_name)
        if not found:
            raise ValueError(f"MCP tool '{namespaced_name}' is not available or server is offline.")
        client, tool_info = found
        return await client.call_tool(tool_info.name, arguments)


    async def close_all(self) -> None:
        """Cleanly close all active server processes."""
        close_tasks = [client.close() for client in self._clients.values()]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        self._clients.clear()

    async def __aenter__(self) -> Self:
        await self.start_all()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()

