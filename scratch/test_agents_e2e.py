"""End-to-End Automated Test Suite for Phase 4: Agent Catalog & Personas.

Tests:
1. AgentDef Entity Constraints & Validation
2. Catalog Loader (built-in discovery & project override)
3. Tool Allowlisting in Agent Factory
4. Orchestration Tools (enter_plan_mode & exit_plan_mode approval)
5. Agent Turn Handler Persona Switching (history preservation)
6. SlashCompleter Commands & Sub-completions
"""

import asyncio
from pathlib import Path

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.agent.loop import AgentTurnHandler
from proto_harness.agents.loader import load_agent, load_agents, load_builtin_agents, parse_agent_file
from proto_harness.entities import events
from proto_harness.entities.agent_def import AgentDef
from proto_harness.entities.permissions import PermissionDecision, PermissionOutcome, PermissionRequest
from proto_harness.permissions.gate import PermissionGate
from proto_harness.permissions.types import PermissionMode
from proto_harness.tools import KNOWN_TOOL_NAMES
from proto_harness.tools.orchestration import enter_plan_mode, exit_plan_mode
from proto_harness.tui.app import SlashCompleter
from prompt_toolkit.document import Document


def test_agent_def_validation():
    print("Testing AgentDef entity validation...")
    # 1. Valid AgentDef
    agent = AgentDef(
        name="test-agent",
        description="A test agent",
        tools=("read", "cd", "pwd"),
        mode=PermissionMode.PLAN,
        prompt="You are a test agent.",
    )
    assert agent.name == "test-agent"
    assert agent.mode == PermissionMode.PLAN
    assert agent.tools == ("read", "cd", "pwd")

    # 2. Unknown tool rejection
    try:
        AgentDef(
            name="bad-agent",
            description="Bad agent",
            tools=("read", "delete_hard_drive"),
            mode=PermissionMode.PLAN,
            prompt="Prompt",
        )
        assert False, "Should reject unknown tools"
    except ValueError as exc:
        assert "lists unknown tool(s)" in str(exc)

    # 3. Empty name rejection
    try:
        AgentDef(name=" ", description="desc", tools=("read",), mode=PermissionMode.PLAN, prompt="prompt")
        assert False, "Should reject empty name"
    except ValueError as exc:
        assert "name must be a non-empty string" in str(exc)

    # 4. Empty prompt rejection
    try:
        AgentDef(name="agent", description="desc", tools=("read",), mode=PermissionMode.PLAN, prompt="   ")
        assert False, "Should reject empty prompt"
    except ValueError as exc:
        assert "must have a non-empty prompt" in str(exc)

    print("  -> AgentDef validation passed!")


def test_catalog_loader():
    print("Testing Agents Catalog loader...")
    # 1. Built-in discovery
    builtins = load_builtin_agents()
    expected_agents = {"build", "plan", "code-reviewer", "explore"}
    assert expected_agents.issubset(set(builtins.keys())), f"Missing built-ins: {expected_agents - set(builtins.keys())}"

    # 2. Specific loader
    build_def = load_agent("build")
    assert build_def.name == "build"
    assert build_def.mode == PermissionMode.DEFAULT
    assert "write" in build_def.tools
    assert "bash" in build_def.tools

    plan_def = load_agent("plan")
    assert plan_def.name == "plan"
    assert plan_def.mode == PermissionMode.PLAN
    assert "write" not in plan_def.tools
    assert "bash" not in plan_def.tools
    assert "exit_plan_mode" in plan_def.tools

    # 3. Unknown agent raises helpful ValueError
    try:
        load_agent("super-coder")
        assert False, "Should raise ValueError for unknown agent"
    except ValueError as exc:
        assert "no such agent 'super-coder'" in str(exc)
        assert "build" in str(exc)

    print("  -> Catalog loader passed!")


def test_tool_allowlisting():
    print("Testing Tool allowlisting in build_agent()...")
    plan_def = load_agent("plan")
    plan_agent = build_agent(agent_def=plan_def)
    plan_tools = set(plan_agent._function_toolset.tools.keys())

    # Plan should only have allowed tools
    for tool_name in plan_def.tools:
        assert tool_name in plan_tools, f"Tool {tool_name} expected in plan agent"

    # Plan MUST NOT have mutating tools
    assert "write" not in plan_tools
    assert "edit" not in plan_tools
    assert "bash" not in plan_tools

    # Build agent should have write and edit
    build_def = load_agent("build")
    build_agent_instance = build_agent(agent_def=build_def)
    build_tools = set(build_agent_instance._function_toolset.tools.keys())
    assert "write" in build_tools
    assert "edit" in build_tools
    assert "bash" in build_tools

    print("  -> Tool allowlisting passed!")


