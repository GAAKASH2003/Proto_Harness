"""End-to-end integration test for MCP support across Proto Harness."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompt_toolkit.document import Document
from proto_harness.tui.app import SlashCompleter
from proto_harness.mcp.config import MCPConfig, MCPServerConfig
from proto_harness.mcp.manager import MCPManager
from proto_harness.runtime.runner import run_headless
from proto_harness.permissions.types import PermissionMode
from pydantic_ai.models.test import TestModel

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
                    "serverInfo": {"name": "mock-mcp-e2e", "version": "1.0.0"},
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
                            "name": "ping",
                            "description": "Send ping and return pong",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "msg": {"type": "string"}
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
            args = params.get("arguments", {})
            msg = args.get("msg", "pong")
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"PONG: {msg}"}],
                    "isError": False
                }
            }
            sys.stdout.write(json.dumps(res) + "\\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
"""

async def run_tests():
    # 1. Test SlashCompleter includes /mcp
    completer = SlashCompleter(lambda: Path.cwd())
    doc = Document("/m", 2)
    completions = list(completer.get_completions(doc, None))
    mcp_completion = next((c for c in completions if c.text == "/mcp"), None)
    assert mcp_completion is not None, "SlashCompleter should suggest /mcp for '/m'"
    assert "MCP" in mcp_completion.display_meta_text
    print("[OK] Test 1: SlashCompleter /mcp autocompletion passed")

    # 2. Test Headless Runner with mock MCP server in workspace config
    temp_dir = Path(__file__).resolve().parent / "_temp_mcp_e2e"
    temp_dir.mkdir(exist_ok=True)
    server_script = temp_dir / "server.py"
    server_script.write_text(MOCK_SERVER_CODE, encoding="utf-8")

    mcp_json = temp_dir / ".mcp.json"
    mcp_json.write_text(
        json.dumps({
            "mcpServers": {
                "tester": {
                    "command": sys.executable,
                    "args": [str(server_script)],
                    "read_only_tools": ["ping"]
                }
            }
        }),
        encoding="utf-8"
    )

    try:
        # Run headless with TestModel configured
        test_model = TestModel()
        result = await run_headless(
            task="Please call tester__ping with msg='hello'",
            cwd=temp_dir,
            mode=PermissionMode.BYPASS,
            model=test_model,
            quiet=True,
        )

        assert result.run_id is not None
        assert result.output is not None
        print("[OK] Test 2: run_headless initialized MCPManager, executed, and cleaned up successfully")

        # 3. Test MCPManager tool discovery and namespacing in this workspace
        mgr = MCPManager(cwd=temp_dir)
        async with mgr:
            tools = mgr.get_all_tools()
            assert len(tools) == 1
            assert tools[0].namespaced_name == "tester__ping"
            assert tools[0].is_read_only is True

            call_res = await mgr.call_tool("tester__ping", {"msg": "proto-harness"})
            assert call_res == "PONG: proto-harness"

        print("[OK] Test 3: MCPManager auto-discovery from .mcp.json and tool dispatching passed")
        print("\nAll MCP End-to-End tests passed successfully!")

    finally:
        if mcp_json.exists():
            mcp_json.unlink()
        if server_script.exists():
            server_script.unlink()
        if temp_dir.exists():
            try:
                temp_dir.rmdir()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(run_tests())
