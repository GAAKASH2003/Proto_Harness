from __future__ import annotations

from .types import ModelPricing, UsageMetrics
from .models import get_model_info

DEFAULT_RATES: dict[str, ModelPricing] = {
    # Google Gemini
    "gemini-2.0-flash": ModelPricing(input_usd_per_mtok=0.075, output_usd_per_mtok=0.30),
    "gemini-1.5-flash": ModelPricing(input_usd_per_mtok=0.075, output_usd_per_mtok=0.30),
    "gemini-1.5-pro": ModelPricing(input_usd_per_mtok=1.25, output_usd_per_mtok=5.00),
    "gemini-3.5-flash": ModelPricing(input_usd_per_mtok=1.50, output_usd_per_mtok=9.00),
    # OpenRouter / Anthropic / Meta
    "anthropic/claude-3.5-sonnet": ModelPricing(input_usd_per_mtok=3.00, output_usd_per_mtok=15.00),
    "anthropic/claude-3.5-haiku": ModelPricing(input_usd_per_mtok=0.80, output_usd_per_mtok=4.00),
    "meta-llama/llama-3.3-70b-instruct": ModelPricing(input_usd_per_mtok=0.40, output_usd_per_mtok=0.40),
}


def compute_batch_cost(
    model_name: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int | None = None,
) -> UsageMetrics:
    """Compute token costs for a single model batch."""
    if total_tokens is None:
        total_tokens = input_tokens + output_tokens

    model_info = get_model_info(model_name)
    pricing = model_info.pricing

    input_cost = (input_tokens / 1_000_000) * pricing.input_usd_per_mtok
    output_cost = (output_tokens / 1_000_000) * pricing.output_usd_per_mtok

    return UsageMetrics(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=round(input_cost + output_cost, 6),
    )


def format_cost_usd(cost: float) -> str:
    """Format USD cost nicely for terminal display."""
    if cost <= 0.0:
        return "$0.00"
    if cost < 0.00001:
        return "< $0.00001"
    if cost < 0.01:
        return f"${cost:.5f}"
    return f"${cost:.4f}"