async def test_orchestration_tools():
    print("Testing orchestration tools (enter_plan_mode / exit_plan_mode)...")
    gate = PermissionGate(mode=PermissionMode.DEFAULT)
    user_answers = []

    async def mock_user_question(question: str) -> str:
        return user_answers.pop(0)

    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=lambda e: None,
        gate=gate,
        resolve_permission=lambda r: None,  # type: ignore
        resolve_user_question=mock_user_question,
    )

    class DummyContext:
        def __init__(self, d):
            self.deps = d

    ctx = DummyContext(deps)

    # 1. enter_plan_mode
    assert gate.mode == PermissionMode.DEFAULT
    res1 = await enter_plan_mode(ctx)  # type: ignore
    assert gate.mode == PermissionMode.PLAN
    assert "Entered plan mode" in res1

    # 2. exit_plan_mode denied
    user_answers.append("n")
    res2 = await exit_plan_mode(ctx, plan="1. Refactor auth\n2. Run tests")  # type: ignore
    assert gate.mode == PermissionMode.PLAN  # Stays in PLAN mode
    assert "Plan not approved" in res2

    # 3. exit_plan_mode approved
    user_answers.append("yes")
    res3 = await exit_plan_mode(ctx, plan="1. Refactor auth\n2. Run tests")  # type: ignore
    assert gate.mode == PermissionMode.EDIT  # Flips to EDIT mode
    assert "Plan approved — entering edit mode." in res3

    print("  -> Orchestration tools passed!")


def test_handler_persona_switching():
    print("Testing AgentTurnHandler persona switching...")
    build_def = load_agent("build")
    plan_def = load_agent("plan")

    gate = PermissionGate(mode=build_def.mode)
    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=lambda e: None,
        gate=gate,
        resolve_permission=lambda r: None,  # type: ignore
        active_agent=build_def,
    )

    agent1 = build_agent(agent_def=build_def)
    handler = AgentTurnHandler(agent=agent1, deps=deps)

    # Simulate some message history
    from pydantic_ai.messages import ModelRequest, UserPromptPart
    handler.message_history.append(ModelRequest(parts=[UserPromptPart(content="hello")]))
    assert len(handler.message_history) == 1

    # Switch to plan agent
    agent2 = build_agent(agent_def=plan_def)
    handler.agent = agent2
    deps.active_agent = plan_def
    gate.set_mode(plan_def.mode)

    # History must remain intact
    assert len(handler.message_history) == 1
    assert handler.agent is agent2
    assert deps.active_agent.name == "plan"
    assert gate.mode == PermissionMode.PLAN

    print("  -> Persona switching passed!")


def test_slash_completer():
    print("Testing SlashCompleter for agent commands...")
    completer = SlashCompleter(lambda: Path.cwd())

    # 1. Base commands
    assert "/agent" in completer._base_commands
    assert "/agents" in completer._base_commands

    # 2. Sub-command completions for /agent <name>
    doc = Document("/agent ")
    completions = list(completer.get_completions(doc, None))
    names = [c.text for c in completions]
    assert "/agent build" in names
    assert "/agent plan" in names
    assert "/agent code-reviewer" in names
    assert "/agent explore" in names

    # Partial prefix
    doc_prefix = Document("/agent pl")
    completions_prefix = list(completer.get_completions(doc_prefix, None))
    assert len(completions_prefix) == 1
    assert completions_prefix[0].text == "/agent plan"

    print("  -> SlashCompleter passed!")


if __name__ == "__main__":
    print("\n--- RUNNING PHASE 4 TEST SUITE ---\n")
    test_agent_def_validation()
    test_catalog_loader()
    test_tool_allowlisting()
    asyncio.run(test_orchestration_tools())
    test_handler_persona_switching()
    test_slash_completer()
    print("\n[SUCCESS] ALL PHASE 4 AGENT CATALOG & PERSONA TESTS PASSED!\n")
