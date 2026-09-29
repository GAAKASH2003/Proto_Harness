"""Automated E2E Test Suite for Headless Runtime & Durable Execution."""

import json
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from proto_harness.cli import cli
from proto_harness.permissions.types import PermissionMode
from proto_harness.runtime.runner import HeadlessRunResult, run_headless


def test_headless_runner_execution_and_journal():
    print("Testing run_headless execution and durable checkpoint journal...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        cwd = Path(tmp_dir)
        task = "Analyze the codebase structure and summarize findings"

        # Mock the handler so we can test the headless driver, events, and checkpointing
        fake_response = ModelResponse(
            parts=[
                ToolCallPart(tool_name="read", args={"path": "main.py"}, tool_call_id="call_1"),
                TextPart(content="Analysis complete: The codebase has 3 modules and is healthy."),
            ],
            usage=MagicMock(input_tokens=150, output_tokens=45),
        )

        async def mock_run_turn(self, prompt):
            self.message_history = [fake_response]

        with patch("proto_harness.runtime.runner.AgentTurnHandler.run_turn", new=mock_run_turn):
            result = asyncio_run(
                run_headless(
                    task,
                    agent_name="build",
                    mode=PermissionMode.BYPASS,
                    cwd=cwd,
                    quiet=True,
                )
            )

        assert isinstance(result, HeadlessRunResult)
        assert "Analysis complete" in result.output
        assert result.tool_calls_count == 1
        assert result.total_tokens == 195
        assert result.run_id.startswith("run_")
        assert result.journal_path is not None
        assert result.journal_path.is_file()

        # Read the persisted checkpoint journal from disk
        with open(result.journal_path, "r", encoding="utf-8") as f:
            journal = json.load(f)

        assert journal["run_id"] == result.run_id
        assert journal["task"] == task
        assert journal["agent"] == "build"
        assert journal["mode"] == "bypass"
        assert journal["output"] == result.output
        assert journal["total_tokens"] == 195
        assert journal["tool_calls_count"] == 1

        print("  [SUCCESS] Headless execution and journal checkpointing verified.")


def test_cli_run_command_stdout_clean():
    print("Testing proto run CLI command with clean stdout...")
    runner = CliRunner()

    fake_result = HeadlessRunResult(
        output="Final markdown report from autonomous agent",
        run_id="run_20260929_test123",
        total_tokens=250,
        tool_calls_count=2,
        duration_s=1.5,
    )

    async def mock_run_headless(*args, **kwargs):
        return fake_result

    with patch("proto_harness.runtime.runner.run_headless", side_effect=mock_run_headless):
        res = runner.invoke(cli, ["run", "write tests for auth", "-a", "build"])

        assert res.exit_code == 0
        # Output on stdout must be clean and match agent's answer
        assert "Final markdown report from autonomous agent" in res.output

    print("  [SUCCESS] CLI proto run command prints clean output.")


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


if __name__ == "__main__":
    test_headless_runner_execution_and_journal()
    test_cli_run_command_stdout_clean()
    print("\n[SUCCESS] ALL HEADLESS RUNTIME & CHECKPOINT TESTS PASSED 100%!")
