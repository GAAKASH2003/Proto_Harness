"""Unit and integration test for MCPClient and MCPManager."""
import asyncio
import json
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from proto_harness.mcp.config import MCPConfig, MCPServerConfig
from proto_harness.mcp.manager import MCPClient, MCPManager, MCPToolInfo

MOCK_SERVER_CODE = """
import sys
import json

def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        method = req.get("method")
        req_id = req.get("id")

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "mock-calc", "version": "1.0.0"},
                    "capabilities": {"tools": {}}
                }
            }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

        elif method == "notifications/initialized":
            # Notification: do nothing, no response
            pass

        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "add",
                            "description": "Add two numbers",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "number"},
                                    "b": {"type": "number"}
                                },
                                "required": ["a", "b"]
                            }
                        },
                        {
                            "name": "multiply",
                            "description": "Multiply two numbers",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "a": {"type": "number"},
                                    "b": {"type": "number"}
                                }
                            }
                        }
                    ]
                }
            }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name")
            args = params.get("arguments", {})
            if tool_name == "add":
                val = args.get("a", 0) + args.get("b", 0)
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(val)}],
                        "isError": False
                    }
                }
            elif tool_name == "multiply":
                val = args.get("a", 0) * args.get("b", 0)
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(val)}],
                        "isError": False
                    }
                }
            else:
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Unknown tool {tool_name}"}],
                        "isError": True
                    }
                }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
"""

async def run_tests():
    # Write mock server script
    mock_script = Path(__file__).resolve().parent / "mock_mcp_server.py"
    mock_script.write_text(MOCK_SERVER_CODE, encoding="utf-8")

    try:
        # 1. Test MCPClient directly
        srv_cfg = MCPServerConfig(
            command=sys.executable,
            args=[str(mock_script)],
            read_only_tools=["add"],
        )
        client = MCPClient("calculator", srv_cfg)
        await client.start()
        assert client.is_running, "Client should be running"
        assert len(client.tools) == 2, f"Expected 2 tools, got {len(client.tools)}"
        
        # Check tool metadata & namespacing
        add_tool = next(t for t in client.tools if t.name == "add")
        assert add_tool.namespaced_name == "calculator__add"
        assert add_tool.is_read_only is True, "add should be read-only per config"
        assert add_tool.description == "Add two numbers"
        
        mult_tool = next(t for t in client.tools if t.name == "multiply")
        assert mult_tool.namespaced_name == "calculator__multiply"
        assert mult_tool.is_read_only is False

        # Test tool calling
        add_result = await client.call_tool("add", {"a": 15, "b": 27})
        assert add_result == "42", f"Expected '42', got '{add_result}'"

        mult_result = await client.call_tool("multiply", {"a": 6, "b": 7})
        assert mult_result == "42", f"Expected '42', got '{mult_result}'"

        await client.close()
        assert not client.is_running, "Client should be closed"
        print("[OK] Test 1: MCPClient start, discovery, call, and close passed")

        # 2. Test MCPManager
        mcp_config = MCPConfig(
            mcp_servers={
                "calc": MCPServerConfig(
                    command=sys.executable,
                    args=[str(mock_script)],
                    read_only_tools=["add"],
                ),
                "disabled_server": MCPServerConfig(
                    command="nonexistent_cmd",
                    enabled=False,
                ),
            }
        )
        manager = MCPManager(config=mcp_config)
        async with manager:
            assert manager.servers_count == 1
            tools = manager.get_all_tools()
            assert len(tools) == 2
            assert {t.namespaced_name for t in tools} == {"calc__add", "calc__multiply"}

            # Test dispatch through manager
            res = await manager.call_tool("calc__add", {"a": 100, "b": 250})
            assert res == "350", f"Expected 350, got {res}"

            # Test lookup
            found = manager.find_tool("calc__multiply")
            assert found is not None
            c, t = found
            assert c.server_name == "calc"
            assert t.name == "multiply"

            # Test invalid tool
            try:
                await manager.call_tool("calc__nonexistent", {})
                assert False, "Should have raised ValueError"
            except ValueError:
                pass

        print("[OK] Test 2: MCPManager context manager, tool aggregation, dispatching passed")
        print("All MCP Manager tests passed successfully!")

    finally:
        if mock_script.exists():
            mock_script.unlink()

if __name__ == "__main__":
    asyncio.run(run_tests())
