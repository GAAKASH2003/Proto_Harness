"""End-to-end test suite for Phase 3C: Context Window Gauge & Compaction."""
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    UserPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RequestUsage

from proto_harness.agent.deps import AgentDeps
from proto_harness.agent.factory import build_agent
from proto_harness.agent.loop import AgentTurnHandler, _leg_input_tokens
from proto_harness.config.settings import settings
from proto_harness.context.compaction import (
    CompactOutcome,
    _is_compaction_boundary,
    split_tail,
    microcompact,
    build_summary_message,
    estimate_history_tokens,
    reserve_threshold,
    should_compact,
)
from proto_harness.entities import events
from proto_harness.permissions.gate import PermissionGate
from proto_harness.tui.render import context_gauge, render_event


def test_gauge_and_thresholds():
    print("--- Test 1: Gauge & Thresholds ---")
    assert reserve_threshold(1_000_000, 0.20) == 800_000
    assert reserve_threshold(1_000_000, 0.40) == 600_000

    lbl, col = context_gauge(0.05, warn_at=0.6, danger_at=0.8)
    assert lbl == "○ 5%" and col == "green"

    lbl, col = context_gauge(0.65, warn_at=0.6, danger_at=0.8)
    assert lbl == "◕ 65%" and col == "yellow"

    lbl, col = context_gauge(0.85, warn_at=0.6, danger_at=0.8)
    assert lbl == "◕ 85%" and col == "red"

    lbl, col = context_gauge(1.0, warn_at=0.6, danger_at=0.8)
    assert lbl == "● 100%" and col == "red"
    print("  -> Gauge & thresholds passed!")


def test_boundary_and_split():
    print("--- Test 2: Compaction Boundary & Split ---")
    req1 = ModelRequest(parts=[UserPromptPart("Initial task prompt")])
    resp1 = ModelResponse(parts=[ToolCallPart("read", {"path": "main.py"}, "c1")])
    req2 = ModelRequest(parts=[ToolReturnPart("read", "def hello(): pass\n" * 200, "c1")])
    resp2 = ModelResponse(parts=[TextPart("Read the file successfully")])

    history = [req1, resp1, req2, resp2]

    assert _is_compaction_boundary(req1) is True
    assert _is_compaction_boundary(resp1) is True
    assert _is_compaction_boundary(req2) is False  # Cannot cut at tool return!
    assert _is_compaction_boundary(resp2) is True

    # Slicing with small token budget should snap back safely to resp1 or resp2, never req2
    cut = split_tail(history, keep_recent_tokens=50)
    assert cut in (1, 3), f"Cut should be a boundary, got {cut}"
    print("  -> Boundary snapping passed!")


def test_microcompaction():
    print("--- Test 3: Microcompaction ---")
    req1 = ModelRequest(parts=[UserPromptPart("Check files")])
    resp1 = ModelResponse(parts=[ToolCallPart("read", {"path": "big.txt"}, "c1")])
    heavy_payload = "A" * 5000
    req2 = ModelRequest(parts=[ToolReturnPart("read", heavy_payload, "c1")])
    resp2 = ModelResponse(parts=[TextPart("Done reading")])

    history = [req1, resp1, req2, resp2]
    new_history, elided = microcompact(history, keep_recent_tokens=10)

    assert elided == 1
    assert new_history[2].parts[0].content == "[tool output elided by microcompaction]"
    # Caller's original object must not be mutated
    assert history[2].parts[0].content == heavy_payload
    print("  -> Microcompaction passed!")


async def test_handler_compaction():
    print("--- Test 4: AgentTurnHandler Compaction Integration ---")
    emitted = []

    def mock_emit(event):
        emitted.append(event)

    test_model = TestModel()
    agent = build_agent(model=test_model)
    gate = PermissionGate()
    deps = AgentDeps(
        cwd=Path.cwd(),
        emit=mock_emit,
        gate=gate,
        resolve_permission=lambda r: None,
    )

    handler = AgentTurnHandler(agent=agent, deps=deps, compaction_model=test_model)
    assert handler.last_input_tokens == 0

    # Test nothing to compact on empty/short history
    outcome = await handler.compact()
    assert outcome == CompactOutcome.NOTHING_TO_COMPACT

    # Populate history with enough turns to trigger split
    history = []
    for i in range(5):
        history.append(ModelRequest(parts=[UserPromptPart(f"User request {i} with some details " * 20)]))
        resp = ModelResponse(
            parts=[TextPart(f"Assistant response {i} explaining work " * 20)],
            usage=RequestUsage(input_tokens=1000 + i * 500, output_tokens=100),
        )
        history.append(resp)

    handler.message_history = history
    handler._last_input_tokens = _leg_input_tokens(history)
    assert handler.last_input_tokens > 0

    # Temporarily set keep_recent_tokens to keep ~2-3 recent messages
    settings.compaction_keep_recent_tokens = 500
    outcome = await handler.compact()
    assert outcome == CompactOutcome.COMPACTED

    # Verify head is summary message
    assert len(handler.message_history) > 1
    head = handler.message_history[0]
    assert isinstance(head, ModelRequest)
    assert "Summary of the earlier conversation" in head.parts[0].content

    # Verify event emitted
    compacted_events = [e for e in emitted if isinstance(e, events.ContextCompacted)]
    assert len(compacted_events) == 1
    rendered = render_event(compacted_events[0])
    assert "compacted context" in str(rendered)

    # Test clear resets tokens and history
    handler.clear()
    assert handler.last_input_tokens == 0
    assert len(handler.message_history) == 0
    print("  -> Handler compaction & clear passed!")


async def main():
    test_gauge_and_thresholds()
    test_boundary_and_split()
    test_microcompaction()
    await test_handler_compaction()
    print("\n🎉 ALL PHASE 3C COMPACTION TESTS PASSED!")


if __name__ == "__main__":
    asyncio.run(main())
