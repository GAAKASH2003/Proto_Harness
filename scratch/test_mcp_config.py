"""Unit test for MCP config schema and discovery."""
import json
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proto_harness.mcp.config import (
    MCPConfig,
    MCPServerConfig,
    find_mcp_config_file,
    load_mcp_config,
)


def test_mcp_config_parsing():
    # 1. Parse stdio server config
    stdio_cfg = MCPServerConfig(
        command="uvx",
        args=["mcp-server-sqlite", "--db-path", "test.db"],
        read_only_tools=["read_query"],
    )
    assert stdio_cfg.transport == "stdio"
    assert stdio_cfg.command == "uvx"
    assert stdio_cfg.read_only_tools == ["read_query"]
    assert stdio_cfg.enabled is True

    # 2. Parse SSE server config (auto-inferred transport)
    sse_cfg = MCPServerConfig(
        url="http://localhost:8000/sse",
    )
    assert sse_cfg.transport == "sse"

    # 3. Overall MCPConfig dict mapping
    config = MCPConfig(
        mcp_servers={
            "sqlite": stdio_cfg,
            "remote": sse_cfg,
        }
    )
    assert len(config.mcp_servers) == 2
    assert "sqlite" in config.mcp_servers
    assert "remote" in config.mcp_servers

    # 4. JSON loading & discovery
    temp_dir = Path(__file__).resolve().parent / "_temp_mcp_test"
    temp_dir.mkdir(exist_ok=True)
    cfg_file = temp_dir / ".mcp.json"
    cfg_file.write_text(
        json.dumps({
            "mcpServers": {
                "mock": {
                    "command": "python",
                    "args": ["-m", "mock_server"],
                    "read_only_tools": ["query"]
                }
            }
        }),
        encoding="utf-8"
    )

    try:
        found_file = find_mcp_config_file(temp_dir)
        assert found_file == cfg_file

        loaded = load_mcp_config(temp_dir)
        assert "mock" in loaded.mcp_servers
        assert loaded.mcp_servers["mock"].command == "python"
        assert loaded.mcp_servers["mock"].read_only_tools == ["query"]
    finally:
        if cfg_file.exists():
            cfg_file.unlink()
        if temp_dir.exists():
            temp_dir.rmdir()

    print("[OK] All MCP config tests passed!")


if __name__ == "__main__":
    test_mcp_config_parsing()
