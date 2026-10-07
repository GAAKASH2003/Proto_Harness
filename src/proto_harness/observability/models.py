from __future__ import annotations

from dataclasses import dataclass
from .types import ModelPricing


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """Metadata and token pricing parameters for an LLM."""

    name: str
    pricing: ModelPricing
    context_window: int = 1_000_000


# Default catalog of provider pricing (USD per million tokens)
DEFAULT_MODELS: dict[str, ModelInfo] = {
    # Google Gemini
    "gemini-2.0-flash": ModelInfo(
        name="gemini-2.0-flash",
        pricing=ModelPricing(input_usd_per_mtok=0.075, output_usd_per_mtok=0.30),
        context_window=1_048_576,
    ),
    "gemini-1.5-flash": ModelInfo(
        name="gemini-1.5-flash",
        pricing=ModelPricing(input_usd_per_mtok=0.075, output_usd_per_mtok=0.30),
        context_window=1_048_576,
    ),
    "gemini-1.5-pro": ModelInfo(
        name="gemini-1.5-pro",
        pricing=ModelPricing(input_usd_per_mtok=1.25, output_usd_per_mtok=5.00),
        context_window=2_097_152,
    ),
    "gemini-3.5-flash": ModelInfo(
        name="gemini-3.5-flash",
        pricing=ModelPricing(input_usd_per_mtok=1.50, output_usd_per_mtok=9.00),
        context_window=1_000_000,
    ),
    # OpenRouter / Anthropic / Meta
    "anthropic/claude-3.5-sonnet": ModelInfo(
        name="anthropic/claude-3.5-sonnet",
        pricing=ModelPricing(input_usd_per_mtok=3.00, output_usd_per_mtok=15.00),
        context_window=200_000,
    ),
    "anthropic/claude-3.5-haiku": ModelInfo(
        name="anthropic/claude-3.5-haiku",
        pricing=ModelPricing(input_usd_per_mtok=0.80, output_usd_per_mtok=4.00),
        context_window=200_000,
    ),
    "meta-llama/llama-3.3-70b-instruct": ModelInfo(
        name="meta-llama/llama-3.3-70b-instruct",
        pricing=ModelPricing(input_usd_per_mtok=0.40, output_usd_per_mtok=0.40),
        context_window=131_072,
    ),
}


def get_model_info(model_name: str) -> ModelInfo:
    """Normalize model slug and return its pricing and context window."""
    normalized = model_name.strip().lower()

    # Exact match
    if normalized in DEFAULT_MODELS:
        return DEFAULT_MODELS[normalized]

    # Strip prefixes like 'google/' or suffixes like ':free', '-latest'
    clean = normalized
    if clean.startswith("google/"):
        clean = clean[len("google/"):]
    if clean.endswith(":free"):
        clean = clean[:-len(":free")]
    if clean.endswith("-latest"):
        clean = clean[:-len("-latest")]

    if clean in DEFAULT_MODELS:
        return DEFAULT_MODELS[clean]

    # Partial / prefix matching
    for key, info in DEFAULT_MODELS.items():
        if key in clean or clean in key:
            return info

    # Fallback default (Gemini 2.0 Flash pricing) if model is unrecognized
    return ModelInfo(
        name=model_name,
        pricing=ModelPricing(input_usd_per_mtok=0.075, output_usd_per_mtok=0.30),
        context_window=1_000_000,
    )