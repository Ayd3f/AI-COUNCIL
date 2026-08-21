"""Token accounting and (configurable) cost estimation.

Prices are NOT hardcoded in application logic — they come from `pricing.json`
(path overridable with `PRICING_FILE`).  Any model without an entry is
reported as "pricing unknown" rather than silently costed at $0.
"""

from __future__ import annotations

from typing import Any

from ..models.schemas import CostLine, CostReport, TokenUsage
from .logging import get_logger

log = get_logger(__name__)


class PricingTable:
    """`{"models": {"<model-id>": {"input_per_1m": 5.0, "output_per_1m": 25.0}}}`"""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        raw = raw or {}
        models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
        self.currency: str = str(raw.get("currency", "USD"))
        self._models: dict[str, dict[str, float]] = {}
        for model_id, entry in (models or {}).items():
            if not isinstance(entry, dict):
                continue
            inp = entry.get("input_per_1m")
            out = entry.get("output_per_1m")
            if inp is None or out is None:
                continue  # explicitly "unknown" — leave it out
            try:
                self._models[str(model_id).lower()] = {
                    "input_per_1m": float(inp),
                    "output_per_1m": float(out),
                }
            except (TypeError, ValueError):
                continue

    def lookup(self, model: str) -> dict[str, float] | None:
        if not model:
            return None
        key = model.lower()
        if key in self._models:
            return self._models[key]
        # Prefix match so "gpt-4o-2024-11-20" resolves against a "gpt-4o" entry.
        best: tuple[int, dict[str, float]] | None = None
        for candidate, prices in self._models.items():
            if key.startswith(candidate) and (best is None or len(candidate) > best[0]):
                best = (len(candidate), prices)
        return best[1] if best else None

    def cost_for(self, model: str, usage: TokenUsage) -> float | None:
        prices = self.lookup(model)
        if prices is None:
            return None
        return (
            usage.input_tokens / 1_000_000 * prices["input_per_1m"]
            + usage.output_tokens / 1_000_000 * prices["output_per_1m"]
        )


def build_cost_report(
    per_agent: dict[str, tuple[str, TokenUsage]], table: PricingTable
) -> CostReport:
    """`per_agent` maps agent name -> (model id, aggregated usage)."""
    lines: list[CostLine] = []
    total_tokens = 0
    total_cost = 0.0
    unknown: list[str] = []

    for agent, (model, usage) in sorted(per_agent.items()):
        cost = table.cost_for(model, usage)
        if cost is None and model:
            unknown.append(model)
        lines.append(
            CostLine(
                agent=agent,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                estimated_cost_usd=round(cost, 6) if cost is not None else None,
                pricing_known=cost is not None,
            )
        )
        total_tokens += usage.total_tokens
        total_cost += cost or 0.0

    return CostReport(
        lines=lines,
        total_tokens=total_tokens,
        estimated_cost_usd=round(total_cost, 6),
        complete=not unknown,
        models_without_pricing=sorted(set(unknown)),
        currency=table.currency,
    )
