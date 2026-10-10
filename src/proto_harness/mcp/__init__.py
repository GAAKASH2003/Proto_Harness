"""Model Context Protocol (MCP) support for Proto Harness."""

from proto_harness.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    find_mcp_config_file,
    load_mcp_config,
)
from proto_harness.mcp.manager import (
    MCPClient,
    MCPManager,
    MCPToolInfo,
)

__all__ = [
    "MCPClient",
    "MCPConfig",
    "MCPManager",
    "MCPServerConfig",
    "MCPToolInfo",
    "find_mcp_config_file",
    "load_mcp_config",
]
