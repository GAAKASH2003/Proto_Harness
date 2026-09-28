"""End-to-End Automated Test Suite for Phase 5: Subagents & Multi-Agent Orchestration.

Tests:
1. Input Substance Floor & Word Count (ModelRetry on < 8 words)
2. Fanout Width Cap (ModelRetry on > 6 prompts or empty prompts)
3. Primary Agent vs Subagent Guard (load_primary_agent rejects subagent-only personas)
4. Child Isolation & Tool Restrictions (explore persona has read-only tools and no recursive agent tool)
5. Execution, Streaming Events, and Aggregation Formatting with Synthesis Footer
6. Quality Validation, Retry Nudge & Budget Truncation
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.tools import RunContext

from proto_harness.agent.deps import AgentDeps
from proto_harness.agents.loader import load_agent, load_builtin_agents, load_primary_agent
from proto_harness.config.settings import Settings
from proto_harness.entities import events
from proto_harness.entities.agent_def import AgentDef
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.tools.agent import (
    MAX_FANOUT_PROMPTS,
    MIN_PROMPT_WORDS,
    _CHILD_FAILED_NOTE,
    _RETRY_NUDGE,
    _SYNTHESIS_FOOTER,
    _label,
    _truncate_text,
    _run_attempt,
    _spawn_child,
    agent,
)


def _make_dummy_run_context(cwd: Path | None = None, emit_fn=None) -> RunContext[AgentDeps]:
    """Create a mock RunContext with valid AgentDeps for testing tools."""
    work_dir = cwd or Path.cwd()
    emitted = []

    def default_emit(event):
        emitted.append(event)

    deps = AgentDeps(
        cwd=work_dir,
        emit=emit_fn or default_emit,
        gate=PermissionGate(mode=PermissionMode.DEFAULT),
        resolve_permission=lambda r: None,  # type: ignore
        resolve_user_question=None,
    )

    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps
    return ctx


def test_substance_floor_and_width_cap():
    print("Testing substance floor (< 8 words) and width cap (> 6 prompts)...")
    ctx = _make_dummy_run_context()

    # 1. Empty prompts
    try:
        asyncio.run(agent(ctx, []))
        assert False, "Should raise ModelRetry on empty prompts"
    except ModelRetry as exc:
        assert "at least one exploration prompt" in str(exc)

    # 2. Prompts with fewer than MIN_PROMPT_WORDS (8 words)
    short_prompts = [
        "check the login file",  # 4 words
    ]
    try:
        asyncio.run(agent(ctx, short_prompts))
        assert False, "Should raise ModelRetry on short prompt"
    except ModelRetry as exc:
        assert f"at least {MIN_PROMPT_WORDS} words" in str(exc)
        assert "question" in str(exc).lower()

    # 3. Multiple prompts where one is short
    mixed_prompts = [
        "Find where the database connection pool is configured and check for leak risks",  # 12 words
        "too short prompt",  # 3 words
    ]
    try:
        asyncio.run(agent(ctx, mixed_prompts))
        assert False, "Should reject if any prompt is under substance floor"
    except ModelRetry as exc:
        assert "Prompt 2 ('too short prompt') is too terse (3 words)" in str(exc)

    # 4. Width cap exceeding MAX_FANOUT_PROMPTS (6)
    valid_prompt = "Inspect the module configuration and locate any broken imports or missing keys"
    too_many = [valid_prompt] * (MAX_FANOUT_PROMPTS + 1)
    try:
        asyncio.run(agent(ctx, too_many))
        assert False, f"Should reject prompts exceeding {MAX_FANOUT_PROMPTS}"
    except ModelRetry as exc:
        assert f"limit is {MAX_FANOUT_PROMPTS}" in str(exc)

    print("  [SUCCESS] Substance floor and width cap properly guarded.")


def test_primary_agent_vs_subagent_guard():
    print("Testing primary agent loader vs subagent persona guard...")
    # Built-in agents
    builtins = load_builtin_agents()
    assert "explore" in builtins
    assert builtins["explore"].subagent is True
    assert builtins["build"].subagent is False
    assert builtins["plan"].subagent is False
    assert builtins["code-reviewer"].subagent is False

    # Primary agent loader should reject subagent-only explore
    try:
        load_primary_agent("explore")
        assert False, "load_primary_agent should fail for explore"
    except ValueError as exc:
        assert "cannot be selected as a main agent" in str(exc)

    # Primary agent loader should accept build, plan, code-reviewer
    build = load_primary_agent("build")
    assert build.name == "build"
    plan = load_primary_agent("plan")
    assert plan.name == "plan"
    code_reviewer = load_primary_agent("code-reviewer")
    assert code_reviewer.name == "code-reviewer"

    print("  [SUCCESS] Primary agent guards working correctly.")


def test_child_isolation_and_explore_tools():
    print("Testing child isolation and explore persona toolset...")
    explore = load_agent("explore")
    assert explore is not None
    # Explore must only have read-only tools
    allowed = set(explore.tools)
    assert allowed == {"read", "cd", "pwd", "skill"}
    assert "write" not in allowed
    assert "edit" not in allowed
    assert "bash" not in allowed
    assert "agent" not in allowed  # explore cannot recursively spawn more agents!

    # build, plan, code-reviewer must have agent tool
    for name in ("build", "plan", "code-reviewer"):
        agent_def = load_agent(name)
        assert agent_def is not None
        assert "agent" in agent_def.tools, f"{name} should have 'agent' tool"

    print("  [SUCCESS] Child tool restrictions and isolation verified.")


def test_aggregation_formatting():
    print("Testing subagent report aggregation and synthesis footer...")
    ctx = _make_dummy_run_context()
    prompts = [
        "Investigate database pool configuration in src/db.py for potential timeout bugs",
        "Inspect redis cache connection retry settings in src/cache.py for disconnect handling",
    ]
    reports = {
        1: "Finding: Pool timeout is set to 30s in db.py:42.\nEvidence: db.py:42-50.\nTrace: config -> db.py.",
        2: "Finding: Retry limit is 3 in cache.py:15.\nEvidence: cache.py:15-22.\nTrace: config -> cache.py.",
    }

    async def mock_spawn(c, prompt, index, max_bytes):
        return reports[index]

    with patch("proto_harness.tools.agent._spawn_child", side_effect=mock_spawn):
        aggregated = asyncio.run(agent(ctx, prompts))

    assert '## Subagent 1 — "Investigate database pool' in aggregated
    assert '## Subagent 2 — "Inspect redis cache' in aggregated
    assert "db.py:42" in aggregated
    assert "cache.py:15" in aggregated
    assert _SYNTHESIS_FOOTER in aggregated

    print("  [SUCCESS] Aggregation formatting and synthesis guidance verified.")


def test_budget_truncation():
    print("Testing per-subagent byte budget truncation...")
    huge_report = "A" * 1000
    # Truncate at 200 bytes
    truncated = _truncate_text(huge_report, max_bytes=200)
    assert "[... report truncated at 200 bytes ...]" in truncated
    assert len(truncated.encode("utf-8")) < 1000

    # Under budget should remain unchanged
    short_report = "Finding: No errors found in file.py:10."
    assert _truncate_text(short_report, max_bytes=200) == short_report

    print("  [SUCCESS] Output truncation correctly enforces byte budgets.")


def test_agent_parallel_execution_mocked():
    print("Testing parallel subagent execution with mocked explore child...")
    emitted_events = []

    def mock_emit(evt):
        emitted_events.append(evt)

    ctx = _make_dummy_run_context(emit_fn=mock_emit)

    prompts = [
        "Examine authentication flow in src/auth.py and verify token expiration handling",
        "Examine database migrations in migrations/ and verify rollback logic consistency",
    ]

    async def mock_run_attempt(child_ctx, prompt, index):
        # Emit a child tool call started event to verify child indexing
        child_ctx.deps.emit(
            events.ToolCallStarted(
                tool_call_id=f"call_{index}",
                name="read",
                args='{"path": "file.py"}',
                child_index=index,
            )
        )
        return f"Report {index}: verified file:line evidence for prompt '{prompt[:20]}...'"

    with patch("proto_harness.tools.agent._run_attempt", side_effect=mock_run_attempt):
        result = asyncio.run(agent(ctx, prompts))

    # Verify result contains reports from both subagents
    assert "## Subagent 1" in result
    assert "## Subagent 2" in result
    assert "Report 1:" in result
    assert "Report 2:" in result
    assert _SYNTHESIS_FOOTER in result

    # Verify child tool call events were emitted with proper child_index
    tool_events = [e for e in emitted_events if isinstance(e, events.ToolCallStarted)]
    assert len(tool_events) == 2
    assert tool_events[0].child_index == 1
    assert tool_events[1].child_index == 2

    print("  [SUCCESS] Parallel subagent execution and child event stream verified.")


def test_subagent_retry_on_unsupported_report():
    print("Testing retry nudge when subagent returns unverified report...")
    ctx = _make_dummy_run_context()
    attempts = []

    async def mock_run_attempt(child_ctx, prompt, index):
        attempts.append(prompt)
        if len(attempts) == 1:
            # First attempt returns None (unsupported / no code read)
            return None
        # Second attempt (after retry nudge) succeeds
        return "Report: successfully inspected code and found evidence at line 10."

    with patch("proto_harness.tools.agent._run_attempt", side_effect=mock_run_attempt):
        output = asyncio.run(_spawn_child(ctx, "Investigate memory usage patterns in backend services", index=1, max_bytes=4000))

    assert len(attempts) == 2
    assert _RETRY_NUDGE in attempts[1]
    assert "successfully inspected code" in output

    print("  [SUCCESS] Retry nudge triggered and recovered.")


if __name__ == "__main__":
    test_substance_floor_and_width_cap()
    test_primary_agent_vs_subagent_guard()
    test_child_isolation_and_explore_tools()
    test_aggregation_formatting()
    test_budget_truncation()
    test_agent_parallel_execution_mocked()
    test_subagent_retry_on_unsupported_report()
    print("\n[SUCCESS] ALL SUBAGENT & MULTI-AGENT ORCHESTRATION TESTS PASSED 100%!")
