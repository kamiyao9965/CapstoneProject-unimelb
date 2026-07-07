from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# IMPORTANT: These rates are NOT authoritative. Confirm current numbers against
# the official OpenAI pricing page before quoting costs to anyone:
#   https://openai.com/api/pricing
# Rates are USD per 1,000,000 tokens. Update the dict below when prices change,
# or override per run with --input-rate / --output-rate on the CLI.
# ---------------------------------------------------------------------------

MILLION = 1_000_000


@dataclass(frozen=True)
class ModelPrice:
    """USD per 1M tokens for a model."""

    input_per_million: float
    output_per_million: float
    verified: bool = False


# Seed values so the tool runs out of the box. `verified=False` means the number
# is a best-effort placeholder and MUST be checked before it is trusted.
PRICING: dict[str, ModelPrice] = {
    "gpt-5": ModelPrice(input_per_million=1.25, output_per_million=10.00, verified=False),
    "gpt-5-mini": ModelPrice(input_per_million=0.25, output_per_million=2.00, verified=False),
    "gpt-4o": ModelPrice(input_per_million=2.50, output_per_million=10.00, verified=False),
    "gpt-4o-mini": ModelPrice(input_per_million=0.15, output_per_million=0.60, verified=False),
}


def resolve_price(
    model: str,
    input_rate: float | None = None,
    output_rate: float | None = None,
) -> tuple[ModelPrice, bool]:
    """Return the price for a model plus a flag marking it as an estimate.

    Explicit --input-rate / --output-rate always win. Otherwise fall back to the
    PRICING table. The bool is True when the numbers used are unverified.
    """
    if input_rate is not None and output_rate is not None:
        return ModelPrice(input_rate, output_rate, verified=True), False

    known = PRICING.get(model)
    if known is None:
        raise KeyError(
            f"No pricing for model '{model}'. Add it to src/cost/pricing.py "
            "or pass --input-rate and --output-rate."
        )

    # Allow overriding just one side while keeping the table value for the other.
    price = ModelPrice(
        input_per_million=input_rate if input_rate is not None else known.input_per_million,
        output_per_million=output_rate if output_rate is not None else known.output_per_million,
        verified=known.verified and input_rate is None and output_rate is None,
    )
    return price, not price.verified


def cost_usd(input_tokens: int, output_tokens: int, price: ModelPrice) -> float:
    """Cost in USD for a given token split."""
    return (
        input_tokens / MILLION * price.input_per_million
        + output_tokens / MILLION * price.output_per_million
    )
