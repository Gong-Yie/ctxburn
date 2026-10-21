"""Per-million-token USD prices. Reference values, not gospel.

Override any of these with --set-price model=in,out,cached or a ctxburn.toml.
Rates move; if a number here is stale the report says so by flagging the model
as `estimated`.
"""

from __future__ import annotations

from dataclasses import dataclass

# model -> (input $/Mtok, output $/Mtok, cached-input $/Mtok)
TABLE: dict[str, tuple[float, float, float]] = {
    "gpt-4o": (2.50, 10.00, 1.25),
    "gpt-4o-mini": (0.15, 0.60, 0.075),
    "gpt-4.1": (2.00, 8.00, 0.50),
    "gpt-5": (1.25, 10.00, 0.125),
    "o3": (2.00, 8.00, 0.50),
    "claude-opus-4": (15.00, 75.00, 1.50),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-3.5": (0.80, 4.00, 0.08),
    "deepseek-chat": (0.27, 1.10, 0.07),
    "gemini-2.5-pro": (1.25, 10.00, 0.31),
    "qwen-max": (1.60, 6.40, 0.40),
}

FALLBACK = "gpt-4o"


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cached: float
    estimated: bool = False


def lookup(model: str) -> Price:
    """Longest-prefix match so 'gpt-4o-2024-08-06' finds 'gpt-4o'."""
    m = (model or "").lower()
    best: str | None = None
    for name in TABLE:
        if m == name or m.startswith(name):
            if best is None or len(name) > len(best):
                best = name
    if best:
        i, o, c = TABLE[best]
        return Price(i, o, c)
    i, o, c = TABLE[FALLBACK]
    return Price(i, o, c, estimated=True)


def parse_override(spec: str) -> tuple[str, Price]:
    """'gpt-4o=2.5,10,1.25' -> ('gpt-4o', Price(2.5, 10, 1.25))"""
    model, _, rest = spec.partition("=")
    parts = [p.strip() for p in rest.split(",")]
    if len(parts) != 3:
        raise ValueError(f"bad price override: {spec!r} (want model=in,out,cached)")
    return model.strip(), Price(*(float(p) for p in parts))
