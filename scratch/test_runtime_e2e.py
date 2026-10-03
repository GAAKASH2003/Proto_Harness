"""Automated E2E Test Suite for Headless Runtime, Durable Execution & Sandboxing."""

import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import MagicMock, patch

from click.testing import CliRunner
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart

from proto_harness.cli import cli
from proto_harness.permissions.types import PermissionMode
from proto_harness.runtime.runner import HeadlessRunResult, run_headless


def _git(cmd: list[str], cwd: Path) -> str:
    res = subprocess.run(
        ["git"] + cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
        encoding="utf-8",
    )
    return res.stdout.strip()


def setup_temp_git_repo() -> Path:
    temp_dir = Path(tempfile.mkdtemp(prefix="proto_rt_sbx_")).resolve()
    _git(["init"], temp_dir)
    _git(["config", "user.name", "TestUser"], temp_dir)
    _git(["config", "user.email", "test@example.com"], temp_dir)
    _git(["config", "commit.gpgsign", "false"], temp_dir)

    readme = temp_dir / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    _git(["add", "README.md"], temp_dir)
    _git(["commit", "-m", "Initial commit"], temp_dir)
    return temp_dir


def test_headless_runner_execution_and_journal():
    print("Testing run_headless execution and durable checkpoint journal...")
    with tempfile.TemporaryDirectory() as tmp_dir:
        cwd = Path(tmp_dir)
        task = "Analyze the codebase structure and summarize findings"

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
            result = asyncio.run(
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


def test_headless_runner_with_sandbox():
    print("Testing run_headless with --sandbox and --apply...")
    repo = setup_temp_git_repo()
    task = "Add new utility file in sandbox"

    try:
        fake_response = ModelResponse(
            parts=[TextPart(content="Created utility.py in sandbox successfully.")],
            usage=MagicMock(input_tokens=80, output_tokens=20),
        )

        async def mock_run_turn(self, prompt):
            # Write file in the agent's active cwd (which is the sandbox worktree)
            new_file = self._deps.cwd / "utility.py"
            new_file.write_text("def util(): pass\n", encoding="utf-8")
            self.message_history = [fake_response]

        with patch("proto_harness.runtime.runner.AgentTurnHandler.run_turn", new=mock_run_turn):
            result = asyncio.run(
                run_headless(
                    task,
                    agent_name="build",
                    mode=PermissionMode.BYPASS,
                    cwd=repo,
                    quiet=True,
                    sandbox=True,
                    apply=True,
                )
            )

        assert result.sandbox_id is not None
        assert result.sandbox_branch.startswith("proto/sbx_")
        assert result.sandbox_applied is True
        assert "utility.py" in result.diff

        # Since apply=True, utility.py should now exist in the host repo!
        assert (repo / "utility.py").exists(), "utility.py must be applied to host repository"

        # Check journal
        with open(result.journal_path, "r", encoding="utf-8") as f:
            journal = json.load(f)
        assert journal["sandbox_id"] == result.sandbox_id
        assert journal["sandbox_applied"] is True

        print("  [SUCCESS] Headless sandbox execution, handback, and apply verified.")

    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_cli_run_command_stdout_clean():
    print("Testing proto run CLI command with clean stdout and sandbox flags...")
    runner = CliRunner()

    fake_result = HeadlessRunResult(
        output="Final markdown report from autonomous agent",
        run_id="run_20260929_test123",
        agent="build",
        mode="bypass",
        prompt="test prompt",
        final_response="Final markdown report from autonomous agent",
        start_time="2026-09-29T12:00:00",
        end_time="2026-09-29T12:00:01",
        total_tokens=250,
        tool_calls_count=2,
        duration_seconds=1.5,
        sandbox_id="sbx_test_cli",
        sandbox_branch="proto/sbx_test_cli",
        sandbox_applied=True,
        diff="diff --git a/file b/file\n+new",
    )

    async def mock_run_headless(*args, **kwargs):
        return fake_result

    with patch("proto_harness.runtime.runner.run_headless", side_effect=mock_run_headless):
        res = runner.invoke(cli, ["run", "write tests for auth", "-a", "build", "--sandbox", "--diff"])

        assert res.exit_code == 0
        assert "Final markdown report from autonomous agent" in res.output

    print("  [SUCCESS] CLI proto run command prints clean output with sandbox options.")


if __name__ == "__main__":
    test_headless_runner_execution_and_journal()
    test_headless_runner_with_sandbox()
    test_cli_run_command_stdout_clean()
    print("\nALL RUNTIME TESTS PASSED!")
