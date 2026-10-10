"""Integration test for MCP tool registration and agent loop execution."""
import asyncio
import json
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pydantic_ai.models.test import TestModel
from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.mcp.config import MCPConfig, MCPServerConfig
from proto_harness.mcp.manager import MCPManager
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.entities.permissions import PermissionDecision, PermissionOutcome

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
                        }
                    ]
                }
            }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

        elif method == "tools/call":
            params = req.get("params", {})
            args = params.get("arguments", {})
            val = args.get("a", 0) + args.get("b", 0)
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(val)}],
                    "isError": False
                }
            }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
"""

async def run_tests():
    mock_script = Path(__file__).resolve().parent / "mock_mcp_agent_server.py"
    mock_script.write_text(MOCK_SERVER_CODE, encoding="utf-8")

    try:
        mcp_config = MCPConfig(
            mcp_servers={
                "calc": MCPServerConfig(
                    command=sys.executable,
                    args=[str(mock_script)],
                    read_only_tools=["add"],
                )
            }
        )

        manager = MCPManager(config=mcp_config)
        await manager.start_all()

        # Build agent with TestModel and MCP manager
        test_model = TestModel()
        agent = build_agent(model=test_model, mcp_manager=manager)

        # Check tool registration
        tool_names = list(agent._function_toolset.tools.keys())
        assert "calc__add" in tool_names, f"calc__add should be registered in agent tools: {tool_names}"
        
        registered_tool = agent._function_toolset.tools["calc__add"]
        assert registered_tool.description == "Add two numbers"
        assert registered_tool.function_schema.json_schema == {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        }
        print("[OK] calc__add tool successfully registered on Agent with correct schema and description")

        # Test tool invocation with AgentDeps and PermissionGate
        events = []
        async def mock_resolver(req):
            return PermissionDecision(outcome=PermissionOutcome.ALLOW)

        deps = AgentDeps(
            cwd=Path.cwd(),
            emit=lambda ev: events.append(ev),
            gate=PermissionGate(mode=PermissionMode.DEFAULT),
            resolve_permission=mock_resolver,
            mcp_manager=manager,
        )

        # Direct execution of the wrapped tool
        tool_wrapper = registered_tool.function
        mock_ctx = type("MockRunCtx", (), {"deps": deps})()
        result = await tool_wrapper(mock_ctx, a=20, b=22)
        assert result == "42", f"Expected 42, got {result}"
        print("[OK] MCP tool invocation through wrapper executed and returned expected result: 42")

        await manager.close_all()
        print("\nAll Step 3 Agent Factory MCP integration tests passed successfully!")

    finally:
        if mock_script.exists():
            mock_script.unlink()

if __name__ == "__main__":
    asyncio.run(run_tests())
