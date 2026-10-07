from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .cost import compute_batch_cost, format_cost_usd
from .models import get_model_info
from .types import UsageMetrics


@dataclass
class UsageTracker:
    """In-memory telemetry tracker for tokens, costs, and generation latency."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0
    turns_count: int = 0
    active_model: str = "gemini-2.0-flash"
    history: list[dict] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def tokens_per_second(self) -> float:
        """Generation throughput based on generated output tokens over latency."""
        if self.total_duration_s <= 0.0:
            return 0.0
        return round(self.total_output_tokens / self.total_duration_s, 1)

    def record_turn(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
        duration_s: float = 0.0,
    ) -> UsageMetrics:
        """Record usage from an individual turn and accumulate session metrics."""
        self.active_model = model_name
        metrics = compute_batch_cost(
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        self.total_input_tokens += metrics.input_tokens
        self.total_output_tokens += metrics.output_tokens
        self.total_cost_usd += metrics.cost_usd
        self.total_duration_s += max(duration_s, 0.0)
        self.turns_count += 1

        self.history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "model": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": metrics.total_tokens,
            "cost_usd": metrics.cost_usd,
            "duration_s": duration_s,
        })

        return metrics

    def reset(self) -> None:
        """Reset all accumulated session metrics."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost_usd = 0.0
        self.total_duration_s = 0.0
        self.turns_count = 0
        self.history.clear()

    def render_summary_panel(self) -> Panel:
        """Render a clean, formatted Rich telemetry panel."""
        model_info = get_model_info(self.active_model)
        rate_str = f"${model_info.pricing.input_usd_per_mtok:.3f} / ${model_info.pricing.output_usd_per_mtok:.3f} per Mtok"

        table = Table.grid(padding=(0, 2))
        table.add_column("Key", style="bold cyan")
        table.add_column("Value", style="white")

        table.add_row("Active Model:", f"{self.active_model} [dim]({rate_str})[/dim]")
        table.add_row("Completed Turns:", f"{self.turns_count} turns [dim]({self.total_duration_s:.2f}s elapsed)[/dim]")
        table.add_row("Throughput:", f"{self.tokens_per_second:.1f} tokens/s")
        table.add_row("Input Tokens:", f"{self.total_input_tokens:,} tok")
        table.add_row("Output Tokens:", f"{self.total_output_tokens:,} tok")
        table.add_row("Total Tokens:", f"[bold green]{self.total_tokens:,} tok[/bold green]")
        table.add_row("Est. Spend:", f"[bold yellow]{format_cost_usd(self.total_cost_usd)} USD[/bold yellow]")

        return Panel(
            table,
            title="[bold blue]Token & Cost Telemetry[/bold blue]",
            border_style="cyan",
            expand=False,
        )
