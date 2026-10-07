from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-million token rates in USD."""

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    cached_usd_per_mtok: float | None = None


@dataclass(slots=True)
class UsageMetrics:
    """Accumulated usage and cost across one or more turns."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
